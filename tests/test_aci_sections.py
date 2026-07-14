"""Parsing an ACI-Bench reference note into SOAP buckets (P1-4).

Two bugs here would silently corrupt the headline metric rather than crash,
so both get a dedicated regression test.

1. CRLF. The challenge CSVs use \\r\\n inside the quoted note field, so a
   '$'-anchored header regex matches nothing, every note parses to zero
   sections, and the harness reports an accuracy computed against an empty
   reference set. This was observed live while designing the harness.

2. Unmapped headers. A section we fail to map is a section of the clinician's
   note we drop, which drops its facts, which shrinks the recall denominator
   and inflates the score. Unknown headers raise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from governance.aci_sections import (
    ASSESSMENT,
    OBJECTIVE,
    PLAN,
    SUBJECTIVE,
    UnknownSectionError,
    bucket_sections,
    normalize,
    parse_sections,
)
from governance.heldout import load_aci_heldout

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"

_DATA_PRESENT = (DATA_ROOT / "aci-bench" / "data" / "challenge_data").is_dir()
needs_data = pytest.mark.skipif(not _DATA_PRESENT,
                                reason="datasets not downloaded")

# Shaped exactly like the real thing: CRLF, blank line after each header.
CRLF_NOTE = (
    "CHIEF COMPLAINT\r\n\r\nCough.\r\n\r\n"
    "PHYSICAL EXAM\r\n\r\nLungs clear.\r\n\r\n"
    "ASSESSMENT AND PLAN\r\n\r\nUpper respiratory infection. Rest and fluids.\r\n"
)


# ---------- the CRLF regression ----------

def test_crlf_note_parses_identically_to_the_same_note_with_lf():
    assert parse_sections(CRLF_NOTE) == parse_sections(normalize(CRLF_NOTE))


def test_crlf_note_actually_finds_its_sections():
    # The bug this pins would return {} here and score against nothing.
    assert set(parse_sections(CRLF_NOTE)) == {
        "CHIEF COMPLAINT", "PHYSICAL EXAM", "ASSESSMENT AND PLAN"}


def test_section_bodies_do_not_keep_carriage_returns():
    assert parse_sections(CRLF_NOTE)["CHIEF COMPLAINT"] == "Cough."


# ---------- the SOAP mapping ----------

def test_headers_map_to_the_expected_soap_buckets():
    by_header = {s.header: s for s in bucket_sections(CRLF_NOTE)}
    assert by_header["CHIEF COMPLAINT"].acceptable == frozenset({SUBJECTIVE})
    assert by_header["PHYSICAL EXAM"].acceptable == frozenset({OBJECTIVE})


def test_a_fused_assessment_and_plan_section_accepts_either_bucket():
    # 51 of the 120 held-out notes fuse these, and the reference gives no
    # basis to prefer one over the other, so a fact from here is correctly
    # placed in assessment OR plan. The leniency is deliberate and disclosed.
    fused = {s.header: s for s in bucket_sections(CRLF_NOTE)}["ASSESSMENT AND PLAN"]
    assert fused.acceptable == frozenset({ASSESSMENT, PLAN})
    assert fused.is_fused is True


def test_a_separate_assessment_section_accepts_only_assessment():
    # No leniency where the reference actually separates them.
    note = "ASSESSMENT\n\nURI.\n\nPLAN\n\nRest.\n"
    by_header = {s.header: s for s in bucket_sections(note)}
    assert by_header["ASSESSMENT"].acceptable == frozenset({ASSESSMENT})
    assert by_header["PLAN"].acceptable == frozenset({PLAN})
    assert by_header["ASSESSMENT"].is_fused is False


def test_unknown_header_raises_rather_than_silently_dropping_facts():
    with pytest.raises(UnknownSectionError, match="inflate recall"):
        bucket_sections("BILLING NOTES\n\nCharge the visit.\n")


def test_a_note_with_no_headers_raises():
    # Scoring against zero reference facts would report a meaningless number.
    with pytest.raises(UnknownSectionError, match="no section headers"):
        bucket_sections("Just a paragraph with no headers at all.\n")


# ---------- the mapping must survive the real dataset ----------

@needs_data
def test_every_header_in_the_real_heldout_set_is_mapped():
    # If ACI-Bench contains a header the table does not know, this fails here
    # loudly instead of quietly deleting reference facts during a scoring run.
    for example in load_aci_heldout():
        bucket_sections(example.reference_note)


@needs_data
def test_the_fused_note_count_is_what_the_design_assumed():
    # The A/P leniency was chosen knowing it applies to 51 of 120 notes. If
    # that number moves, the disclosure in the report is wrong.
    fused = sum(
        any(s.is_fused for s in bucket_sections(e.reference_note))
        for e in load_aci_heldout()
    )
    assert fused == 51
