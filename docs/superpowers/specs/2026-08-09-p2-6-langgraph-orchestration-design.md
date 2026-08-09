# P2-6: LangGraph Orchestration

**Status:** design, approved
**Roadmap task:** P2-6
**Depends on:** P2-1, P2-2, P2-3 (all merged), P2-5 (kind cluster, `eb66ec6`)
**Model/effort:** Opus at xhigh per `docs/MODEL-EFFORT-GUIDE.md` line 67
("multi-service control and failure isolation")

---

## 1. What this task actually changes

The roadmap gate is three clauses:

> `POST /run` on the orchestrator fans out to all three agents over in-cluster
> service DNS, an integration test verifies inter-service communication, and a
> single agent failure does not abort the other two.

The starting state satisfies none of them in a load-bearing way.
`services/orchestrator/graph.py` is a sequential `for` loop over
`httpx.Client.post` whose docstring claims the agents run "in parallel". They
do not. `langgraph==0.2.60` is pinned in `services/orchestrator/requirements.txt`
and imported nowhere, so P2-5's healthy pods are not evidence that the pinned
LangGraph works: nothing ever loaded it.

This task makes the orchestrator a real compiled `StateGraph`, makes the
fan-out genuinely concurrent, closes two isolation holes the current code has,
and proves all of it with a socket-level integration test plus one live
in-cluster run.

**What this task does not do.** No retries on transient agent failures
(`tenacity` is available and this is deliberately deferred). No registry
logging from the orchestrator; that is P2-7. No conditional or dynamic
routing; v1 is a static fan-out and the graph edges are the seam where v2
routing lands.

---

## 2. The graph

```
START ──┬──> call_prior_auth ──┐
        ├──> call_care_gap   ──┼──> END
        └──> call_coding     ──┘
        one superstep, ainvoke runs all three concurrently
```

```python
class PipelineState(TypedDict):
    payload:    dict
    prior_auth: dict | None
    care_gap:   dict | None
    coding:     dict | None
    errors:     Annotated[dict[str, str], _merge_errors]
```

Three constraints on this shape, each verified by probe rather than assumed
(section 6):

**Node names are `call_<agent>`, distinct from the `<agent>` state keys they
write.** On `langgraph==0.2.60` this was mandatory: `add_node("prior_auth")`
against a state carrying a `prior_auth` key raises
`ValueError: 'prior_auth' is already being used as a state key`. On the
`1.2.10` being shipped that restriction is gone, and colliding names were
measured to behave correctly. The prefix is kept anyway, for two honest
reasons and not because the library forces it: a node is an action while a
state key is an artifact, and the code stays valid across both versions. Do
not treat this as a load-bearing constraint on 1.2.10; it is a naming
convention.

**`errors` requires a reducer.** Three nodes can write `errors` in the same
superstep. Without `Annotated[..., _merge_errors]` LangGraph raises
`InvalidUpdateError` and aborts **the entire graph**, which is precisely the
failure the gate's third clause forbids. The reducer is load-bearing, not
decoration, and the probe confirms the unreduced control case really does
raise rather than silently last-write-wins.

**The artifact keys need no reducer.** `prior_auth`, `care_gap`, and `coding`
each have exactly one writer, so a `LastValue` channel is correct for them.

Nodes come from one factory taking `(agent_name, url_getter, schema)`, so
there is one code path rather than three near-identical ones. The graph is
built and compiled once at import; URLs are resolved **per call**, not at
import, so the Kubernetes Service DNS names stay the production default while
a test can retarget them.

Each node opens its own `httpx.AsyncClient`. The three agents are three
different hosts, so a shared connection pool buys nothing, and independent
clients keep each node independently testable.

`services/orchestrator/app.py` `POST /run` becomes `async def` and awaits
`run_agents`.

---

## 3. The two isolation holes being closed

The gate's substance is "a single agent failure does not abort the other two".
Two ways to violate it exist today and no current test would catch either.

**Hole 1: concurrent `errors` writes.** Covered in section 2. Test: two agents
fail simultaneously, the third still returns its artifact and both failures
appear in `errors`.

**Hole 2: agent responses are validated too late.** Today each agent's raw
JSON is passed straight into `PipelineResult`, so pydantic validates at
*response-construction* time. One agent returning a schema-invalid `200`
therefore raises inside FastAPI and destroys **all three** artifacts.

