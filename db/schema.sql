-- Care Ops Copilot: model registry and note storage schema
-- Design goal: every AI decision is reconstructable from these tables.

CREATE TABLE IF NOT EXISTS encounters (
    id              BIGSERIAL PRIMARY KEY,
    external_ref    TEXT,                       -- e.g. PriMock57 file id
    source_type     TEXT NOT NULL,              -- 'audio' or 'transcript'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
    id              BIGSERIAL PRIMARY KEY,
    encounter_id    BIGINT NOT NULL REFERENCES encounters(id),
    version         INT NOT NULL DEFAULT 1,
    soap            JSONB NOT NULL,             -- structured SOAP note
    model           TEXT NOT NULL,              -- model id used to structure
    model_effort    TEXT,                       -- effort level used
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (encounter_id, version)
);

-- Model registry: one row per agent decision. This is the audit backbone.
CREATE TABLE IF NOT EXISTS agent_decisions (
    id              BIGSERIAL PRIMARY KEY,
    encounter_id    BIGINT NOT NULL REFERENCES encounters(id),
    note_id         BIGINT NOT NULL REFERENCES notes(id),
    agent_name      TEXT NOT NULL,              -- prior_auth, care_gap, coding
    model           TEXT NOT NULL,
    model_effort    TEXT,
    input_ref       JSONB NOT NULL,             -- what the agent saw
    output          JSONB NOT NULL,             -- structured artifact
    confidence      REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_decisions_encounter ON agent_decisions(encounter_id);
CREATE INDEX IF NOT EXISTS idx_decisions_agent ON agent_decisions(agent_name);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON agent_decisions(created_at);

-- Evaluation runs: accuracy of an agent against a held-out labeled set,
-- captured per version or time window so drift can be measured.
CREATE TABLE IF NOT EXISTS eval_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,
    model           TEXT NOT NULL,
    window_label    TEXT NOT NULL,              -- e.g. 'v1', 'v2', '2026-07-w1'
    dataset_ref     TEXT NOT NULL,              -- held-out set identifier
    n_examples      INT NOT NULL,
    accuracy        REAL,
    f1              REAL,
    precision       REAL,
    recall          REAL,
    -- P3-1: this is WHEN THE MEASUREMENT WAS TAKEN, not when the row was
    -- inserted. governance/evaluate.py::record_eval_run always writes it
    -- explicitly, from the artifact's own created_at. The default is a
    -- fallback for hand-written rows only. It matters because P1-4's July
    -- measurement was backfilled in August: under the default it would sit at
    -- the newest end of the trend and P3-3 would read the drift backwards.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_agent_window ON eval_runs(agent_name, window_label);

-- P2-4: the coding routing benchmark stores a JSONB metrics blob (per-arm
-- section-1 metrics plus the comparison block) and the effort half of the
-- configuration under test. accuracy/f1/precision/recall stay NULL for coding
-- rows: no held-out set carries gold billing codes, so a verified rate must
-- not be written where a Phase 3 dashboard would read it as accuracy.
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS model_effort TEXT;

-- Model inventory for the transparency dashboard (HTI-1 style fields).
CREATE TABLE IF NOT EXISTS model_inventory (
    id                  BIGSERIAL PRIMARY KEY,
    agent_name          TEXT NOT NULL,
    model               TEXT NOT NULL,
    version             TEXT NOT NULL,
    intended_use        TEXT,
    training_data_note  TEXT,
    known_limitations   TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_name, model, version)
);

-- P3-4: four more of HTI-1's nine source-attribute categories. Performance
-- and validation (the other three: details/output, quantitative performance,
-- and the update/revalidation schedule) are answered live from eval_runs and
-- governance/drift.py, never stored here, so they cannot go stale the way
-- governance/pricing.json and the P1-4 cache key both did.
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    cautioned_out_of_scope_use TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    fairness_process_note TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    external_validation_note TEXT;
ALTER TABLE model_inventory ADD COLUMN IF NOT EXISTS
    maintenance_schedule TEXT;
