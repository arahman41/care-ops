"""P4-5: the metric audit, including proof that it can fail.

The test that matters most here is the mutation one. An audit that returns
"verified" no matter what is worse than no audit, because it converts an
unchecked claim into a checked-looking one, and this repo has already been
bitten by a detector that failed closed (P3-3's drift stub returned False
when it could not find what it was looking for). So one test deliberately
publishes a wrong number and asserts the audit catches it.

No API calls, no cost. The end-to-end test recomputes from committed
artifacts and takes a few seconds.
"""
from __future__ import annotations

import pytest

from governance import audit as audit_mod
from governance.audit import (
    Backing, CLAIMS, Claim, REPO_ROOT, Status, _appears_in, audit, failed,
    summarize,
)


# ---------- manifest hygiene ----------

def test_claim_keys_are_unique():
    keys = [c.key for c in CLAIMS]
    assert len(keys) == len(set(keys)), "a duplicate key silently shadows a claim"


def test_every_observed_claim_names_a_record_that_exists():
    """An OBSERVED claim's only backing is the document recording it, so a
    missing or misspelled path would leave it backed by nothing at all."""
    for claim in CLAIMS:
        if claim.backing is Backing.OBSERVED:
            assert claim.record, f"{claim.key} is OBSERVED but names no record"
            assert (REPO_ROOT / claim.record).exists(), (
                f"{claim.key} names {claim.record}, which does not exist")


def test_no_claim_is_left_without_a_backing_tier():
    for claim in CLAIMS:
        assert isinstance(claim.backing, Backing)
        assert claim.where, f"{claim.key} does not say what backs it"


# ---------- the whole-token matcher ----------

def test_appears_in_does_not_match_inside_a_longer_number():
    """The 0.84 / 0.840 case, which is live in this repo: the coding cost
    margin is $0.84 and PriMock57's capture rate is 0.840. A substring match
    would let one satisfy the other's check."""
    assert not _appears_in("capture rate is 0.840 here", "0.84")
    assert _appears_in("a margin of $0.84 over 113 notes", "0.84")


def test_appears_in_handles_punctuated_values():
    assert _appears_in("delta 95% BCa CI [-0.73, 2.22] straddles", "[-0.73, 2.22]")
    assert _appears_in("out of 5,875** facts", "5,875")
    assert _appears_in("p95 | 240ms |", "240ms")
    assert not _appears_in("58,750 facts", "5,875")


# ---------- classification, with regeneration stubbed ----------

def _claim(key="x", published="1.000", backing=Backing.RECOMPUTED):
    return Claim(key, published, backing, "test claim", in_readme=False)


