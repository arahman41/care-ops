# AGENTS.md

Guidance for AI coding agents working in this repo.

## What this project is
Care Ops Copilot: an end-to-end pipeline that turns a clinical encounter
into a structured SOAP note, routes it through three specialist agents,
and governs every AI decision with an auditable drift-monitoring layer.
See docs/PRD-CareOpsCopilot-MVP.md and docs/TECH-DESIGN.md.

## Hard rules
- No real patient data, ever. Public de-identified datasets only.
- No notebooks in the repo.
- No em dashes anywhere, in code, comments, or docs.
- The held-out evaluation set is leak-free. It never tunes rules or prompts.
- Report measured metrics only. Never inflate or invent numbers.
- Every agent returns a structured artifact with a confidence in [0, 1],
  never free text as its primary payload.
- Phase gates are hard stops. Do not advance to the next phase until the
  current phase exit gate in docs/ROADMAP.md is met and verified with
  evidence: command output, a passing test, or an eval_runs row. State
  the gate, show the evidence, and get explicit user confirmation before
  starting the next phase. Specifically: do not begin any accuracy work
  until the leak-free held-out split (P0-5) is locked, and do not start
  Phase 2 until one PriMock57 encounter runs end to end and produces a
  measured structuring accuracy on the held-out set (the Phase 1 gate).

## Model routing
Two layers, kept separate in docs/MODEL-EFFORT-GUIDE.md. The app runtime
routing lives in shared/llm.py: do not scatter model ids across the code.
Structuring and prior-auth use Sonnet 5 at high effort, care-gap is
rules-based with Haiku only for optional phrasing, coding uses Sonnet 5
at xhigh with an Opus 4.8 benchmark, transparency uses Haiku.

For the Claude Code session itself, follow the notify convention in
docs/MODEL-EFFORT-GUIDE.md: state the recommended model and effort at the
start of each task and prompt the user to switch if needed.

## Phase map
- Phase 0: scaffolding, local cluster, dataset, Postgres schema.
- Phase 1: intake (Whisper plus Claude structuring) and its tests.
- Phase 2: three agents, containerized, wired via LangGraph on Kubernetes.
- Phase 3: registry logging, Evidently drift, transparency report.
- Phase 4: dashboard, integration and load tests, docs, demo video.
- Phase 5 (stretch): LoRA fine-tune, cloud EKS deploy.

## Definition of done
See section 12 of the PRD. Every claimed metric must be reproducible from
a committed script.