Fix: each node validates its own response against its own contract
(`PriorAuthOutput`, `CareGapOutput`, `CodingOutput`) inside the node. A
`ValidationError` becomes an `errors` entry and leaves that one artifact
`None`. This is the same trust-boundary shape as P2-3's `ModelCodingPayload` /
`CodingOutput` split: the parse happens at the boundary the untrusted value
crosses, not later.

---

## 4. How a failure is recorded

The reason this needs specifying at all is not cosmetic. `str(httpx.ReadTimeout(""))`
is the empty string, confirmed against the shipped `httpx==0.28.1`, so the
current `str(exc)` writes `""` into the audit trail for exactly the failure
mode most likely to occur in a cluster. An error channel that records nothing
for a timeout is not an error channel. Note that the obvious fix,
`f"{type(exc).__name__}: {exc}"`, only improves it to `"ReadTimeout: "`.

One helper produces every entry, so no call site can invent its own format:

```python
def _describe(exc: Exception, url: str, timeout: float) -> str:
    name = type(exc).__name__
    detail = str(exc).strip()
    if isinstance(exc, httpx.HTTPStatusError):
        detail = (f"HTTP {exc.response.status_code} "
                  f"{exc.response.text[:200].strip()}").strip()
    elif isinstance(exc, httpx.TimeoutException) and not detail:
        detail = f"no response within {timeout}s"
    return f"{name} calling {url}: {detail}" if detail else f"{name} calling {url}"
```

Every entry therefore names the exception class and the URL that failed, which
is what makes a cluster DNS problem distinguishable from an agent bug. The
response body is truncated to 200 characters so a verbose agent traceback
cannot dominate the `PipelineResult`.

Failure classes each node must convert into an `errors` entry rather than
propagate: connection refused, DNS failure, read timeout, non-2xx status, and
schema-invalid 200.

### Timeout

New setting `agent_timeout_seconds: float = 60.0` in `shared/config.py`. The
value is unchanged from the current hard-coded 60; making it configurable is
what lets the timeout test run in a second instead of a minute.

60s is justified against measurement, not taste. The routed coding
configuration (opus-4-8 at high, per P2-4 artifact
`governance/eval_artifacts/coding_20260807T214249Z.json`) measured p50
10,768 ms, p95 15,517 ms, max 18,102 ms over 113 held-out notes. 60s is 3.9x
that p95 and 3.3x its measured maximum. Prior-auth latency is unmeasured;
this is recorded as a known gap, not papered over.

Concurrency changes the pipeline's worst case from 3 x 60s to ~60s, since the
slowest agent now bounds the run instead of the sum.

---

## 5. Dependency decision

`langgraph` moves from `0.2.60` to `1.2.10`, and the whole langgraph family is
pinned in both `requirements-dev.txt` (so CI exercises the real graph) and
`services/orchestrator/requirements.txt` (so the image is reproducible):

```
langgraph==1.2.10
langgraph-checkpoint==4.2.0
langgraph-prebuilt==1.1.0
langgraph-sdk==0.4.2
langchain-core==1.5.3
langchain-protocol==0.0.18
```

Rationale. The existing `0.2.60` pin left its transitives unpinned, so pip
resolves `langgraph-checkpoint==2.1.2`, a 2025 release against a Dec-2024
langgraph, a combination nobody upstream tests. It happens to work, but
"happens to work and floats on every rebuild" is not a dependency policy. The
1.2.10 family resolves against this repo's exact `pydantic==2.10.4`,
`fastapi==0.115.6`, and `httpx==0.28.1` with `pip check` reporting no broken
requirements, so the upgrade costs no ripple through `shared/schemas.py`. The
orchestrator image is rebuilt for this task's live verification regardless,
which is when the upgrade would have to be absorbed anyway.

`langgraph-prebuilt` and `langchain-protocol` are unused by this code; they
are transitives of `langgraph` and are pinned only so a rebuild is
deterministic.

---

## 6. Evidence already collected

Run before this spec was written, against both candidate pin sets, on Python
3.10.11 (`scratchpad/probe_langgraph.py`):

