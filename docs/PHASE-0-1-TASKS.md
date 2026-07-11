# Phase 0 and Phase 1 Task Breakdown

Each task is written to be tracked as a single GitHub issue. Every task has a definition of done that is verifiable, not aspirational. Copy each block into an issue.

---

## Phase 0: Setup (target ~1 week)

### P0-1: Repository scaffold and tooling
- Initialize the repo with the layout in the README.
- Add requirements, requirements-dev, Makefile, .gitignore, .env.example.
- Configure ruff and pytest.
**Done when:** `make dev-install` succeeds, `make lint` runs clean, `pytest` collects the test suite with zero errors.

### P0-2: Postgres schema and local database
- Apply `db/schema.sql` to a local Postgres via docker-compose.
- Confirm all five tables and their constraints exist.
**Done when:** `make db-init` completes, and `\dt` lists encounters, notes, agent_decisions, eval_runs, model_inventory. The confidence CHECK constraint rejects a value of 1.5.

### P0-3: Local Kubernetes cluster
- Create a kind cluster and apply `k8s/namespace.yaml` and `k8s/postgres.yaml`.
**Done when:** `kubectl get pods -n care-ops` shows the postgres pod running and the `db` Service resolves in-cluster.

### P0-4: Dataset acquisition
- Clone PriMock57 into `data/primock57`. Obtain ACI-Bench into `data/aci-bench`.
- Confirm `data/` is gitignored.
**Done when:** both datasets are present locally, no data file is tracked by git, and `scripts/download_data.md` reflects the exact steps taken.

### P0-5: Held-out set definition
- Reserve a leak-free held-out split from the labeled data for note-structuring accuracy.
- Document the split so it is reproducible and never used for tuning.
**Done when:** a committed script produces the same split deterministically, and the split is documented as tuning-forbidden.

---

## Phase 1: Ambient Intake (target ~1 to 2 weeks)

### P1-1: Whisper transcription
- Implement `services/intake/transcribe.py` with faster-whisper.
- Transcribe at least one PriMock57 audio file end to end.
**Done when:** a PriMock57 audio file produces a non-empty transcript, and model size is configurable.

### P1-2: SOAP structuring via Claude
- Implement `services/intake/structure.py` using the Sonnet 5 routing in `shared/llm.py`.
- Enforce the four-section SOAP schema and reject extra keys.
**Done when:** a transcript yields a valid `SoapNote`, malformed model output raises a clear parse error, and the structuring prompt forbids invented findings.

### P1-3: Intake service and persistence
- Wire `services/intake/app.py`: accept audio or transcript, persist `encounters` and `notes`.
- Return encounter_id, note_id, soap, model, effort.
**Done when:** `POST /intake` with a transcript returns 200 and writes one encounter and one note row. Empty input returns 422. `GET /health` returns ok.

### P1-4: Note-structuring accuracy harness
- Score structured output against held-out reference notes (exact-field-match or F1).
- Store the result in `eval_runs`.
**Done when:** the harness runs on the held-out set, prints measured accuracy, and writes an `eval_runs` row. Numbers are measured, never hardcoded.

### P1-5: Intake test suite
- Contract tests for the SOAP schema and the intake endpoint behavior.
**Done when:** `pytest tests/test_schemas.py` passes, empty-input and happy-path intake cases are covered, and coverage for `services/intake` is reported in CI.

### P1-6: CI green on main
- Ensure `.github/workflows/ci.yml` runs Postgres, loads schema, lints, and tests on every push and pull request.
**Done when:** a pull request shows a green CI run including lint and coverage output.

---

## Suggested issue labels
`phase-0`, `phase-1`, `infra`, `intake`, `governance`, `testing`, `data`.

## Tracking note
Close Phase 0 before starting Phase 1 dependencies that need the database or cluster. P1-4 depends on P0-5 (the held-out split). Do not begin accuracy claims until P0-5 is locked, so the headline metric is defensible from the start.
