# SETUP

Everything needed to get from a fresh clone to a green baseline. Read this once, then let Claude Code drive.

## 0. Prerequisites on your machine

You do not need all of these on day one. The first two columns cover Phase 0 and Phase 1. Kubernetes and the datasets are not needed until Phase 2.

| Tool | Needed for | Notes |
|---|---|---|
| Python 3.12 | everything | use a venv |
| Docker and Docker Compose | local Postgres and services | `make up`, `make db-init` |
| psql client | loading the schema via Make | optional if you load schema inside the db container |
| git | version control, Claude Code diffs | init before first session |
| Claude Code | the build itself | already installed on your machine |
| kind or minikube plus kubectl | Phase 2 onward | local Kubernetes |
| PriMock57 and ACI-Bench | Phase 1 accuracy, Phase 2 | see scripts/download_data.md |

## 1. First steps

```bash
unzip care-ops-copilot.zip
cd care-ops-copilot
git init && git add -A && git commit -m "Phase 0 scaffold"
cp .env.example .env          # then edit .env and set a real ANTHROPIC_API_KEY
```

The key in `.env` is the app's key for the pipeline's own Claude calls. It is separate from whatever auth Claude Code itself uses.

## 2. Python environment

There are two pip files, and you need both. `make dev-install` installs only the first. This is deliberate, because the governance dependencies (Evidently, pandas, scikit-learn) are heavier and not every service needs them, but the drift and evaluate tests do.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r governance/requirements.txt
```

## 3. Database and stack

```bash
docker compose up -d db       # Postgres only
make db-init                  # loads db/schema.sql
docker compose up --build     # full local stack when you are ready
```

## 4. Baseline check

```bash
make lint
make test
make cov
```

Tests are designed to run offline. `tests/conftest.py` sets a dummy API key so the schema, rules, drift, and metric tests do not make real Claude calls.

## 5. Known soft spots (fix these early, do not trust them)

1. **Dependency pins are best guesses, not a verified resolve.** The first `pip install` may surface a conflict. Have Claude Code run the install and repin against what actually resolves.
2. **The `effort` keyword in `shared/llm.py` is unverified against the current SDK.** It is isolated to one call, so it is a one-line fix once confirmed. Confirm before any accuracy number matters.
3. **The Care Gap rules are four placeholders.** Swap in real, citable screening guidelines before Phase 1 accuracy work. That agent's credibility rests on a defensible rule set.
4. **`governance/drift.py` uses the newer Evidently API.** If the pinned version's surface differs, the `Report` and preset imports are the two lines to adjust.

## 6. First Claude Code prompt

Paste this to get a green baseline before any feature work:

> Read CLAUDE.md, AGENTS.md, and everything in docs/. Then set up the environment: create a venv, install requirements-dev.txt and governance/requirements.txt, bring up Postgres via docker compose, load db/schema.sql, and run the full test suite with coverage. Fix any dependency conflicts or import failures you hit, repin requirements against what actually resolves, and confirm the effort parameter in shared/llm.py against the current SDK. Then report what passed and what is still red. Do not start Phase 1 features yet.

## 7. Order of work

Follow docs/ROADMAP.md. Do not begin any accuracy claim until the leak-free held-out split is locked (task P0-5). That single discipline is what keeps every headline metric defensible from the start.
