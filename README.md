# Care Ops Copilot

An end-to-end pipeline that turns a raw clinical encounter into a structured note, routes it through a multi-agent system that flags administrative and clinical follow-up actions, and governs every AI decision with an auditable drift-monitoring dashboard.

This is a portfolio and demonstration project. It uses public de-identified data only and is not a product being sold.

## Why this exists

The build closes four named skill areas in one coherent project: Kubernetes orchestration, MLOps drift detection, agentic orchestration, and LLM fine-tuning (fine-tuning is a marked stretch goal). It sits at the intersection of ambient clinical documentation and AI governance, two of the fastest-growing healthcare AI hiring categories in 2026.

It builds on ClinAIQA, a pre-deployment LLM audit harness. Where ClinAIQA audited output before deployment, Care Ops Copilot monitors decisions continuously in production.

## Architecture

Three layers.

1. **Ambient intake.** Whisper transcribes audio, Claude structures the transcript into a SOAP note as versioned JSON.
2. **Multi-agent routing.** A LangGraph graph fans the note out to three specialist agents (prior-auth, care-gap, coding and eligibility), each returning a structured artifact with a confidence score. Each agent is its own containerized service on Kubernetes.
3. **Governance and drift.** Every agent decision is logged to a Postgres model registry. A held-out labeled set periodically re-scores accuracy, Evidently flags drift, and a React dashboard shows inventory, accuracy trends, drift alerts, and an ONC HTI-1 style transparency report.

See `docs/PRD-CareOpsCopilot-MVP.md` and `docs/TECH-DESIGN.md` for detail.

## Repository layout

```
care-ops-copilot/
  docs/            PRD and technical design
  db/              Postgres schema (model registry)
  shared/          config, schemas, db, registry, Claude routing
  services/        intake, orchestrator, and the three agents (FastAPI)
  governance/      held-out evaluation, Evidently drift, transparency report
  dashboard/       React front end (Vite)
  k8s/             Kubernetes manifests for local cluster
  .github/         GitHub Actions CI
  tests/           contract, rules, drift, and metric tests
  scripts/         load test and dataset instructions
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
kubectl get pods -n care-ops  # expect db, intake, orchestrator, three agents
```

See `k8s/README.md` for image build and secret steps.

## Model routing

Routing is centralized in `shared/llm.py`. Structuring and prior-auth use Sonnet 5 at high effort, care-gap is rules-based with Haiku only for optional phrasing, coding uses Sonnet 5 at xhigh with an Opus 4.8 benchmark, and the transparency report uses Haiku. Stable prompt content is cached and offline re-scoring runs through the Batch API.

## Hard rules

- No real patient data, ever.
- No notebooks in the repo.
- No em dashes anywhere, in code, comments, or docs.
- The held-out evaluation set is leak-free and never tunes rules or prompts.
- Report measured metrics only.

## Success metrics (report honestly)

Note-structuring accuracy, per-agent decision accuracy, end-to-end p95 latency and requests per second, drift detection sensitivity on an injected drop, and test count and coverage. Every claimed metric must be reproducible from a committed script.

## Measured results

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

## Where to start

Read `SETUP.md` for environment setup and the first Claude Code prompt, then follow `docs/ROADMAP.md` for the full Phase 0 through Phase 5 plan. `AGENTS.md` and `CLAUDE.md` give AI coding agents the rules and commands.

## Status

Phase 0 scaffold. See `docs/ROADMAP.md` for every phase and `docs/` for full specs.
