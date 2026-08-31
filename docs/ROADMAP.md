# Care Ops Copilot: Full Roadmap

The complete build, Phase 0 through Phase 5, in one file. Each phase has a goal, tasks with verifiable acceptance criteria, dependencies, an exit gate, and the honest metric it unlocks. Phases 0 and 1 also have a granular issue-ready version in docs/PHASE-0-1-TASKS.md.

Ground rules that hold across every phase: no real patient data, no notebooks in the repo, no em dashes anywhere, the held-out set is leak-free and never tunes anything, and every reported number is measured and reproducible from a committed script.

Phase exit gates are hard stops, enforced in AGENTS.md and CLAUDE.md. Do not advance to the next phase until the current exit gate is met and verified with evidence (command output, a passing test, or an eval_runs row), and the user confirms.

Rough total: 7 to 9 weeks part-time for Phases 0 through 4, with Phase 5 as optional stretch.

---

## Phase 0: Setup (~1 week)

**Goal:** A clean repo, a running local database, a local cluster shell, the datasets in place, and a locked held-out split.

- **P0-1 Repo scaffold and tooling.** Done when `make dev-install` succeeds, `make lint` is clean, and `pytest` collects with zero errors.
- **P0-2 Postgres schema.** Done when `make db-init` completes and all five tables exist. The confidence CHECK rejects a value of 1.5.
- **P0-3 Local Kubernetes.** Done when `kubectl get pods -n care-ops` shows Postgres running and the `db` Service resolves in-cluster.
- **P0-4 Dataset acquisition.** Done when PriMock57 and ACI-Bench are present locally, nothing under `data/` is tracked by git, and download steps are documented.
- **P0-5 Held-out set definition.** Done when a committed script deterministically reproduces a leak-free split documented as tuning-forbidden.

**Exit gate:** green CI on an empty feature set, database and cluster reachable, held-out split locked.
**Metric unlocked:** none yet. This phase exists so later metrics are trustworthy.

---

## Phase 1: Ambient Intake (~1 to 2 weeks)

**Goal:** Audio or transcript in, structured SOAP note out, persisted and scored.

- **P1-1 Whisper transcription.** Done when a PriMock57 audio file yields a non-empty transcript and model size is configurable.
- **P1-2 SOAP structuring.** Done when a transcript yields a valid four-section SoapNote, malformed output raises a clear parse error, and the prompt forbids invented findings.
- **P1-3 Intake service and persistence.** Done when `POST /intake` returns 200 and writes one encounter and one note, empty input returns 422, and `GET /health` returns ok.
- **P1-4 Note-structuring accuracy harness.** Done when the harness scores structured output against held-out reference notes, prints a measured number, and writes an `eval_runs` row.
- **P1-5 Intake test suite.** Done when schema and endpoint tests pass and intake coverage is reported in CI.
- **P1-6 CI green on main.** Done when a pull request shows a green run including lint and coverage.

**Exit gate:** one PriMock57 encounter runs end to end and produces a measured structuring accuracy on the held-out set.
**Metric unlocked:** note-structuring accuracy (F1 or exact-field-match).

---

## Phase 2: Multi-Agent Layer (~2 weeks)

**Goal:** Three agents running as independent Kubernetes services, wired through a LangGraph graph, each logging structured decisions.

