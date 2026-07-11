# Care Ops Copilot: Product Requirements Document (MVP)

**Author:** Ateeq Ur Rahman
**Status:** Draft v1 for build
**Type:** Portfolio / demonstration project (not a commercial product)
**Builds on:** ClinAIQA (pre-deployment LLM audit harness)
**Document scope:** MVP (v1). Stretch and v2 items are marked explicitly.

---

## 1. Product Overview

**Name:** Care Ops Copilot

**One-line description:** An end-to-end pipeline that turns a raw clinical encounter into a structured note, routes it through a multi-agent system that flags administrative and clinical follow-up actions, and governs every AI decision with an auditable drift-monitoring dashboard.

**Primary goal:** Close four named, resume-relevant skill gaps in one coherent, defensible project: Kubernetes orchestration, MLOps drift detection, agentic orchestration, and LLM fine-tuning (fine-tuning as a marked stretch goal).

**Secondary goal:** Sit at the intersection of the two fastest-growing healthcare AI hiring categories in mid-2026: ambient clinical documentation and AI governance / model-risk management.

**Timeline target:** Roughly 7 to 9 weeks of part-time work across five build phases, with a stretch phase after MVP ship.

**Success framing:** Portfolio-defensible. Success means verifiable, honestly reported metrics and a working demo, not user adoption. This mirrors the ClinAIQA precedent of citing only verified numbers.

---

## 2. Target Users and Personas

This is a portfolio project, so personas exist to keep scope and design decisions grounded, not to drive a go-to-market plan.

**Primary persona: Clinical operations lead at a mid-size outpatient clinic**
- Needs clinician documentation time reduced.
- Wants confidence that any AI-assisted administrative suggestion (prior-auth flag, care-gap flag) is logged, explainable, and monitored for accuracy drift over time.
- Cares about auditability because new regulatory frameworks are starting to require it.

**Secondary persona: The evaluator of this portfolio (hiring manager, technical interviewer)**
- Wants to see real engineering depth across the four target skill areas.
- Wants honest metrics and a running demo, not slideware.
- This persona is why every architectural choice below is tied to a specific, nameable skill.

---

## 3. Problem Statement

Clinicians and administrative staff face two compounding problems. First, documentation burden: hours of after-hours charting. Second, downstream administrative friction: prior authorization, care-gap tracking, and coding or eligibility checks, all of which depend on that documentation being accurate and complete.

Point solutions exist for each piece separately. Few systems chain ambient documentation into actionable downstream agent workflows while keeping every AI decision auditable and monitored for drift, which is exactly what emerging regulatory frameworks (ONC HTI-1 transparency expectations, CHAI assurance guidance) are starting to require.

Care Ops Copilot demonstrates that full chain end to end.

---

## 4. User Journey

The journey below is the demo narrative for a single encounter passing through all three layers.

1. **Intake.** The ops lead (or a demo script) submits an encounter: an audio file from PriMock57, or a raw transcript. The system timestamps and versions the submission.
2. **Transcription.** Whisper transcribes the audio to text. For transcript-only inputs, this step is skipped.
3. **Structuring.** The Claude API structures the transcript into a SOAP note (Subjective, Objective, Assessment, Plan) as versioned, timestamped JSON.
4. **Routing.** The structured note enters a LangGraph agent graph. Three specialist agents run and each returns a discrete, structured, auditable artifact with a confidence score.
5. **Governance.** Every agent decision (input, output, confidence, timestamp, model version) is logged to the model registry. A held-out labeled set periodically re-scores agent accuracy, and drift detection flags degradation.
6. **Review.** The ops lead opens the dashboard and sees model inventory, per-agent accuracy over time, drift alerts, and an auto-generated transparency report. Every suggestion on screen traces back to a logged, explainable decision.

Outcome: documentation time is reduced and every AI-assisted suggestion is logged, explainable, and monitored.

---

## 5. System Architecture (Three Layers)

### Layer 1: Ambient Intake
- Input: audio file or raw transcript (public de-identified data, no real PHI).
- Whisper (or a Whisper-family model) transcribes audio to text.
- Claude API structures the transcript into a SOAP note.
- Output: structured JSON note, versioned and timestamped.

