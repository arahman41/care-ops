"""Contract tests for shared/vocab.py.

This module is the integrity boundary for P2-4's headline number, so the
tests here are about two things beyond ordinary correctness: that the sha256
gate actually runs, and that the vendored artifacts are what they claim to
be.
"""
import gzip
import hashlib
import shutil
import subprocess

import pytest

from shared import vocab


def test_normalize_strips_dot_whitespace_and_case():
    assert vocab.normalize(" e11.9 ") == "E119"
    assert vocab.normalize("E11.9") == "E119"
    assert vocab.normalize("E119") == "E119"


def test_icd10_pin_matches_decompressed_content():
    data = gzip.decompress(vocab.ICD10_PATH.read_bytes())
    assert hashlib.sha256(data).hexdigest() == vocab.ICD10_SHA256


def test_hcpcs_pin_matches_decompressed_content():
    data = gzip.decompress(vocab.HCPCS_PATH.read_bytes())
    assert hashlib.sha256(data).hexdigest() == vocab.HCPCS_SHA256


def test_loader_raises_on_tampered_content(tmp_path, monkeypatch):
    """The integrity gate must actually run, not just be transcribed.

    Both loaders are lru_cached over a module-level path, so this test has
    to clear the cache on BOTH sides. Without the clear beforehand it reads
    an already-cached good set and passes without touching the
    verification, which is the exact no-op failure this test exists to
    catch. Without the clear afterwards it poisons every later test.
    """
    bad = tmp_path / "tampered.txt.gz"
    bad.write_bytes(gzip.compress(b"E119\n"))

    vocab.load_icd10.cache_clear()
    monkeypatch.setattr(vocab, "ICD10_PATH", bad)
    try:
        with pytest.raises(ValueError, match="sha256"):
            vocab.load_icd10()
    finally:
        vocab.load_icd10.cache_clear()


def test_hcpcs_loader_raises_on_tampered_content(tmp_path, monkeypatch):
    """Both loaders need this, not just one. A copy-paste bug such as
    _load(HCPCS_PATH, ICD10_SHA256) or reusing ICD10_PATH would otherwise
    ship undetected: the sha256 pin tests read the files directly rather
    than through the loaders, so they would still pass."""
    bad = tmp_path / "tampered.txt.gz"
    bad.write_bytes(gzip.compress(b"J1885\n"))

    vocab.load_hcpcs.cache_clear()
    monkeypatch.setattr(vocab, "HCPCS_PATH", bad)
    try:
        with pytest.raises(ValueError, match="sha256"):
            vocab.load_hcpcs()
    finally:
        vocab.load_hcpcs.cache_clear()


