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