### Layer 2: Multi-Agent Routing (LangGraph)
- The structured note passes into a LangGraph agent graph with three specialist agents.
  1. **Prior-Auth Agent:** scans for procedures or medications that commonly require prior authorization and drafts a justification snippet.
  2. **Care Gap Agent:** checks the note against a rules-based reference set of standard screening and follow-up guidelines (overdue labs, missed screenings) and flags gaps. Rules-based for v1, embeddings a v2 candidate.
  3. **Coding/Eligibility Agent:** suggests likely ICD-10 or CPT codes and flags potential eligibility mismatches.
- Each agent output is a discrete, structured, auditable artifact with a confidence score, not free text.
- Agents run as independent containerized services orchestrated via Kubernetes. This is the deliberate Kubernetes skill-building piece.

### Layer 3: Governance and Drift Monitoring
- Every agent decision is logged to a model registry (Postgres table following a lightweight model-registry pattern).
- A held-out labeled evaluation set periodically re-scores agent accuracy.
- Evidently AI tracks accuracy and confidence-distribution shift over time and flags degradation.
- A React dashboard displays model inventory, per-agent accuracy over time, drift alerts, and an auto-generated transparency report styled after ONC HTI-1 disclosure fields (model name, version, intended use, training-data summary, performance metrics, known limitations).
- This layer reuses the ClinAIQA audit mindset but applies it continuously in production rather than pre-deployment.

---

## 6. MVP Features and Acceptance Criteria

Each feature below is a v1 must-have. Acceptance criteria are written to be directly testable, consistent with the SDET background.

### F1. End-to-end intake pipeline
**Description:** Audio or transcript in, structured SOAP note out.
**Acceptance criteria:**
- Given a PriMock57 audio file, when submitted, then Whisper produces a transcript and the pipeline returns valid SOAP JSON conforming to the defined schema.
- Given a raw transcript, when submitted, then transcription is skipped and valid SOAP JSON is returned.
- Every note is persisted with a version id and timestamp.
- Malformed or empty input returns a structured error, not a crash.

### F2. Three agents producing structured, logged output
**Description:** Prior-auth, care-gap, and coding/eligibility agents each run on a structured note.
**Acceptance criteria:**
- Given a structured note, when the graph runs, then each of the three agents returns a structured artifact matching its output schema, including a confidence score in [0, 1].
- No agent returns free text as its primary payload.
- Each agent completes or fails independently, and a single agent failure does not abort the other two.

### F3. Agents deployed as separate Kubernetes services
**Description:** Each agent is an independently containerized FastAPI service on a local cluster (kind or minikube).
**Acceptance criteria:**
- Each agent has its own container image and Kubernetes Deployment plus Service manifest.
- `kubectl get pods` shows all three agent services plus the orchestrator running.
- The orchestrator reaches each agent over in-cluster service DNS, verified by an integration test.
- A readiness or liveness probe is defined for each service.

### F4. Model registry logging every agent decision
**Description:** Postgres table capturing every agent call.
**Acceptance criteria:**
- Given any agent decision, when it completes, then a row is written with input reference, output, confidence, timestamp, agent name, and model version.
- Query by encounter id returns every agent decision for that encounter.
- Log writes are covered by automated tests.

### F5. Drift detection on a held-out set
**Description:** Track accuracy across at least two versions or time windows and visualize the trend using Evidently AI.
**Acceptance criteria:**
- A held-out labeled set exists for at least one agent type.
- Accuracy is computed and stored for at least two distinct versions or time windows.
- Given an injected accuracy drop in a controlled test, when drift detection runs, then it flags the drop.
- The trend is retrievable for the dashboard.

### F6. Governance dashboard
**Description:** React dashboard showing model inventory, drift chart, and one auto-generated transparency report.
**Acceptance criteria:**
- Dashboard lists every model or agent in the registry with version and intended use.
- Dashboard renders a per-agent accuracy-over-time chart and any active drift alerts.
- Dashboard renders one transparency report populated from real registry data, with ONC HTI-1-style fields.
- No hardcoded or mocked values in the shipped views; all data comes from the backend.

---

## 7. Out of Scope for v1 (v2 or Stretch Candidates)