def test_vendored_files_are_tracked_by_git():
    """The .gitignore rule is the one failure that passes locally and fails
    only in a fresh clone or in CI."""
    if shutil.which("git") is None:
        pytest.skip("git binary not available")
    if not (vocab.REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for path in (vocab.ICD10_PATH, vocab.HCPCS_PATH):
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=vocab.REPO_ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert result.returncode == 0, (
            f"{path.name} is not tracked by git. The data/vocab/ exception "
            f"in .gitignore is missing or placed above the data/* line."
        )


def test_known_real_codes_are_present():
    icd10 = vocab.load_icd10()
    assert vocab.normalize("E11.9") in icd10
    assert vocab.normalize("I10") in icd10
    hcpcs = vocab.load_hcpcs()
    assert vocab.normalize("J1885") in hcpcs
    assert vocab.normalize("G0008") in hcpcs


def test_cpt_codes_are_in_neither_set():
    """If a CPT code ever appears here, the HCPCS artifact picked up Level I
    and the unchecked bucket has quietly dissolved. See PROVENANCE.md."""
    both = vocab.load_icd10() | vocab.load_hcpcs()
    assert "99213" not in both
    assert "0001T" not in both


def test_constants_are_filled_in_not_placeholders():
    assert vocab.VOCAB_VERSION
    assert "<" not in vocab.VOCAB_VERSION
    assert "<" not in vocab.ICD10_SHA256
    assert "<" not in vocab.HCPCS_SHA256
    assert vocab.ICD10_PATH.is_file()
    assert vocab.HCPCS_PATH.is_file()


def test_vocabulary_sizes_match_the_vendored_artifacts():
    """Exact, because the artifacts are pinned. A change here means the
    vocabulary moved and VOCAB_VERSION and the pins must move with it."""
    assert len(vocab.load_icd10()) == 74_719
    assert len(vocab.load_hcpcs()) == 8_725


def test_classify_verifies_real_codes_regardless_of_declared_system():
    # Rules 1 and 2 ignore the label entirely.
    assert vocab.classify("ICD-10", "E11.9") == "verified"
    assert vocab.classify("CPT", "E11.9") == "verified"
    assert vocab.classify("HCPCS", "J1885") == "verified"
    assert vocab.classify("ICD-10", "J1885") == "verified"


def test_classify_unchecked_only_when_shape_and_label_both_say_cpt():
    assert vocab.classify("CPT", "99213") == "unchecked"
    assert vocab.classify("CPT", "0001T") == "unchecked"


def test_classify_fabricated_icd_shaped_code_declared_cpt_is_not_found():
    """The escape hatch. A model must not be able to exempt its own
    fabricated code by relabelling it."""
    assert vocab.classify("CPT", "M9999") == "not_found"


def test_classify_fabricated_icd_shaped_code_declared_hcpcs_is_not_found():
    """The specific hole an earlier draft opened with a shape-based HCPCS
    rule. M9999 is letter-plus-four-digits, exactly HCPCS shape, and 6,761
    real ICD-10-CM codes share that shape."""
    assert vocab.classify("HCPCS", "M9999") == "not_found"


def test_classify_cpt_shaped_code_declared_hcpcs_is_not_found():
    """The other half of rule 3's label guard."""
    assert vocab.classify("HCPCS", "99213") == "not_found"


def test_classify_real_cpt_code_mislabelled_icd10_is_not_found():
    """A known, conservative distortion: a real code scored as a miss.
    Pinned so it stays deliberate rather than incidental."""
    assert vocab.classify("ICD-10", "99213") == "not_found"


def test_classify_unrecognised_system_still_gets_both_lookups():
    # Rules 1 and 2 ignore the label, so a real code verifies anyway.
    assert vocab.classify("LOINC", "E11.9") == "verified"
    # Only a code absent from BOTH sets is not_found here.
    assert vocab.classify("LOINC", "ZZZ999") == "not_found"


def test_classify_degenerate_input_is_not_found():
    assert vocab.classify("ICD-10", "") == "not_found"
    assert vocab.classify("ICD-10", "N/A") == "not_found"


def test_classify_is_normalization_insensitive():
    assert (vocab.classify("ICD-10", "e11.9")
            == vocab.classify("ICD-10", "E11.9")
            == vocab.classify("ICD-10", "E119")
            == "verified")


def test_verified_rate_pools_counts():
    assert vocab.verified_rate(9, 1) == 0.9


def test_verified_rate_is_none_not_zero_on_empty_denominator():
    """0.0 would report a perfect score for a run where nothing was
    checkable, which is the most misleading possible reading."""
    assert vocab.verified_rate(0, 0) is None


def test_pooled_rate_differs_from_mean_of_per_note_rates():
    """Documents the pooling rule from spec section 2a with a worked case.

    Note A: 1 verified, 0 not_found -> 1.0
    Note B: 1 verified, 3 not_found -> 0.25
    Mean of per-note rates = 0.625; pooled = 2/5 = 0.4.

    Honest about what this is: the counts are pre-summed before the call,
    so this documents the rule rather than testing that a caller pools.
    Nothing in this repo pools yet; P2-4 is the caller. The value is that
    the intended number is written down executably.
    """
    pooled = vocab.verified_rate(1 + 1, 0 + 3)
    mean_of_rates = (1.0 + 0.25) / 2
    assert pooled == 0.4
    assert pooled != mean_of_rates