def test_a_wrong_published_value_is_caught(monkeypatch):
    """The mutation test. If this ever passes with a green audit, the audit
    is decorative."""
    monkeypatch.setattr(audit_mod, "CLAIMS", (_claim(published="0.999"),))
    monkeypatch.setattr(audit_mod, "regenerate",
                        lambda **_: ({"x": "0.111"}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.MISMATCH]
    assert failed(findings), "a mismatch must make the audit fail"
    assert "0.111" in findings[0].detail


def test_a_correct_published_value_verifies(monkeypatch):
    monkeypatch.setattr(audit_mod, "CLAIMS", (_claim(published="0.111"),))
    monkeypatch.setattr(audit_mod, "regenerate",
                        lambda **_: ({"x": "0.111"}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.VERIFIED]
    assert not failed(findings)


def test_a_claim_nothing_regenerated_is_unbacked_not_verified(monkeypatch):
    """Absence of evidence must not read as evidence. A RECOMPUTED claim with
    no regenerated value means the artifact backing it is gone."""
    monkeypatch.setattr(audit_mod, "CLAIMS", (_claim(),))
    monkeypatch.setattr(audit_mod, "regenerate", lambda **_: ({}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.UNBACKED]
    assert failed(findings)


def test_a_missing_environment_claim_is_skipped_not_failed(monkeypatch):
    """An absent cluster is not a wrong number. It is reported, and it does
    not pass either."""
    monkeypatch.setattr(audit_mod, "CLAIMS",
                        (_claim(backing=Backing.ENVIRONMENT),))
    monkeypatch.setattr(audit_mod, "regenerate", lambda **_: ({}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.SKIPPED]
    assert not failed(findings)
    assert summarize(findings)[Status.VERIFIED] == 0


def test_a_readme_claim_missing_from_the_readme_fails(monkeypatch):
    """Catches the manifest and the README drifting apart, which is how a
    corrected number ends up living in only one of them."""
    monkeypatch.setattr(audit_mod, "CLAIMS", (
        Claim("y", "9.87654321", Backing.RECOMPUTED, "test", in_readme=True),))
    monkeypatch.setattr(audit_mod, "regenerate",
                        lambda **_: ({"y": "9.87654321"}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.MISMATCH]
    assert "does not appear in README.md" in findings[0].detail


# ---------- reading the suite's own output ----------

def test_a_suite_run_with_skips_reports_nothing():
    """An incomplete environment must not manufacture a finding. Without a
    Postgres the suite skips every needs_db test and passes a smaller number,
    which is not evidence that the published count is wrong."""
    text = "406 passed, 24 skipped in 71.78s\nTOTAL   2108    178    92%\n"
    assert audit_mod._parse_suite_output(text) == {}


def test_a_clean_suite_run_yields_both_numbers():
    text = "441 passed, 5 warnings in 50.02s\nTOTAL   1600    103    94%\n"
    assert audit_mod._parse_suite_output(text) == {
        "suite_passed": "441", "suite_coverage": "94%"}


def test_unrecognizable_suite_output_yields_nothing():
    """Reported as skipped rather than guessed at, so a changed pytest output
    format shows up as an unchecked claim, never as a false pass."""
    assert audit_mod._parse_suite_output("pytest exploded") == {}


# ---------- observed claims, whose only backing is a document ----------

def test_an_observed_claim_whose_record_is_missing_is_unbacked(monkeypatch):
    monkeypatch.setattr(audit_mod, "CLAIMS", (
        Claim("z", "1,234 ms", Backing.OBSERVED, "test",
              record="docs/NOT-A-REAL-FILE.md", in_readme=False),))
    monkeypatch.setattr(audit_mod, "regenerate", lambda **_: ({}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.UNBACKED]
    assert failed(findings)


def test_an_observed_claim_absent_from_its_record_is_a_mismatch(monkeypatch):
    """The whole point of the OBSERVED tier: a number that cannot be
    recomputed must at least say the same thing everywhere it is written."""
    monkeypatch.setattr(audit_mod, "CLAIMS", (
        Claim("z", "4,321 ms", Backing.OBSERVED, "test",
              record="docs/ROADMAP.md", in_readme=False),))
    monkeypatch.setattr(audit_mod, "regenerate", lambda **_: ({}, []))

    findings, _ = audit()

    assert [f.status for f in findings] == [Status.MISMATCH]
    assert "supposed to record it" in findings[0].detail


def test_a_regenerator_that_throws_is_reported_not_fatal(monkeypatch):
    """One broken artifact must not take the whole audit down with it, or a
    single missing file would hide the state of every other claim."""
    def boom():
        raise ValueError("artifact is corrupt")

    monkeypatch.setattr(audit_mod, "_load_test", boom)
    values, problems = audit_mod.regenerate()

    assert any("artifact is corrupt" in p for p in problems)
    assert "aci_f1" in values, "the other groups still had to run"


# ---------- the real thing ----------

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_the_committed_evidence_backs_every_published_number():
    """The regression guard for the whole project's published numbers.

    Editing an artifact, a metric, or a number in the README without the
    others fails here, on every push, rather than being discovered by a
    reader of the resume.
    """
    findings, problems = audit()

    assert not problems, f"regeneration could not run: {problems}"
    bad = failed(findings)
    assert not bad, "\n".join(
        f"{f.claim.key}: {f.detail}" for f in bad)

    counts = summarize(findings)
    assert counts[Status.VERIFIED] >= 40, (
        "the audit should be regenerating most claims; a sudden drop means "
        "regeneration silently stopped running")