- **P2-1 Prior-Auth Agent.** Build the agent and its endpoint. Done when a SOAP note yields a valid PriorAuthOutput with confidence in [0, 1], and a note with no prior-auth items returns an empty list rather than free text.
- **P2-2 Care Gap Agent with a real rule set.** Replace the four placeholder rules with citable screening and follow-up guidelines. Done when each rule maps to a documented guideline source and the rules engine has unit tests for every rule firing and not firing.
- **P2-3 Coding and Eligibility Agent.** Done when a SOAP note yields a valid CodingOutput, codes are presented as suggestions for human review, and eligibility flags are structured booleans.
- **P2-4 Coding model routing benchmark.** Run Sonnet 5 at xhigh against Opus 4.8 at high on the held-out set. **This does not measure coding accuracy, and must never be described as if it does.** Neither ACI-Bench nor PriMock57 carries gold billing codes (`heldout_manifest.csv` is `dataset,encounter_id,split`; ACI-Bench is `dataset,encounter_id,dialogue,note`), so there is nothing to compute precision or recall of *correct* codes against. A model can score a perfect verified rate while suggesting codes that are clinically wrong for the note. What is measured: pooled verified rate (`shared/vocab.py::verified_rate`), `unchecked` share, inter-model agreement, and cost and latency. Done when the verified rate's floor is measured and stated FIRST (per the P2-3 spec section 1a: causes 2, 3, and 4 give it a nonzero floor unrelated to hallucination, so without the floor a small gap between two models is uninterpretable), both models' results are written to `eval_runs`, and the winner is recorded in `shared/llm.py` with a one-line note on why.

  **DONE 2026-08-07.** Artifact `governance/eval_artifacts/coding_20260807T214249Z.json`,
  `eval_runs` rows 3 and 4, replay verified. 240 live calls, $9.17, analysis
  set 113 of 120 (above the 108 floor, so not void; attrition mildly
  length-biased, dropped median 2,908 chars against retained 2,670).

  | arm | configuration | verified rate | not-found | unchecked | codes/note | cost |
  |---|---|---|---|---|---|---|
  | A | claude-sonnet-5 at xhigh | 96.65 | 3.35 | 37.05 | 7.14 | $6.01 |
  | B | claude-opus-4-8 at high | 97.35 | 2.65 | 37.08 | 8.50 | $3.16 |

  **Cost correction, 2026-08-31 (found during P3-2).** Those two figures were
  computed against a `governance/pricing.json` entry of $3/$15 for Sonnet 5,
  a rate that never took effect: $2/$10 became the standard price and the
  scheduled September increase was cancelled. Recomputed from this run's own
  token counts, arm A is **$4.01**, not $6.01; arm B is unchanged at $3.16.
  Opus 4.8 at high stays the cheaper configuration, so **the routing decision
  below does not change**, but the margin is $0.84 rather than $2.85. The
  figures in this table are left as reported, because they are what the run
  measured under the table of the day.

  Paired delta nf(A)-nf(B) = 0.70 points, 95% BCa CI [-0.73, 2.22], seed
  20260722, 10,000 replicates. Branch **inconclusive** (guard
  `floor_divergence`), so the rule routed on **cost** to
  `("claude-opus-4-8", "high")`, now set in `shared/llm.py`.

  **The floor was measured and stated first, as this entry requires.**
  `vocab_floor_version = "none"`: every not-found code in both arms is cause 1
  (fabricated). Nothing landed in the degenerate, CPT-shaped, or absent-from-pin
  buckets, so the FY2025 cause-2 vendoring would have changed nothing and was
  not done. Both floor bands are therefore [0, not-found rate].

  **Two caveats to carry forward.** (1) The floor-divergence guard could not
  have passed on this data: with cause 2, 3, and 4 all zero, both bands start
  at zero and `max(nf_A, nf_B)` is always at least `|nf_A - nf_B|`. Removing
  the guard does **not** change the branch, because the CI reaches inconclusive
  on its own. Any future re-run should either attribute real mass to causes 2
  to 4 or re-pre-register the guard, before seeing data. (2) The quality
  comparison is unresolved, not settled: the CI straddles zero. This is a cost
  decision.

  **Tooling debt found during this run:** `make db-init` expands
  `$(POSTGRES_USER)`/`$(POSTGRES_DB)` to empty because make never loads `.env`,
  and the Make targets call bare `python` rather than the venv interpreter.
  Both fixed 2026-08-07.

  **Storage contract (forward-looking, added 2026-07-22).** P2-4 writes coding
  `eval_runs` rows with `accuracy`, `f1`, `precision`, and `recall` all NULL,
  and the verified rate in the `metrics` JSONB column. Any Phase 3 consumer
  (P3-1 runner, P3-2 windows, P3-5 API, P4-1 dashboard) MUST tolerate an
  all-NULL accuracy family and read coding numbers from `metrics`. As of
  2026-07-22 there are no `eval_runs` SELECT readers anywhere in the repo, so
  this is a contract on code not yet written, not a migration of existing code.

