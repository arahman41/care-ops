# Model and Effort Guide

Two different things use Claude models in this project. Keep them separate.

- **Layer A, your Claude Code session.** The model and effort you run Claude Code at while building. This changes per phase and per task. This is the layer the notify convention below is about.
- **Layer B, the app runtime.** Which Claude model each pipeline component calls at execution time. This lives canonically in `shared/llm.py` and is reproduced here for reference. It does not change as you move through phases.

---

## How to switch (Layer A, Claude Code)

- Model: `/model sonnet`, `/model opus`, `/model haiku`, `/model fable`. Default in Claude Code is Sonnet 5.
- Effort: `/effort low`, `/effort medium`, `/effort high`, `/effort xhigh`, `/effort max`.
- `max` is Opus only and lasts only the current session. `low`, `medium`, and `high` persist across sessions until you change them, so reset deliberately.
- You can also launch with `claude --model opus --effort xhigh`, or set `CLAUDE_CODE_EFFORT_LEVEL` in your environment.

Guiding principle for this build:
- **Sonnet 5** is the default for mechanical work: scaffolding, endpoints, wiring, containerization, UI, docs.
- **Opus 4.8 at xhigh** for reasoning-heavy work: orchestration, clinical-content correctness, drift logic, infrastructure.
- **Opus 4.8 at max** for correctness-critical metric work only: anything that computes or protects a headline number, because a subtle bug there silently invalidates the project. This is the same rule used on ClinAIQA for the leak-free split and the precision and recall computation.

---

## Notify convention (for Claude Code)

At the start of each task, before writing code, Claude Code should:
1. Look up the task in the table below.
2. State the recommended session model and effort, for example "recommended: `/model opus` and `/effort max`".
3. If it likely differs from the current session, tell the user the exact commands to run and wait for confirmation before proceeding.
4. After a correctness-critical task that used `max`, remind the user that `max` persists only for the session and to step back down for routine work.

This convention is referenced in CLAUDE.md and AGENTS.md so it is loaded every session.

---

## Layer A: session model and effort by phase and task

Legend: S5 = Sonnet 5, O48 = Opus 4.8.

### Phase 0: Setup
| Task | Model | Effort | Why |
|---|---|---|---|
| P0-1 Repo scaffold and tooling | S5 | medium | Mechanical |
| P0-2 Postgres schema | S5 | medium | Straightforward DDL |
| P0-3 Local Kubernetes | S5 | high | Manifest correctness matters |
| P0-4 Dataset acquisition | S5 | low | Download and document |
| P0-5 Held-out split definition | O48 | max | Correctness-critical. A leak invalidates every later metric |

### Phase 1: Ambient Intake
| Task | Model | Effort | Why |
|---|---|---|---|
| P1-1 Whisper transcription | S5 | medium | Integration wiring |
| P1-2 SOAP structuring | S5 | high | Prompt and schema design matter |
| P1-3 Intake service and persistence | S5 | medium | CRUD and endpoint |
| P1-4 Accuracy harness | O48 | max | Computes the headline structuring metric |
| P1-5 Intake tests | S5 | high | Coverage of edge cases |
| P1-6 CI green | S5 | medium | Pipeline config |

### Phase 2: Multi-Agent Layer
| Task | Model | Effort | Why |
|---|---|---|---|
| P2-1 Prior-Auth Agent | S5 | high | Agent logic and prompt |
| P2-2 Care Gap real rule set | O48 | xhigh | Needs citable guidelines, high hallucination risk to verify |
| P2-3 Coding and Eligibility Agent | O48 | xhigh | Hardest clinical domain |
| P2-4 Coding model benchmark | O48 | max | An eval that decides routing, must be sound |
| P2-5 Containerize and deploy | S5 | medium | Dockerfiles and manifests |
| P2-6 LangGraph orchestration | O48 | xhigh | Multi-service control and failure isolation |
| P2-7 Registry logging | S5 | high | Audit correctness matters |

### Phase 3: Governance and Drift
| Task | Model | Effort | Why |
|---|---|---|---|
| P3-1 Evaluation runner | O48 | max | Metric computation |
| P3-2 Two windows of data | O48 | max | Produces the second headline number, and the harness will fabricate it if unguarded. See below |
| P3-3 Drift detection | O48 | xhigh | Sensitivity logic and Evidently API correctness |
| P3-4 Transparency report generator | S5 | high | Verify HTI-1 field mapping by hand |
| P3-5 Governance API | S5 | medium | Read endpoints |