- Real-time audio streaming (batch transcript processing is fine for v1).
- Fine-tuning the note-structuring model (prompting first; LoRA fine-tune is a marked stretch, see Section 11).
- Multi-clinic or multi-tenant support.
- Real EHR integration (static de-identified sample data only).
- Auth or RBAC beyond a basic single-user setup.
- Embedding-based Care Gap Agent (rules-based for v1).
- Cloud-hosted cluster (local kind or minikube for v1; AWS EKS is a stretch).

---

## 8. Success Metrics

Portfolio framing. Capture and report honestly. Do not inflate. Mirror the ClinAIQA precedent of citing only verified numbers.

- **Note structuring accuracy:** F1 or exact-field-match rate against a held-out labeled set (PriMock57 and ACI-Bench reference notes).
- **Agent decision accuracy:** per agent type, on a held-out labeled set.
- **End-to-end latency:** p95 and requests per second under light load testing, framed like ClinAIQA's 133 req/s at p95 73ms.
- **Drift detection sensitivity:** does it correctly flag an injected accuracy drop in a controlled test.
- **Test coverage:** count of passing automated tests and coverage percentage, reusing the SDET background as a strength.

Every metric above must be reproducible from a committed script before it appears on a resume.

---

## 9. Design Direction

- **Vibe:** Clinical, auditable, calm. The dashboard should read like a compliance tool, not a consumer app.
- **Key screens:** Model inventory table, per-agent accuracy trend chart, drift alert panel, transparency report viewer.
- **Reuse:** Match ClinAIQA's React frontend patterns for consistency across the portfolio.
- **Priority:** Legibility and traceability over visual flourish. Every number on screen should be clickable back to its source record in a later iteration.

---

## 10. Technical Considerations

| Concern | Approach |
|---|---|
| Transcription | Whisper, local inference, no data leaves the local environment |
| Note structuring | Claude API, reusing the ClinAIQA integration pattern |
| Agent orchestration | LangGraph |
| Agent services | FastAPI, one service per agent |
| Container orchestration | Kubernetes, local via kind or minikube for v1 |
| Model registry | PostgreSQL |
| Drift detection | Evidently AI |
| Dashboard | React |
| CI/CD | GitHub Actions |
| Testing | Pytest, with test count and coverage kept visible |
| Data | PriMock57 (primary, audio) and ACI-Bench (scale, text) |

**Non-functional requirements:**
- No real PHI at any point. Public de-identified data only.
- Every agent decision must be reconstructable from the registry.
- Services must fail independently.
- Latency and load metrics must be reproducible from a committed load-test script.

---

## 11. Stretch Scope (Post-MVP)

- **LoRA fine-tune** of an open note-structuring model on a public clinical NLP dataset, comparing fine-tuned versus prompted accuracy. This is the LLM fine-tuning skill-gap piece, deliberately deferred so the MVP is not blocked on it.
- **Cloud deployment** to AWS EKS instead of local Kubernetes, with a CI/CD deploy step.
- **Embedding-based Care Gap Agent** replacing or augmenting the rules-based v1.

---

## 12. Definition of Done (v1 Launch Checklist)

- [ ] One sample encounter runs end to end: audio or transcript in, SOAP note out.
- [ ] All three agents run and produce structured, logged output.
- [ ] All three agents plus orchestrator run as separate Kubernetes services on a local cluster.
- [ ] Model registry logs every agent decision with confidence, timestamp, and version.
- [ ] Drift detection flags an injected accuracy drop in a controlled test.
- [ ] Dashboard shows model inventory, drift chart, and one transparency report from real data.
- [ ] Automated test suite passes in CI (GitHub Actions), with count and coverage reported.
- [ ] Latency and load metrics captured from a committed, reproducible script.
- [ ] README and demo video published, mirroring the ClinAIQA launch pattern.
- [ ] Every claimed metric is reproducible from a committed script.

---

## 13. Open Questions (Tracked)

| Question | Current lean | Resolve by |
|---|---|---|
| Which dataset for de-identified transcripts | PriMock57 primary (audio), ACI-Bench for scale. Both public, no credentialing. | Resolved for v1 |
| Local Kubernetes vs cloud | Local (kind or minikube) for v1, EKS as stretch | End of Phase 0 |
| Rules vs embeddings for Care Gap Agent | Rules-based for v1, embeddings v2 | Resolved for v1 |
| Transparency report field schema | Map to real ONC HTI-1 disclosure language where possible | During Phase 3 |