- **P2-5 Containerize and deploy.** Done when each agent has its own image and Kubernetes Deployment plus Service, and `kubectl get pods -n care-ops` shows all three agents plus the orchestrator running with passing readiness probes.

  **DONE 2026-08-07.** All 6 pods (postgres, intake, orchestrator, and the 3
  agents) `1/1 Running`, 0 restarts, stable over 2+ minutes. `readinessProbe`
  confirmed genuinely configured (not absent-default) via `kubectl describe`,
  and cross-service Kubernetes DNS confirmed working: the orchestrator pod
  reached `agent-coding` by Service name and got a real `/health` response.
  `db/schema.sql` applied to the in-cluster Postgres for the first time (5
  tables created clean, no "already exists").

  **Real bug found and fixed along the way, not a deployment artifact:** local
  `.env` has `ANTHROPIC_API_KEY= sk-ant-...` with a stray space after `=`.
  `python-dotenv`/pydantic-settings silently strips that whitespace, which is
  why every local run all through P2-4 worked fine. `kubectl create secret
  --from-env-file` does **not** strip it, so the literal leading space landed
  in the secret and every outbound call from the coding agent pod died with
  `httpx.LocalProtocolError: Illegal header value b' sk-ant-...'` surfaced as
  an opaque `anthropic.APIConnectionError` two layers up. Fixed by trimming at
  the point the secret's temp env file is built, not by editing `.env`
  (`.venv` and Docker Compose both tolerate the space; only kubectl's env-file
  loader doesn't). **Any future secret rotation for this cluster must trim
  whitespace the same way, or the failure returns silently** as a connection
  error that looks like a network problem, not a header problem.

  A synthetic end-to-end smoke test (fake `encounter_id: 1`) then reached a
  real model call and a real `CodingOutput` parse before failing on a
  legitimate `agent_decisions` foreign-key constraint, since no such encounter
  exists yet. That is correct behavior, not a P2-5 gate blocker, and is
  P2-6/P2-7's job to exercise with real data.
- **P2-6 LangGraph orchestration.** Done when `POST /run` on the orchestrator fans out to all three agents over in-cluster service DNS, an integration test verifies inter-service communication, and a single agent failure does not abort the other two.

  **DONE 2026-08-09.** Spec
  `docs/superpowers/specs/2026-08-09-p2-6-langgraph-orchestration-design.md`,
  plan `docs/superpowers/plans/2026-08-09-p2-6-langgraph-orchestration.md`.
  319 passed / 1 xfailed, ruff clean, `services/orchestrator` at 100% line
  coverage.

  Before this task the orchestrator was a sequential `for` loop over
  `httpx.Client.post` whose docstring claimed "in parallel", and
  `langgraph==0.2.60` was pinned but imported nowhere, so P2-5's healthy pods
  were never evidence the dependency worked. It is now a compiled
  `StateGraph`: `START` fans out to three `call_<agent>` nodes in one
  superstep and joins at `END`.

  | Gate clause | Evidence |
  |---|---|
  | fans out to all three agents | live run below, and `test_all_three_agents_answer_over_real_http` |
  | over in-cluster service DNS | live run below, `errors` names `http://agent-coding:8000/run` |
  | integration test verifies inter-service communication | 3 stub agents on real uvicorn sockets, artifacts out and `AgentInput` confirmed received |
  | a single agent failure does not abort the other two | 5 failure classes in pytest, plus the live injected failure below |

  **Live in-cluster run** (encounter 1, note 1, seeded first because
  `agent_decisions` has FKs to both `encounters` and `notes`):

  - Healthy: `HTTP 200 in 5.66s`, `errors {}`, all three artifacts. prior-auth
    `MRI lumbar spine`; care-gap `A1C_MONITORING`; coding `E11.9` and `M54.16`
    verified, CPT `72148` and `83036` `unchecked`.
  - `kubectl scale deployment/agent-coding --replicas=0`: `HTTP 200 in 3.70s`,
    prior-auth and care-gap still returned, `coding` null, and
    `errors.coding = "ConnectError calling http://agent-coding:8000/run: All
    connection attempts failed"`. The URL in that string is the point: without
    it a cluster DNS fault is indistinguishable from an agent bug.
  - Restored to 1 replica: `HTTP 200 in 5.74s`, `errors {}`, coding recovered.

  **Concurrency is proved twice, by independent means.** In pytest by interval
  overlap (`max(starts) < min(ends)`), which is a real overlap proof rather
  than a flake-prone wall-clock threshold. In the cluster by the registry's own
  latency column: run 1's agents summed to 9,652 ms of work (care-gap 1,
  prior-auth 4,284, coding 5,367) but the request returned in 5,661 ms, just
  over the slowest agent. Sequential execution was arithmetically impossible.

  **Two real bugs fixed, not just a refactor.**
  1. Agent responses were validated at `PipelineResult` construction, so one
     agent's schema-invalid `200` raised inside FastAPI and destroyed all three
     artifacts. Each node now validates against its own contract, in the node.
  2. Failures were recorded as `str(exc)`, and `str(httpx.ReadTimeout(""))` is
     the empty string, so the single most likely cluster failure wrote nothing
     to the audit trail. One `_describe` helper now always names the exception
     class and the failing URL.

  The `errors` channel carries a reducer because three nodes can write it in
  one superstep. This was mutation-checked: with the reducer removed, the
  two-agents-down test fails with `InvalidUpdateError: At key 'errors': Can
  receive only one value per step`, so the test detects the reducer rather than
  passing either way.

  **langgraph upgraded 0.2.60 to 1.2.10, whole family pinned** in both
  `requirements-dev.txt` and `services/orchestrator/requirements.txt`. The old
  pin left transitives floating onto `langgraph-checkpoint 2.1.2`, a 2025
  release against a Dec-2024 langgraph. Verified on Python 3.12 inside the
  rebuilt image, not only on the 3.10 local venv.

  **Found along the way, and NOT a P2-6 deliverable:**
  - **The in-cluster Postgres had no PersistentVolumeClaim.** `k8s/postgres.yaml`
    declared no volume at all, so the container restart when Docker started
    wiped all five tables created in P2-5. `db/schema.sql` had to be reapplied
    before this run. **P2-7 stores the audit trail and Phase 3 needs two
    windows of data over time; both are impossible on an ephemeral database.**

    **FIXED 2026-08-09, before starting P2-7.** A 5Gi `ReadWriteOnce` PVC on
    kind's default `standard` (`rancher.io/local-path`) StorageClass, bound and
    mounted at `/var/lib/postgresql/data`. Three details that are not
    decoration: the Deployment strategy is `Recreate`, because the default
    `RollingUpdate` deadlocks on a RWO volume when the new pod waits for a
    volume the old pod will not release; `PGDATA` points at a `pgdata`
    subdirectory of the mount, because `initdb` refuses a non-empty directory
    and a volume root can carry `lost+found`; and Postgres finally has
    `pg_isready` readiness and liveness probes, without which the pod reports
    Ready before it accepts connections and anything run straight after
    `kubectl rollout status`, schema application included, fails
    intermittently.

    Verified by destruction, not by inspection: a sentinel row was inserted,
    the pod deleted outright (`postgres-845d954dbb-xlhg7`), and after the
    replacement came up (`...-wk4d8`) both the row and all five tables were
    still there. The sentinel was then removed, leaving the database clean for
    P2-7.
  - **No `.dockerignore` existed**, so every `docker build` shipped a 4.9 GB
    context to the daemon, including 3.9 GB of PriMock57 clinical audio that no
    Dockerfile copies. Added one that keeps only `data/vocab/` (200 KB), which
    is the sole `data/` path any image needs. Build now takes 44s.
