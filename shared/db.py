"""Thin Postgres access layer using psycopg 3."""
from __future__ import annotations

import json
from contextlib import contextmanager

import psycopg

from shared.config import settings


@contextmanager
def get_conn():
    conn = psycopg.connect(settings.database_url, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def insert_encounter(external_ref: str | None, source_type: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO encounters (external_ref, source_type) "
            "VALUES (%s, %s) RETURNING id",
            (external_ref, source_type),
        ).fetchone()
        return row[0]


def insert_note(encounter_id: int, soap: dict, model: str,
                effort: str | None, version: int = 1) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO notes (encounter_id, version, soap, model, model_effort) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (encounter_id, version, json.dumps(soap), model, effort),
        ).fetchone()
        return row[0]
