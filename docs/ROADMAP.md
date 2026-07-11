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
- **P2-4 Coding model benchmark.** Run Sonnet 5 at xhigh against Opus 4.8 at high on the held-out coding set. Done when both results are written to `eval_runs` and the winner is recorded in `shared/llm.py` with a one-line note on why.
- **P2-5 Containerize and deploy.** Done when each agent has its own image and Kubernetes Deployment plus Service, and `kubectl get pods -n care-ops` shows all three agents plus the orchestrator running with passing readiness probes.
- **P2-6 LangGraph orchestration.** Done when `POST /run` on the orchestrator fans out to all three agents over in-cluster service DNS, an integration test verifies inter-service communication, and a single agent failure does not abort the other two.
- **P2-7 Registry logging for every agent.** Done when every agent call writes a row to `agent_decisions` with input, output, confidence, model, effort, and latency, and a query by encounter id returns every decision.

**Exit gate:** a note submitted to the orchestrator returns all three structured artifacts, each logged, with the pipeline surviving a single injected agent failure.
**Metric unlocked:** per-agent decision accuracy, Kubernetes service count, and the coding model routing decision backed by numbers.

---

## Phase 3: Governance and Drift (~1 to 2 weeks)

**Goal:** Continuous evaluation, drift detection, and an auto-generated transparency report.

- **P3-1 Evaluation runner.** Done when `governance/evaluate.py` scores an agent against the held-out set for a named window and writes accuracy, F1, precision, and recall to `eval_runs`.
- **P3-2 Two windows of data.** Done when at least one agent has accuracy stored for at least two distinct versions or time windows, so a trend exists to plot.
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

Once real numbers exist, describe the work with the Google XYZ formula and measured metrics only. Likely angles: per-agent decision accuracy on the held-out set, drift detection sensitivity on a controlled injected drop, end-to-end latency under load, Kubernetes service count and uptime, the coding model routing decision backed by a benchmark, and test count with coverage. No inflated numbers, no invented ones.