| Claim | 0.2.60 + checkpoint 2.1.2 | 1.2.10 family |
|---|---|---|
| Same-superstep nodes overlap under `ainvoke` | pass, 0.324s wall vs 0.90s sequential | pass, 0.334s |
| Reducer merges two simultaneous failures, survivor intact | pass | pass |
| Unreduced control raises `InvalidUpdateError` | pass | pass |
| Node name colliding with a state key | raises `ValueError` | allowed, and correct |
| Resolves against repo pins | n/a | pass, `pip check` clean |

Two things in this table were found by probing rather than predicted: the
node-name collision, and the fact that it stopped being an error between the
two versions. `str(httpx.ReadTimeout(""))` was likewise confirmed empty
against the shipped httpx, which is what motivates section 4.

---

## 7. Testing

### CI (`tests/test_orchestrator_integration.py`)

No API key, no database, no cluster. Three stub agent servers run on real
uvicorn sockets on ephemeral ports (`127.0.0.1:0`), returning real
`PriorAuthOutput` / `CareGapOutput` / `CodingOutput` objects. The orchestrator
is driven through `TestClient` and reaches the stubs over actual HTTP.

- **Happy path.** Three artifacts present, `errors == {}`, response validates
  as `PipelineResult`, and each stub asserts it received the `AgentInput` it
  was sent, so communication is verified in both directions.
- **Concurrency.** Stubs record their own start and end instants; the test
  asserts `max(starts) < min(ends)`, an interval-overlap proof rather than a
  wall-clock threshold, because a threshold is a flake waiting for a loaded CI
  runner.
- **Isolation, one case per failure class.** 502 status; connection refused
  (closed port); read timeout (short `agent_timeout_seconds`); schema-invalid
  200; and two agents down at once. Every case asserts the surviving agents
  still return artifacts and that `errors` names exactly the failed agents.

**What these tests are honest about.** The stubs are stubs. What is real is
the socket, the HTTP round trip, the JSON serialization, the pydantic
contracts on both ends, and the failure handling. What is not exercised is any
agent's own logic, which its own test file already covers.

### Unit (`tests/test_orchestrator_graph.py`)

Graph shape and reducer semantics without sockets: `_merge_errors` is
associative over the cases used, node names do not collide with state keys,
and the compiled graph exposes the expected nodes.

### Live, in-cluster (recorded as roadmap evidence)

Against the existing kind cluster from P2-5:

1. Seed an `encounters` row and a `notes` row in the in-cluster Postgres, so
   the care-gap agent's registry write satisfies its foreign keys. P2-5's
   smoke test died on exactly this constraint, correctly.
2. `kubectl port-forward svc/orchestrator`, POST a real SOAP note, and confirm
   three real artifacts return over Kubernetes Service DNS.
3. `kubectl delete pod -l app=agent-coding`, re-POST during the restart, and
   confirm the other two artifacts still return with `errors.coding` set.

Expected cost is roughly $0.05 of API for the two runs. Secret rotation for
this cluster must trim whitespace per the P2-5 note in `docs/ROADMAP.md`, or
outbound calls fail as an opaque connection error.

---

## 8. Files

| File | Change |
|---|---|
| `services/orchestrator/graph.py` | rewritten as a compiled `StateGraph` |
| `services/orchestrator/app.py` | `/run` becomes async |
| `shared/config.py` | add `agent_timeout_seconds` |
| `services/orchestrator/requirements.txt` | langgraph family pinned |
| `requirements-dev.txt` | langgraph family pinned, so CI runs the graph |
| `tests/test_orchestrator_integration.py` | new |
| `tests/test_orchestrator_graph.py` | new |
| `docs/ROADMAP.md` | P2-6 evidence entry |
| `docs/TECH-DESIGN.md` | correct the orchestrator section if it drifts |

---

## 9. Known gaps, stated rather than discovered later

1. **Prior-auth and care-gap latency are unmeasured.** The 60s timeout is
   justified only against the coding agent's measured distribution.
2. **The local venv is Python 3.10.11; CI and the image are 3.12.** A
   pre-existing mismatch, out of scope here, but it means CI is the authority
   on whether a test passes and the probe results above are 3.10 results.
3. **No retries.** A single transient blip costs that agent's artifact for
   that run. Acceptable while the pipeline is synchronous and re-runnable.
4. **Errors are strings, not structured.** Enough for an audit trail to say
   what broke; not enough to drive automated remediation. P2-7 may want more.
