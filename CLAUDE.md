# CLAUDE.md

Session notes for Claude Code.

## Setup
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements-dev.txt`
3. `cp .env.example .env` and fill in ANTHROPIC_API_KEY
4. `docker compose up db` then `make db-init`

## Common commands
- Run tests: `make test`
- Coverage: `make cov`
- Lint: `make lint`
- Full local stack: `make up`
- Load test: `make load-test`
- Local cluster: `make cluster-up`

## Conventions
- One place for model routing: shared/llm.py.
- One place for data shapes: shared/schemas.py.
- Every agent logs to the registry via shared/registry.py.
- Keep the no-em-dash rule in all generated text.

## Phase gates (hard stop)
Do not advance to the next phase until the current phase exit gate in
docs/ROADMAP.md is met and verified with evidence: command output, a
passing test, or an eval_runs row. State the gate, show the evidence, and
get explicit user confirmation before starting the next phase. Do not
begin any accuracy work until the leak-free held-out split (P0-5) is
locked. Do not start Phase 2 until one PriMock57 encounter runs end to
end and produces a measured structuring accuracy on the held-out set.

## Model and effort per task (important)
Session model and effort recommendations for every phase and task live in
docs/MODEL-EFFORT-GUIDE.md. Follow the notify convention: at the start of
each task, state the recommended `/model` and `/effort` from that guide,
and if it differs from the current session, tell the user the exact
commands to run and wait for confirmation before writing code. Default is
Sonnet 5. Use Opus 4.8 at xhigh for reasoning-heavy design and
orchestration, and Opus 4.8 at max for any task that computes or protects
a headline metric, since a subtle bug there silently invalidates it.
After a max-effort task, remind the user that max is session-only and to
step back down for routine work.
