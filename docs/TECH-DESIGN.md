# Care Ops Copilot: Technical Design

**Status:** Draft v1 for build
**Companion to:** docs/PRD-CareOpsCopilot-MVP.md
**Scope:** Translates PRD Sections 5 and 10 into concrete module boundaries, API contracts, data schemas, and the model registry design.

---

## 1. Design Principles

1. **Every AI decision is reconstructable.** The `agent_decisions` table stores input, output, confidence, model, effort, latency, and timestamp for every agent call. Nothing an agent does is invisible.
2. **Structured artifacts, never free text.** Each agent returns a Pydantic-validated object with a confidence score in [0, 1]. This is what makes the output auditable and testable.
3. **One place for cross-cutting concerns.** Model routing lives only in `shared/llm.py`. Data shapes live only in `shared/schemas.py`. Registry writes go only through `shared/registry.py`. No duplication across services.
4. **Services fail independently.** The orchestrator calls each agent over HTTP and isolates failures, so one agent going down does not abort the pipeline.
5. **Leak-free evaluation.** The held-out labeled set never tunes rules or prompts. It scores accuracy and feeds drift detection only.

---

## 2. Module Boundaries

The system is five deployable services plus three importable packages.

### Services (each a FastAPI app, its own container and Kubernetes Service)

| Service | Responsibility | Key module |
|---|---|---|
| `intake` | Whisper transcription and Claude note structuring | `services/intake/` |
| `orchestrator` | LangGraph fan-out to the three agents | `services/orchestrator/` |
| `agent_prior_auth` | Flag prior-auth items, draft justification | `services/agent_prior_auth/` |
| `agent_care_gap` | Rules-based care gap detection | `services/agent_care_gap/` |
| `agent_coding` | Suggest ICD-10 and CPT codes, flag eligibility | `services/agent_coding/` |

### Shared packages (imported, not deployed)

| Package | Responsibility |
|---|---|
| `shared/` | Config, Pydantic schemas, Postgres access, registry logging, Claude routing |
| `governance/` | Held-out evaluation, Evidently drift detection, transparency report generation |
| `dashboard/` | React front end for inventory, accuracy trend, transparency report |

The dependency direction is strict: services depend on `shared`, `governance` depends on `shared`, and nothing depends on a service. This keeps the seams clean and the units testable.

---

## 3. API Contracts

All request and response bodies are the Pydantic models in `shared/schemas.py`. Every service exposes `GET /health` returning `{"status": "ok", "service": "<name>"}` for readiness and liveness probes.

### 3.1 Intake

`POST /intake`

Request:
```json
{ "transcript": "string or null",
  "audio_path": "string or null",
  "external_ref": "PriMock57 file id, optional" }
```
Rules: exactly one of `transcript` or `audio_path` is required. Empty transcript returns 422. Audio is transcribed with Whisper, then structured.

Response:
```json
{ "encounter_id": 1, "note_id": 1,
  "soap": {"subjective": "", "objective": "", "assessment": "", "plan": ""},
  "model": "claude-sonnet-5", "effort": "high" }
```

### 3.2 Orchestrator

`POST /run` accepts an `AgentInput` and returns a `PipelineResult`.

Request (`AgentInput`):
```json
{ "encounter_id": 1, "note_id": 1,
  "soap": {"subjective": "", "objective": "", "assessment": "", "plan": ""} }
```

Response (`PipelineResult`):
```json
{ "encounter_id": 1, "note_id": 1,
  "prior_auth": { "...": "PriorAuthOutput or null" },
  "care_gap": { "...": "CareGapOutput or null" },
  "coding": { "...": "CodingOutput or null" },
  "errors": { "coding": "reason a given agent failed, if any" } }
```

### 3.3 Agents (uniform contract)

Each agent exposes `POST /run`, accepts the same `AgentInput`, and returns its own structured output. The uniform input shape is what lets the orchestrator treat the three agents as interchangeable nodes.

Prior-Auth (`PriorAuthOutput`):
```json
{ "agent_name": "prior_auth",
  "items": [{"item": "", "reason": "", "justification": ""}],
  "confidence": 0.0 }
```

Care Gap (`CareGapOutput`):
```json
{ "agent_name": "care_gap",
  "gaps": [{"gap": "", "rule_id": "", "evidence": ""}],
  "confidence": 0.0 }
```

Coding (`CodingOutput`):
```json
{ "agent_name": "coding",
  "codes": [{"system": "ICD-10", "code": "", "description": "", "eligibility_flag": false}],
  "confidence": 0.0 }
```