- **P2-7 Registry logging for every agent.** Done when every agent call writes a row to `agent_decisions` with input, output, confidence, model, effort, and latency, and a query by encounter id returns every decision.

  **DONE 2026-08-10.** Spec
  `docs/superpowers/specs/2026-08-09-p2-7-registry-logging-design.md`, plan
  `docs/superpowers/plans/2026-08-09-p2-7-registry-logging.md`. 325 passed /
  1 xfailed, ruff clean.

  The write side was already correct going in: all three agents were already
  calling `log_decision` with real models, confidences, and latencies,
  confirmed during P2-6's live verification. The read side was not: nothing
  called `shared/registry.py::decisions_for_encounter`, and when checked its
  `SELECT` turned out to return only 5 of the 8 stored columns, silently
  dropping `model_effort`, `input_ref`, and `latency_ms`. Caught by
  `tests/test_registry.py` against a real Postgres (a mocked connection
  cannot catch a dropped `SELECT` column), then fixed and exposed as
  `GET /encounters/{id}/decisions` on the orchestrator.

  | Gate clause | Evidence |
  |---|---|
  | every agent call writes input, output, confidence, model, effort, latency | already true (P2-1/P2-2/P2-3); reconfirmed live below |
  | a query by encounter id returns every decision | the `SELECT` fix, `test_registry.py`, and the live call below |

  **Live in-cluster run**, orchestrator image rebuilt to include the new
  endpoint (encounter 26, note 25, seeded fresh since the prior encounter was
  cleaned up during the PVC fix's own verification):

  - `POST /run`: `HTTP 200 in 6.95s`, `errors {}`, all three artifacts.
  - `GET /encounters/26/decisions`: `HTTP 200`, exactly 3 rows, one per
    agent. `care_gap` reads back `model="rules-v1"`, `model_effort=null`,
    `latency_ms=20`; `prior_auth` reads back `model="claude-sonnet-5"`,
    `model_effort="high"`, `latency_ms=3947`; `coding` reads back
    `model="claude-opus-4-8"`, `model_effort="high"`, `latency_ms=6472`.
    Every row's `input_ref` carried the full SOAP note and `output` carried
    that agent's real structured artifact.
  - `GET /encounters/999999/decisions`: `HTTP 200`, `[]`. The documented
    simplification (no existence check against `encounters`) confirmed live,
    not only in pytest.

  **One process note, not a code finding.** Merging P2-6's PR with
  `--delete-branch` deleted `p2-6-langgraph-orchestration`, which was the
  base branch of the still-open Postgres PVC fix PR. GitHub auto-closed that
  PR rather than merging it, and its commit did not land on `main`. GitHub
  also refuses to reopen a PR whose base branch no longer exists and refuses
  to retarget a closed PR's base, so the fix was to open a fresh PR
  (`#7`, same commit `d7faa5e`) targeting `main` directly. **Do not
  `--delete-branch` on a PR that is any other open PR's base**, or repeat
  this recovery.

**Exit gate:** a note submitted to the orchestrator returns all three structured artifacts, each logged, with the pipeline surviving a single injected agent failure.

**MET 2026-08-10.** P2-6 proved the fan-out and the injected-failure
survival (`kubectl scale deployment/agent-coding --replicas=0`, the other
two agents still returned, `errors.coding` set, then full recovery). P2-7
proved every artifact is logged and independently queryable by encounter id,
live, in the same cluster. Phase 2 is complete.
**Metric unlocked:** Kubernetes service count, and the coding model routing decision backed by verified rate, `unchecked` share, agreement, and cost and latency.

Not "per-agent decision accuracy". Accuracy is only claimable where a labeled reference set exists, and for the coding agent none does (see P2-4). The care-gap agent's rules are deterministic and unit-tested, which is a correctness property rather than a measured accuracy. Before claiming an accuracy number for any agent, name the labeled set it was measured against.

---

## Phase 3: Governance and Drift (~1 to 2 weeks)

**Goal:** Continuous evaluation, drift detection, and an auto-generated transparency report.

- **P3-1 Evaluation runner.** Done when `governance/evaluate.py` scores an agent against the held-out set for a named window and writes accuracy, F1, precision, and recall to `eval_runs`.

  **DONE 2026-08-31.** Spec
  `docs/superpowers/specs/2026-08-27-p3-1-evaluation-runner-design.md`, plan
  `docs/superpowers/plans/2026-08-31-p3-1-evaluation-runner.md`. 368 passed /
  1 xfailed (from 325), ruff clean.

  | Gate clause | Evidence |
  |---|---|
  | scores an agent against the held-out set | `score_artifact` replays a committed artifact through `structuring_eval.replay`, which recomputes from the per-fact verdicts; ACI reproduces `f1 == 0.8685633622463043` exactly |
  | for a named window | `--window`, carried onto the row; `GenerationConfig` defines what "window" is allowed to mean |
  | writes accuracy, F1, precision, recall to `eval_runs` | rows 7 and 8 below, plus three `needs_db` round-trip tests |

  **Read literally, the gate cannot be honored, and the refusal is the
  deliverable.** Accuracy, F1, precision and recall require labels, and only
  `note_structuring` has them. `coding` has no gold billing codes (P2-4),
  `care_gap`'s rules are deterministic and unit-tested (a correctness
  property, not an accuracy), and no held-out encounter carries a labeled
  prior-auth determination. P3-1 scores the one agent that has a labeled set
  and refuses the other three by name, with the reason carried into the
  exception.

  That refusal is now a guard rather than prose. The invariant, **no agent
  outside the scoreable registry may ever be written a non-NULL accuracy, f1,
  precision or recall**, previously existed only in this file, a schema
  comment, and a docstring, and `coding_row_params`' four literal `None`s were
  a convention that nothing enforced. Nothing prevented a later P3-5 endpoint
  or P4-1 dashboard from writing a verified rate into `accuracy`, where it
  would be indistinguishable from a real number on a chart.
  `record_structuring_run` was **deleted** rather than deprecated, so there is
  one guarded writer instead of two paths, and the live P1-4 script now
  ingests through the same one.

  The guard is mutation-checked, not merely present: with
  `coding_row_params` monkeypatched to leak a rate into the `f1` slot,
  `record_coding_run` raises `UnscoreableAgentError` before `get_conn`, so a
  refused write never reaches the database. Counted before and after in
  `test_a_refused_write_inserts_nothing_at_all`, because "raises" alone would
  still pass if a row had already landed.

  **The spec's backfill premise was wrong, and checking it produced a
  cross-check nobody had ever run.** P1-4's July measurements were already in
  `eval_runs` as rows 1 and 2, written live on 2026-07-14, not missing as the
  spec assumed. All eight stored values reproduce from the committed
  artifacts, and PriMock57's `accuracy` was already correctly NULL. The
  database rows and the committed artifacts are two independent halves of
  P1-4's evidence and they agree.

  They carried no provenance, though: `model_effort` and `metrics` were both
  NULL, so nothing recorded the model, effort, prompt hash or output cap. P3-3
  compares windows by `GenerationConfig`, and a reference window with no
  config is comparable to nothing, so backfilling alongside them would have
  double-counted one measurement. Both were re-filed through the guarded
  writer by `scripts/refile_eval_run.py`, which refuses to delete a row until
  the artifact has reproduced its stored metrics and its timestamp lands
  within five minutes of the row's, snapshots the original to
  `governance/eval_artifacts/refiled/`, and inserts and verifies the
  replacement **before** removing the original, so an interrupted run leaves a
  visible duplicate rather than a missing measurement.

  | row | dataset | n | accuracy | f1 | created_at |
  |---|---|---|---|---|---|
  | 7 | aci-bench-heldout-v1 | 120 | 0.8796581 | 0.86856335 | 2026-07-14T03:24:03Z |
  | 8 | primock57-heldout-v1 | 7 | NULL | 0.8990241 | 2026-07-14T09:36:50Z |

  `created_at` is **redefined as the time the measurement was taken**, not the
  time the row was inserted, and is written explicitly from the artifact. Under
  the `now()` default a July measurement filed in August would sit at the
  newest end of the trend and P3-3 would read any drift backwards. The
  definition is in `db/schema.sql` so no later reader misinterprets the column.
  The re-filed rows kept the artifact's stamp rather than the original row's,
  which differed by the 213 ms and 58 ms artifact-to-insert lag; the insert
  time is not a fact about the model.

  **PriMock57's NULL accuracy survives the whole trip.** `replay()` forces it
  back to `None` because placement is not scorable against an unsectioned GP
  note, where the arithmetic yields a meaningless 1.0. Row 8 stores SQL NULL.
  Had `record_eval_run` written the 1.0, the exact failure `replay()` exists to
  prevent would have occurred one layer further down, in the table a dashboard
  reads.

  **The `max_tokens` gap is closed forward and left open backward.** The
  artifact never recorded `max_tokens`, even though `generate_soap` folds it
  into its cache key precisely because a 1200-token cap once truncated long
  encounters. Whether it was 8000 in July is not recoverable, so both July rows
  record `max_tokens: null`, meaning "not recorded by the harness of the day",
  never `8000`. `_redacted()` now writes it, so every window from here on is
  fully provenanced. `differing_fields` reports `max_tokens` as **differing**
  when exactly one side is null, so P3-3 sees the ambiguity in data rather than
  assuming equality.

  **One deviation from the spec, recorded rather than applied quietly.** Spec
  §7 assigns the split check to `heldout.verify_split()`, which rebuilds the
  split from `data/`. That would make the ingest path unrunnable in CI, where
  `data/` is gitignored, and would contradict spec §8's requirement that the
  replay tests need neither `needs_data` nor `needs_db`. Ingesting a committed
  artifact never touches the datasets. `score_artifact` therefore raises the
  same `SplitDriftError` on a data-free comparison of the artifact's
  `split_digest` against the committed lock, and `verify_split()` stays where
  it already guards generation, in `scripts/run_structuring_eval.py`, before a
  single paid call.

  **Carried forward.** One window exists, not two; P3-2 produces window 2 and
  costs a live paid run. The agent registry is hand-maintained, so a genuinely
  new agent raises `UnknownAgentError` at write time rather than at startup.
  The guard protects `eval_runs` only: a consumer that computes a number and
  renders it without writing a row is outside its reach. PriMock57's window is
  n=7, too small to quote as a headline beside ACI-Bench's n=120; P3-4 decides
  what may be said about it.