### Phase 4: Dashboard and Polish
| Task | Model | Effort | Why |
|---|---|---|---|
| P4-1 Dashboard wiring | S5 | medium | React UI, no hardcoded values |
| P4-2 End-to-end integration test | S5 | high | Full pipeline assertion |
| P4-3 Load test and latency capture | S5 | medium | Run and interpret carefully |
| P4-4 Documentation and demo | S5 | medium | Writing |
| P4-5 Metric audit | O48 | max | Regenerates every headline number |

### Phase 5: Stretch
| Task | Model | Effort | Why |
|---|---|---|---|
| P5-1 LoRA fine-tune and comparison | O48 | xhigh | Training loop and honest baseline comparison |
| P5-2 AWS EKS deploy and CI/CD | O48 | xhigh | Infrastructure reasoning |
| P5-3 Embedding Care Gap Agent | O48 | xhigh | Retrieval design and comparison |

Quick rule if you forget: if the task writes or protects a number that could end up on your resume, use `/model opus` and `/effort max`. Otherwise Sonnet 5 at medium or high is right, and Opus 4.8 xhigh is for the genuinely hard design and orchestration work.

**P3-2 was rated S5/medium, "data plumbing", until 2026-08-31.** That was
written before P3-1 defined what a window is, and it was wrong twice over.
P3-2 produces the second measured number on the held-out set, and P3-3's
entire drift metric is the gap between it and window 1, so the quick rule
above already pointed at max. Worse, the task turned out to contain a live
trap: `cache_key` does not cover the window label, so all 120 held-out
structuring calls were already cached and a second window run against them
would have replayed July's notes and reproduced its metrics bit-identically,
passing `replay()`, the P3-1 guard, and CI. Finding that is not plumbing
work. The row is corrected rather than left to mislead the next reader.

---

## Layer B: app runtime routing (reference, lives in shared/llm.py)

This is what the pipeline calls at execution time. It does not change per phase.

| Component | Model | Effort | Note |
|---|---|---|---|
| Note structuring | claude-sonnet-5 | high | Benchmark Opus 4.8 once, keep the comparison |
| Prior-Auth Agent | claude-sonnet-5 | high | Bounded reasoning |
| Care Gap Agent | claude-haiku-4-5-20251001 | n/a | Rules-based core, LLM only for phrasing |
| Coding and Eligibility | claude-opus-4-8 | high | Chosen by the P2-4 benchmark, 2026-08-07. See note below |
| Transparency report | claude-haiku-4-5-20251001 | n/a | Template fill |
| Orchestrator routing | none | n/a | Deterministic in v1 |

**Coding routing, decided 2026-08-07 (P2-4).** Artifact
`governance/eval_artifacts/coding_20260807T214249Z.json`, `eval_runs` rows 3
and 4. The winner is a `(model, effort)` **configuration**, not a model, and
the number behind it is a vocabulary **verified rate**, never coding accuracy.

Measured over 113 of 120 held-out ACI-Bench notes: Sonnet 5 at xhigh verified
96.65 points against Opus 4.8 at high on 97.35. The paired not-found-rate
difference was 0.70 points, 95% BCa CI [-0.73, 2.22]. That interval neither
clears zero nor fits inside the pre-registered 1.5 point margin, so the branch
was **inconclusive** and the rule routed on **cost**: $3.16 for Opus 4.8 at
high against $6.01 for Sonnet 5 at xhigh over the same set.

So this is a cost decision, not a demonstrated quality win. Latency agreed
independently (p50 15 s against 73 s), as did the unresolved quality point
estimate. Two caveats worth carrying forward: Sonnet 5 at xhigh emits roughly
5.3x the output tokens of Opus 4.8 at high, which is what makes it more
expensive despite the lower per-token rate; and the floor-divergence guard
could not have passed on this data, though removing it does not change the
branch.

Cost controls that stay on regardless of phase: prompt caching on the stable content (SOAP schema, system prompts, coding references) and the Batch API for offline re-scoring in the drift harness. Confirm the exact effort keyword against the current SDK, isolated in `shared/llm.call`.
