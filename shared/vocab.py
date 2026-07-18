"""Vendored CMS code vocabularies and the one place codes are classified.

Two consumers depend on this module: services/agent_coding, and P2-4's
benchmark. The status mapping is the thing they must agree on, so classify()
is the only public entry point for it. A membership primitive is not
exposed, because two callers re-deriving a status from one is how the two
diverge silently and a scoring difference gets read as a model difference.

ICD-10-CM and HCPCS Level II are public domain and vendored. CPT is licensed
and is not, which is the only reason any code ends up "unchecked".

The vendored files are one code per line. The CMS originals are not: the
ICD-10 source is whitespace-delimited with descriptions, and the HCPCS
source is fixed width. Both were converted once at vendoring time, and
data/vocab/PROVENANCE.md carries the reproduction script.
"""
from __future__ import annotations

import gzip
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
VOCAB_DIR = REPO_ROOT / "data" / "vocab"

ICD10_PATH = VOCAB_DIR / "icd10cm_codes_2026.txt.gz"
ICD10_SHA256 = "2a65a372ee0660fb812e2491a6a5d54212fcaccecf1cd508964c79a7744cf587"
HCPCS_PATH = VOCAB_DIR / "hcpcs_level2_2026q3.txt.gz"
HCPCS_SHA256 = "d841e172cb20b718528eef465a8d19f36621570ae068fdaef983969dd810e9e2"

# Names BOTH releases; recorded on every CodingOutput so a stored result is
# traceable to the exact pair that produced it. Bump this on ANY change to
# either pin. That is an obligation on this repo, not a property of the
# releases: ICD-10-CM moves annually, HCPCS Level II quarterly.
VOCAB_VERSION = "ICD-10-CM FY2026 (2026-04-01) + HCPCS Level II 2026Q3"

VocabularyStatus = Literal["verified", "not_found", "unchecked"]

# Five digits (99213), or four digits plus a trailing letter (0001T, 9999F)
# for Category II and III. Both lead with a digit, which is what makes this
# disjoint from ICD-10-CM and HCPCS Level II: those always lead with a
# letter. That disjointness is why a shape test is safe here and was not
# safe for HCPCS, where 6,761 of the 74,719 vendored ICD-10-CM codes share
# the letter-plus-four-digit shape.
_CPT_RE = re.compile(r"^\d{5}$|^\d{4}[A-Z]$")


def normalize(code: str) -> str:
    """Lookup key: trimmed, uppercased, no decimal point.

    Load-bearing. CMS stores ICD-10-CM codes without the dot (E119) while
    models emit the dotted display form (E11.9). Without this every real
    ICD-10 code would read as a hallucination and the metric would be
    exactly inverted. HCPCS carries no decimal, so for it this is only the
    strip and uppercase, but both paths use one definition of a key.
    """
    return code.strip().upper().replace(".", "")


def _load(path: Path, expected_sha256: str) -> frozenset[str]:
    raw = gzip.decompress(path.read_bytes())
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"{path.name} failed its sha256 pin: expected {expected_sha256}, "
            f"got {actual}. The vocabulary changed without the pin being "
            f"updated, which would silently move any metric computed on it."
        )
    return frozenset(
        normalize(line) for line in raw.decode("utf-8").splitlines()
        if line.strip()
    )


@lru_cache(maxsize=1)
def load_icd10() -> frozenset[str]:
    # Reads the module global at call time, NOT via a default argument. A
    # default binds at def time and would defeat the tamper test's
    # monkeypatch, turning it back into the no-op it exists to prevent.
    return _load(ICD10_PATH, ICD10_SHA256)


@lru_cache(maxsize=1)
def load_hcpcs() -> frozenset[str]:
    return _load(HCPCS_PATH, HCPCS_SHA256)


def _looks_like_cpt(code: str) -> bool:
    return bool(_CPT_RE.match(code))