- **P3-2 Two windows of data.** Done when at least one agent has accuracy stored for at least two distinct versions or time windows, so a trend exists to plot.

  **DONE 2026-08-31.** Spec
  `docs/superpowers/specs/2026-08-31-p3-2-two-windows-design.md`, plan
  `docs/superpowers/plans/2026-08-31-p3-2-two-windows.md`. 386 passed /
  1 xfailed (from 368), ruff clean.

  | id | window | n | f1 | recall | precision | accuracy | measured |
  |---|---|---|---|---|---|---|---|
  | 7 | v1 | 120 | 0.868563 | 0.785649 | 0.971044 | 0.879658 | 2026-07-14 |
  | 25 | 2026-08-w5 | 120 | 0.873771 | 0.792919 | 0.972984 | 0.884425 | 2026-08-31 |

  Window 2 cost **$4.55** over 120 encounters, inside the $4.30 to $5.30 the
  pilot projected. Artifact
  `governance/eval_artifacts/structuring_aci-bench-heldout-v1_20260831T205449Z.json`.

  **The task turned out not to be the paid run.** Measured before writing any
  code, free: **all 120 held-out structuring calls were already cached.**
  `cache_key` covers the model, prompt version and payload but NOT the window,
  and P3-1 defined a window as a point in time with the generation
  configuration held FIXED, so two windows are the same key by construction.
  Running window 2 that day would have replayed July's notes, reproduced its
  metrics bit-identically, filed them under today's date, and passed
  `replay()`, P3-1's guard and CI. P3-3 would then have been "validated"
  against two copies of one measurement. Nothing in the system detected this.

  Two independent guards now close it, failing for different reasons so
  neither masks the other. Both verified live, not only in pytest:

  | Guard | Evidence |
  |---|---|
  | 1, causal: a window served from cache is refused | the real run reports `structure 120 generated, 0 from cache` |
  | 2, symptomatic: two windows may not share counts | filing window 2's own artifact under `2026-09-FAKE` was refused and inserted nothing, row count 5 before and after |

  Windows get their own cache directory rather than the label being folded
  into the key, because the flat cache also holds P2-4's coding entries and
  this project has already lost a run to a key changed underneath it. Nothing
  was migrated; the legacy cache still holds its 2,804 files. Whisper
  transcripts stay shared, being local, deterministic, and not the model under
  test.

  **The delta must not be called drift, and P3-3 has to honor three reasons.**
  F1 moved +0.005208. That is not yet interpretable:

  1. **The two windows are not certified comparable.**
     `differing_fields` returns `('max_tokens',)`, because July's cap was never
     recorded and window 2's is 8000. This is the P3-1 design working as
     intended on its first real use, not a defect.
  2. **The sampling baseline is unmeasured.** Effort-driven calls sample, so
     two runs of the identical configuration differ even with no vendor
     change. Establishing that baseline needs a same-day repeat run, a second
     paid run, and it was deliberately **not** done here.
  3. **The measuring instrument moved too.** The reference notes are byte
     identical across both windows, yet decomposition produced **6,553**
     reference facts against July's **6,550**. The judge and decomposer are
     model calls, so part of any observed delta is instrument variation rather
     than a change in what is being measured. A drift metric that attributes
     all of it to the model is wrong.

  **Two findings outside the task's scope, both verified rather than assumed.**
  `governance/pricing.json` was pricing Sonnet 5 at $3/$15, a rate that never
  took effect: the published table records that $2/$10 became standard and the
  September increase was cancelled. Recomputing P2-4 from its own token counts
  gives arm A $4.01 rather than the reported $6.01, arm B unchanged at $3.16,
  so **the P2-4 routing branch does not flip** but the margin narrows from
  $2.85 to $0.84. Haiku 4.5 was absent from the table entirely, which is why
  the structuring harness could never cost itself; it is the pinned judge.
  A committed price table goes stale silently, and a cost-based routing
  decision sits on top of this one.

  Second, a `--limit` smoke run used to write its artifact into
  `governance/eval_artifacts/` under a filename indistinguishable from a real
  measurement, so a five-encounter probe sat beside the 120-encounter window
  looking exactly like it. Found while running this task's pilot and squarely
  its business. Partial runs now go to a gitignored `smoke/` subdirectory.

  **Smaller things worth keeping.** Sonnet made 122 calls for 120 encounters:
  two bounded resamples, matching the known ~1/120 invalid-JSON rate. 40 of
  1,436 decompose calls were cache hits **within** the run, which is identical
  section text across encounters deduplicating, not a cross-window leak; the
  namespace was empty at start. Structuring at high effort emits about 936
  output tokens per note, nothing like the 6,477 P2-4 measured for coding at
  xhigh, which is why this run was cheap.
