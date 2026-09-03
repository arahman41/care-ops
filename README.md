# Care Ops Copilot

An end-to-end pipeline that turns a raw clinical encounter into a structured note, routes it through a multi-agent system that flags administrative and clinical follow-up actions, and governs every AI decision with an auditable drift-monitoring dashboard.

This is a portfolio and demonstration project. It uses public de-identified data only and is not a product being sold.

## Why this exists

The build closes four named skill areas in one coherent project: Kubernetes orchestration, MLOps drift detection, agentic orchestration, and LLM fine-tuning (fine-tuning is a marked stretch goal, not yet started). It sits at the intersection of ambient clinical documentation and AI governance, two of the fastest-growing healthcare AI hiring categories in 2026.

It builds on ClinAIQA, a pre-deployment LLM audit harness. Where ClinAIQA audited output before deployment, Care Ops Copilot monitors decisions continuously in production.

## Architecture

Three layers, all built and running, not diagrammed.

1. **Ambient intake.** Whisper transcribes audio, Claude structures the transcript into a SOAP note as versioned JSON. `services/intake`.
2. **Multi-agent routing.** A LangGraph graph fans a note out to three specialist agents (prior-auth, care-gap, coding and eligibility) concurrently, each returning a structured artifact with a confidence score, never free text. Each agent is its own containerized FastAPI service on Kubernetes; a single agent's failure does not abort the other two. `services/orchestrator`, `services/agent_*`.
3. **Governance and drift.** Every agent decision is logged to a Postgres model registry. A held-out labeled set periodically re-scores note-structuring accuracy, a paired bootstrap flags drift with a stated confidence interval (not a bare yes/no), and a React dashboard shows model inventory, accuracy trends, drift alerts, and an ONC HTI-1 style transparency report, all read live off the registry. `governance/`, `dashboard/`.

See `docs/PRD-CareOpsCopilot-MVP.md` and `docs/TECH-DESIGN.md` for detail, and `docs/ROADMAP.md` for what shipped when, with evidence for every claim below.

## Repository layout

```
care-ops-copilot/
  docs/            PRD, technical design, and the full phase-by-phase roadmap
  db/              Postgres schema (model registry)
  shared/          config, schemas, db, registry, Claude routing
  services/        intake, orchestrator, and the three agents (FastAPI)
  governance/      held-out evaluation, drift detection, transparency report
  dashboard/       React front end (Vite), reads the governance API live
  k8s/             Kubernetes manifests for the local cluster
  .github/         GitHub Actions CI
  tests/           contract, rules, drift, registry, and end-to-end tests
  scripts/         evaluation, benchmark, drift-check, and load-test runners
  data/            gitignored, never holds real PHI
```

## Datasets

Public and de-identified only. See `scripts/download_data.md`.