---

## 4. SOAP JSON Schema

The note is the contract between Layer 1 and Layer 2. It is deliberately flat: four required string sections. Keeping it flat makes exact-field-match scoring against reference notes straightforward and keeps the structuring prompt simple.

```json
{
  "subjective": "patient reported symptoms and history",
  "objective": "exam findings and measurements",
  "assessment": "clinical assessment and differential",
  "plan": "orders, prescriptions, follow-up"
}
```

Validation: all four keys required, all string, no extra keys accepted by the parser. The structuring prompt instructs the model to return only this JSON and to avoid inventing findings not supported by the transcript.

---

## 5. Model Registry (Postgres)

The registry is the audit backbone. Full DDL is in `db/schema.sql`. Table summary:

| Table | Purpose |
|---|---|
| `encounters` | One row per submitted encounter, with source type and external ref |
| `notes` | Structured SOAP note, versioned per encounter, with model and effort used |
| `agent_decisions` | One row per agent call: input, output, confidence, model, effort, latency, timestamp |
| `eval_runs` | Accuracy of an agent against the held-out set, per version or time window |
| `model_inventory` | HTI-1 style disclosure fields for the transparency report |

Two design choices worth noting. First, `agent_decisions.confidence` carries a `CHECK (confidence >= 0 AND confidence <= 1)` constraint so a malformed confidence never reaches the audit log. Second, `eval_runs.window_label` is the axis the drift chart plots against, which is why accuracy is stored per window rather than as a single running number.

---

## 6. Data Flow (one encounter)

1. Client posts audio or transcript to `intake`.
2. `intake` transcribes if needed, structures the note, writes `encounters` and `notes`, returns `encounter_id` and `note_id`.
3. Client posts the note to `orchestrator` `/run`.
4. `orchestrator` fans out to the three agents over in-cluster service DNS.
5. Each agent runs, writes its row to `agent_decisions`, and returns its artifact.
6. Governance jobs periodically re-score agents against the held-out set, write `eval_runs`, and run Evidently drift on the window series.
7. The dashboard reads `model_inventory`, `eval_runs`, and the transparency report.

---

## 7. Model Routing and Cost Controls

Routing is centralized in `shared/llm.py`:

| Component | Model | Effort | Rationale |
|---|---|---|---|
| Note structuring | `claude-sonnet-5` | high | Headline accuracy metric, bounded extraction, Opus not required |
| Prior-Auth Agent | `claude-sonnet-5` | high | Moderate, well-scoped reasoning |
| Care Gap Agent | `claude-haiku-4-5-20251001` | n/a | Rules-based core, LLM only for optional phrasing |
| Coding/Eligibility | `claude-sonnet-5` vs `claude-opus-4-8` | xhigh vs high | Hardest component; benchmark and keep the winner |
| Transparency report | `claude-haiku-4-5-20251001` | n/a | Template fill |

Two cost controls are first-class, because they are the MLOps discipline the target roles screen for:

1. **Prompt caching** on the stable content (SOAP schema, agent system prompts, coding references), so only the transcript varies per request.
2. **Batch API** for offline re-scoring in the drift harness, which is not latency-sensitive.

Note: the exact effort parameter surface should be confirmed against the current Anthropic SDK. It is isolated in `shared/llm.call` so there is one line to change.

---

## 8. Testing Strategy

- **Contract tests** (`tests/test_schemas.py`): confidence bounds, required SOAP sections, constrained code systems.
- **Deterministic unit tests** (`tests/test_care_gap_rules.py`): the rules engine is fully testable without an LLM.
- **Drift sensitivity** (`tests/test_drift.py`): inject an accuracy or confidence collapse and assert the detector flags it. This is the controlled test behind the drift success metric.
- **Metric correctness** (`tests/test_evaluate.py`): the scoring function is verified against known inputs so the headline numbers cannot be silently wrong.
- **Integration and load** (Phase 4): end-to-end pipeline test and a Locust run for p95 and requests per second.

CI (`.github/workflows/ci.yml`) spins up Postgres, loads the schema, lints with ruff, and runs the suite with coverage on every push and pull request.

---

## 9. Open Items Carried From the PRD

- Confirm the exact transparency report field mapping to ONC HTI-1 disclosure language during Phase 3.
- Decide whether to escalate the Coding Agent to Opus 4.8 based on the held-out benchmark, rather than by default.
- Local Kubernetes for v1, with AWS EKS as the stretch path in Phase 5.