- **P3-3 Drift detection.** Done when `governance/drift.py` compares a reference window against a current window and, given an injected accuracy or confidence drop in a controlled test, flags it. The test in `tests/test_drift.py` passes.
- **P3-4 Transparency report generator.** Done when `governance/transparency.py` produces a report from real `model_inventory` data using ONC HTI-1 style fields, mapped to real disclosure language where possible.
- **P3-5 Governance API.** Expose read endpoints for inventory, accuracy trend, and the transparency report so the dashboard has real data. Done when each endpoint returns registry-backed JSON, no mocked values.

**Exit gate:** an injected accuracy drop is flagged by drift detection, and a transparency report renders from real data.
**Metric unlocked:** drift detection sensitivity on a controlled injected drop.

---

## Phase 4: Dashboard and Polish (~1 week)

**Goal:** A working dashboard, end-to-end and load tests, and a public launch.

- **P4-1 Dashboard wiring.** Done when the React app renders model inventory, a per-agent accuracy trend chart, active drift alerts, and one transparency report, all from the Phase 3 endpoints with no hardcoded values.
- **P4-2 End-to-end integration test.** Done when a single test drives audio or transcript in through to three logged agent decisions and asserts the registry rows exist.
- **P4-3 Load test and latency capture.** Done when the Locust script in `scripts/load_test.py` runs against the intake path and captures p95 latency and requests per second from a committed, reproducible run.
- **P4-4 Documentation and demo.** Done when the README reflects the built system, every claimed metric links to the script that produces it, and a demo video is recorded, mirroring the ClinAIQA launch pattern.
- **P4-5 Metric audit.** Done when a single command or short script regenerates every headline number, so nothing on the resume is unbacked.