- **PriMock57** (primary, has audio): 57 mock primary care consultations with audio, transcripts, and clinician notes.
- **ACI-Bench** (scale, text): 207 dialogue and note pairs with expert-reviewed references, for note-structuring accuracy at larger N.

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # fill in ANTHROPIC_API_KEY
docker compose up --build     # brings up Postgres and all services
```

Run the suite:

```bash
make test
make cov
```

Local Kubernetes:

```bash
make cluster-up               # kind cluster plus kubectl apply -f k8s/
kubectl get pods -n care-ops  # 6/6 Running: db, intake, orchestrator, three agents
```

See `k8s/README.md` for image build and secret steps.

Dashboard (reads the live governance API, nothing hardcoded):

```bash
cd dashboard && npm install && npm run dev
# then, separately, run the orchestrator so the dev proxy has something to hit:
uvicorn services.orchestrator.app:app --port 8001
```

## Model routing

Routing is centralized in `shared/llm.py`. Note structuring and prior-auth use Sonnet 5 at high effort, care-gap is a deterministic rules engine with Haiku only for optional phrasing, coding uses **Opus 4.8 at high** (the P2-4 benchmark's routed configuration, a cost decision, not a demonstrated quality win, see below), and the transparency report uses Haiku for template fill. Stable prompt content is cached and offline re-scoring runs through the Batch API.

## Hard rules

- No real patient data, ever.
- No notebooks in the repo.
- No em dashes anywhere, in code, comments, or docs.
- The held-out evaluation set is leak-free and never tunes rules or prompts.
- Report measured metrics only, and name exactly what was measured. "Coding accuracy" is never claimed; see the coding section below for why.

## Measured results

Every number below is reproduced by the command next to it. `docs/ROADMAP.md` carries the full evidence trail (live runs, exact artifact filenames, `eval_runs` row numbers) for each one.

### Note structuring, ACI-Bench held-out (n = 120)

| metric | value |
|---|---|
| **F1 (headline)** | **0.869** |
| recall (captured and correctly placed) | 0.786 |
| precision (grounded in the transcript) | 0.971 |
| section-placement accuracy | 0.880 |
| hallucination rate | 0.029 |

Sonnet 5 at high effort, scored by a pinned Haiku 4.5 judge at temperature 0.
Produced by `scripts/run_structuring_eval.py` (`make eval-structuring`), against
the frozen held-out split, whose digest the harness re-verifies before it
scores anything.

The metric is deliberately asymmetric, and the asymmetry is the first thing to
challenge: **recall is scored against the clinician note** (the gold for what
matters) and **precision against the transcript** (the gold for what is true),
because a clinician note is a selective summary, so writing something it omits
is a legitimate inclusion, while writing something the transcript does not
support is a hallucination.

Read honestly:
- 51 of the 120 reference notes fuse `ASSESSMENT AND PLAN`, so a fact from
  those may sit in either section and still count as placed. On the 69 notes
  that separate them, strict F1 is **0.869** and strict placement is 0.879, so
  the leniency is not what is holding the number up.
- A hand audit of 30 randomly sampled judge verdicts agrees with the judge
  **29 / 30 (96.7%)**. The single miss inflates recall, so read recall as a mild
  upper bound. See `docs/HELD-OUT-POLICY.md` for the full audit.

Reproduce the number offline, from the committed verdicts, with zero API calls:

```
make eval-structuring-replay
```

The committed artifact carries per-fact verdicts and no clinical text, and CI
replays it on every run, so the published number is regression-tested rather
than merely remembered.

### End to end from audio, PriMock57 held-out (n = 7)

The full pipeline with nothing stubbed: two speaker wavs in, Whisper (`base`)
on each track, merged into dialogue by timestamp, then the same structuring
prompt and the same fact-level judging. `make eval-structuring-primock`.

| metric | value |
|---|---|
| **highlights recall** | **0.897** (26 / 29 human-authored key concepts) |
| precision (grounded in transcript) | 0.967 |
| hallucination rate | 0.033 |
| section-placement accuracy | **not scored** (see below) |

**These numbers are not comparable to the ACI-Bench headline above, and the
direction of the gap is the opposite of what it looks like.** PriMock57's F1
computes to 0.899, which is *higher* than the 0.869 headline. That does not
mean the audio path outperforms the text path. It means the two recalls measure
different things: ACI-Bench recall demands a fact be captured **and filed in the
right SOAP section**, while PriMock57 recall demands only that it be **captured**,
because unsectioned GP notes carry no ground truth for placement. Comparing the
one thing that *is* common, the raw capture rate:

| | capture rate |
|---|---|
| ACI-Bench (clean human transcripts) | 5850 / 6550 = **0.893** |
| PriMock57 (Whisper from audio) | 215 / 256 = **0.840** |

So the audio path is measurably **worse**, by about five points of capture, which
is what you would expect once ASR error enters the pipeline. Whisper `base`
mishears clinical terms ("wheezy" comes back as "weezy") and its coarse segment
boundaries sometimes land an answer a beat before its question. That degradation
is inside the measurement on purpose: this is the number for the system as it
actually runs, not for the system given a perfect transcript.

**Placement is reported as NULL, not as a number.** PriMock57's reference notes
are free-text GP shorthand with no section headers, so every SOAP bucket is
acceptable for every fact and placement accuracy computes to a perfect 1.0 by
construction: not because the model filed anything correctly, but because
nothing *could* be filed wrongly. `eval_runs.accuracy` is written NULL and the
replay declines to print it. A 1.0 there would be the most flattering number on
the board and would mean nothing at all.

### Multi-agent orchestration on Kubernetes

Three agents plus the orchestrator, each an independent Deployment and
Service (`k8s/`). `make cluster-up`, then live-verified: `kubectl get pods
-n care-ops` shows 6/6 `Running` (Postgres, intake, orchestrator, three
agents), each with a real `readinessProbe`, and the orchestrator reaches
every agent by Kubernetes Service DNS.

Concurrency is proved by more than a claim: three agents summed **9,652 ms**
of individual work in one real run (care-gap 1 ms, prior-auth 4,284 ms,
coding 5,367 ms), yet the pipeline request returned in **5,661 ms**, just
over the slowest agent, which is arithmetically impossible if the graph ran
them sequentially. Isolation is proved the same way: scaling the coding
agent's Deployment to zero replicas mid-run still returned prior-auth and
care-gap's real artifacts in the same request, with `errors.coding` naming
the exact failed URL rather than a generic message.

### Coding and eligibility: a routing decision, not an accuracy claim

**No held-out set carries gold ICD-10 or CPT codes.** Neither ACI-Bench nor
PriMock57 labels billing codes, so there is nothing to compute the precision
or recall of a *correct* code against. What this project measures instead is
a **verified rate**: whether a suggested code exists in the vendored CMS
release. A model can score a perfect verified rate while suggesting codes
that are clinically wrong for the note. This project never calls that number
"coding accuracy."

`make coding-benchmark-replay` recomputes the routing benchmark from the
committed artifact, zero API calls:

| arm | configuration | verified rate | cost (113 notes, corrected pricing) |
|---|---|---|---|
| A | Sonnet 5 at xhigh | 96.65 | $4.01 |
| B | Opus 4.8 at high | 97.35 | $3.16 |

The paired quality delta (0.70 points, 95% BCa CI [-0.73, 2.22]) straddles
zero, so this is **not a demonstrated quality win** for either model. The
routing decision was made on **cost**: Opus 4.8 at high is cheaper for
equivalent verified-rate performance, by a corrected margin of $0.84 over
113 notes. That decision is recorded in `shared/llm.py`.

### Drift detection

`governance/drift.py` compares two measurement windows with a **paired**
bootstrap (both windows score the same held-out encounters, so a two-sample
test would discard real information), and returns a confidence interval and
a minimum detectable effect, not a boolean. Sensitivity was measured on a
controlled injected accuracy drop, not assumed: as little as **3 flipped
facts out of 5,875** (0.05%) was enough to cross into a DRIFT verdict at
2,000 bootstrap replicates. `python scripts/run_drift_check.py --reference-run
<id> --current-run <id>` reproduces any comparison from committed artifacts,
zero API calls.

On the two real windows this project has today, the honest verdict is
`NOT_ATTRIBUTABLE`: the delta (+0.0052 F1) is measured, but the two windows
differ on `max_tokens`, the generation-sampling baseline is unmeasured, and
the reference-fact decomposer itself produced a slightly different count
across windows from byte-identical source notes. A detector that called this
"drift" or "no drift" instead of naming those three reasons would be
publishing a claim the data cannot support. This is what the live
transparency report says too, verbatim, in its "Ongoing maintenance"
category for `note_structuring`.

### Governance dashboard

Three read endpoints (`/governance/inventory`, `/governance/accuracy-trend`,
`/governance/transparency-report`, all on the orchestrator, `governance/api.py`
and `governance/transparency.py`) back a React dashboard (`dashboard/`) that
renders model inventory, a per-agent-and-dataset accuracy trend chart, active
drift alerts, and the full ONC HTI-1 style transparency report, all live off
the registry, nothing hardcoded. Verified by actually loading the page: two
real ACI-Bench windows draw one real trend line, PriMock57's single n=7
measurement renders as an unconnected point rather than a fabricated
continuation of a different dataset's line, and the coding agent's real P2-4
benchmark numbers render in its transparency card.

### End-to-end pipeline test

`tests/test_e2e_pipeline.py` drives a transcript through the real intake
service (real DB write), the real orchestrator (real HTTP fan-out), and the
three real agent services, including care-gap's real rules engine, then
reads the resulting rows back through the real `/encounters/{id}/decisions`
endpoint and asserts all three agents logged a decision. Only the two calls
that would otherwise spend real money and add nondeterminism (structuring's
and two agents' underlying Claude calls) are mocked; parsing, schema
validation, vocabulary classification, and the registry write all run for
real.

### Load test and latency

```
make load-test
```

runs `scripts/run_load_test.py`, which starts the intake service with
structuring stubbed (`shared/config.py::fake_structuring`, so the numbers
below measure this service's own concurrency handling, not the Anthropic
API's response-time variance), drives it with Locust, parses Locust's own
CSV for every number, and writes a committed artifact under
`governance/eval_artifacts/`.

| users | spawn-rate | duration | requests | failures | req/s | p50 | p95 | p99 |
|---|---|---|---|---|---|---|---|---|
| 20 | 5 | 1m | 851 | 0 | 14.25 | 110ms | 240ms | 370ms |

Artifact: `governance/eval_artifacts/load_test_20260903T173359Z.json`. The
audit below pins these figures to that exact run, so a later load test
produces a new artifact rather than silently redefining what this table means.

This is load-path latency for the FastAPI service, real Pydantic validation,
and a real Postgres write on every request. It is explicitly **not** an
end-to-end number including a live Claude generation call, and should never
be quoted as one.

### Test suite

**447 passed**, ruff clean, **96% line coverage** (`shared`, `services`,
`governance`, measured against a live Postgres, matching CI's own
`postgres:16` service). `make test` / `make cov`.

### Every number above, audited

```
make audit          # regenerate every published number, no infrastructure needed
make audit-full     # also re-run the suite, closing the two claims that need Postgres
```

`governance/audit.py` carries a manifest of every headline number this
project publishes, together with what backs each one. `scripts/audit_metrics.py`
regenerates them from committed evidence and **fails** if a published value
cannot be reproduced. It runs in CI on every push, so a stale README is a
red build rather than something a reader discovers.

Nothing is taken on trust, and nothing that could not be checked is reported
as if it had been. Each claim is backed as strongly as its evidence allows:

| tier | what it proves | example |
|---|---|---|
| recomputed | derived from primitives in a committed artifact, so a corrupted stored aggregate is caught too | the 0.869 F1, recomputed from per-fact verdicts |
| artifact | read from a committed artifact's stored field; catches a stale README, not a corrupted artifact | the load-test p95 |
| environment | needs a live cluster or database; checked when available, reported as skipped when not | 6/6 pods, the test count |
| observed | a recorded live run or human audit, not re-derivable on demand; cross-checked against the document that records it | the 29 / 30 judge audit |

Of the 48 published claims, 40 are regenerated with no infrastructure at
all, 3 more need a live Postgres or cluster (`make audit-full`), and 5 are
recorded observations that cannot be re-derived on demand and are instead
cross-checked against the document that records them. The counts a given run
prints depend on what infrastructure is present, which is why a skipped
claim is reported as skipped and never as passed.

The audit has already earned its place: it caught `governance/pricing.json`
describing the coding cost margin as $0.85, which is the two rounded figures
subtracted, where the exact costs subtracted and then rounded give the $0.84
stated everywhere else.

## Demo

A recorded video is not yet published. `docs/DEMO-SCRIPT.md` is the
scene-by-scene script for it, mirroring the ClinAIQA launch pattern: live
commands, real numbers, and the honest edge cases (drift's NOT_ATTRIBUTABLE
verdict, the coding agent's verified-rate-not-accuracy framing) shown rather
than cut.

## Where to start

Read `SETUP.md` for environment setup and the first Claude Code prompt, then follow `docs/ROADMAP.md` for the full Phase 0 through Phase 5 plan, task by task, with evidence for every claim on this page. `AGENTS.md` and `CLAUDE.md` give AI coding agents the rules and commands.

## Status

Phases 0 through 3 are complete (setup, ambient intake, the multi-agent Kubernetes layer, governance and drift). Phase 4 is in progress: the dashboard, the end-to-end test, and the load test above are done; documentation and the metric audit are the remaining items. See `docs/ROADMAP.md` for the exact task-by-task state and every phase's exit-gate evidence.