**Exit gate:** the Definition of Done checklist in the PRD is fully checked, and every metric is reproducible.
**Metric unlocked:** end-to-end p95 latency and requests per second, test count and coverage percentage.

---

## Phase 5: Stretch (optional, time permitting)

**Goal:** Close the LLM fine-tuning gap and the cloud deployment gap. Deferred so the MVP is never blocked on either.

- **P5-1 LoRA fine-tune of the note-structuring model.** Fine-tune an open note-structuring model on a public clinical NLP dataset. Done when fine-tuned accuracy is compared against the prompted baseline on the same held-out set, and both numbers are recorded honestly, including the case where fine-tuning does not win.
- **P5-2 Cloud deployment to AWS EKS.** Move from local kind to a cloud cluster. Done when the services run on EKS, a CI/CD deploy step is added to the GitHub Actions workflow, and the service count and uptime are captured.
- **P5-3 Embedding-based Care Gap Agent.** Augment or replace the rules engine with embedding retrieval against a guideline reference set. Done when embedding-based recall is compared against the rules-based baseline on the held-out set.

**Exit gate:** at least one stretch item complete with a measured comparison against its baseline.
**Metric unlocked:** fine-tuned versus prompted accuracy delta, cloud Kubernetes service count and uptime.

---

## Dependency notes

- P1-4 depends on P0-5. Do not start accuracy work until the held-out split is locked.
- P2-4 (coding benchmark) depends on P0-5 and a working coding agent from P2-3.
- All of Phase 3 depends on Phase 2 registry logging (P2-7).
- Phase 4 dashboard wiring (P4-1) depends on the Phase 3 governance API (P3-5).
- Phase 5 is independent of Phase 4 and can run in either order once Phase 3 is done.

## Resume framing (build first, write later)

Once real numbers exist, describe the work with the Google XYZ formula and measured metrics only. Likely angles: note-structuring F1 on the leak-free held-out split (the one accuracy number backed by a labeled reference set), drift detection sensitivity on a controlled injected drop, end-to-end latency under load, Kubernetes service count and uptime, the coding model routing decision backed by verified rate and cost, and test count with coverage. No inflated numbers, no invented ones.

**The specific trap here.** "Coding accuracy" is the phrasing this project keeps reaching for and it is not supported by the data (see P2-4). The verified rate says a suggested code *exists in the CMS release*, not that it is *right for the note*. Anything on a resume must say which of those two it is.
