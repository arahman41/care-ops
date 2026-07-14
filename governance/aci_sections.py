"""Parse an ACI-Bench clinician note into SOAP buckets.

The reference notes are the gold standard for the headline structuring
metric, so how they are read is part of the metric. Two failure modes here
would corrupt the number silently rather than crash, and both are guarded.

1. Line endings. The challenge CSVs use CRLF inside the quoted note field. A
   '$'-anchored header regex therefore matches nothing, every note parses to
   zero sections, and the harness happily reports an accuracy computed
   against an empty reference set. Observed live while designing this. So
   normalize() runs before anything else touches the text.

2. Unmapped headers. A header this table does not know is a section of the
   clinician's note that would be dropped, dropping its facts, shrinking the
   recall denominator and inflating the score. So an unknown header raises.

Assessment and plan: 51 of the 120 held-out notes fuse them into a single
ASSESSMENT AND PLAN section, and the reference gives no basis to split them.
Rather than guess, each section carries the *set* of SOAP buckets a fact
drawn from it may legitimately occupy: a fused section accepts either
assessment or plan, and a separately-headed one accepts only itself. The
leniency is therefore per-fact, exact, and reported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SUBJECTIVE = "subjective"
OBJECTIVE = "objective"
ASSESSMENT = "assessment"
PLAN = "plan"

SOAP_BUCKETS = (SUBJECTIVE, OBJECTIVE, ASSESSMENT, PLAN)

# Sentinel for the fused ASSESSMENT AND PLAN header, which is not a bucket
# but a pair of them.
_FUSED_AP = "assessment_and_plan"

# Every section header present in the 120 held-out ACI-Bench notes, mapped to
# the SOAP bucket a fact from that section belongs in. Enumerated from the
# data, not guessed. The histories, medications and allergies sit under
# subjective because they are patient-reported, which is the standard SOAP
# reading and the one this project commits to.
HEADER_TO_BUCKET: dict[str, str] = {
    # Subjective: what the patient reports, including the history they give.
    "CHIEF COMPLAINT": SUBJECTIVE,
    "HISTORY OF PRESENT ILLNESS": SUBJECTIVE,
    "REVIEW OF SYSTEMS": SUBJECTIVE,
    "REVIEW OF SYMPTOMS": SUBJECTIVE,
    "SOCIAL HISTORY": SUBJECTIVE,
    "MEDICAL HISTORY": SUBJECTIVE,
    "PAST HISTORY": SUBJECTIVE,
    "PAST MEDICAL HISTORY": SUBJECTIVE,
    "FAMILY HISTORY": SUBJECTIVE,
    "SURGICAL HISTORY": SUBJECTIVE,
    "MEDICATIONS": SUBJECTIVE,
    "CURRENT MEDICATIONS": SUBJECTIVE,
    "ALLERGIES": SUBJECTIVE,
    "SUBJECTIVE": SUBJECTIVE,
    # Objective: what the clinician measured, observed, or ordered back.
    "VITALS": OBJECTIVE,
    "VITALS REVIEWED": OBJECTIVE,
    "PHYSICAL EXAM": OBJECTIVE,
    "PHYSICAL EXAMINATION": OBJECTIVE,
    "EXAM": OBJECTIVE,
    "RESULTS": OBJECTIVE,
    "EKG": OBJECTIVE,
    "PROCEDURE": OBJECTIVE,
    # Assessment: the clinical judgment.
    "ASSESSMENT": ASSESSMENT,
    "IMPRESSION": ASSESSMENT,
    # Plan: what happens next.
    "PLAN": PLAN,
    "INSTRUCTIONS": PLAN,
    "ORDERS": PLAN,
    # Fused: no ground truth separating A from P.
    "ASSESSMENT AND PLAN": _FUSED_AP,
}

# An all-caps line on its own is a section header. Anchored per line, which is
# exactly why the CRLF normalization above is load-bearing.
_HEADER_RE = re.compile(r"^([A-Z][A-Z0-9 /&'-]{2,})$", re.M)


class UnknownSectionError(ValueError):
    """A reference note contains a section this harness cannot place."""


@dataclass(frozen=True)
class RefSection:
    """One section of a reference note, and where its facts may legally live."""

    header: str
    body: str
    acceptable: frozenset[str]   # SOAP buckets a fact from here may occupy

    @property
    def is_fused(self) -> bool:
        """True when the reference fused assessment and plan into one section."""
        return len(self.acceptable) > 1

    @property
    def primary(self) -> str:
        """A single bucket for reporting. Fused sections report as assessment."""
        return ASSESSMENT if self.is_fused else next(iter(self.acceptable))


def normalize(note: str) -> str:
    """CRLF and CR to LF. Must run before any line-anchored matching."""
    return note.replace("\r\n", "\n").replace("\r", "\n")


def parse_sections(note: str) -> dict[str, str]:
    """Split a reference note into {HEADER: body}. CRLF-safe."""
    text = normalize(note)
    matches = list(_HEADER_RE.finditer(text))

    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        header = match.group(1).strip()
        body = text[start:end].strip()
        if body:
            sections[header] = body
    return sections


def bucket_sections(note: str) -> list[RefSection]:
    """Map a reference note's sections onto SOAP buckets.

    Raises rather than dropping anything. A dropped section is a dropped set
    of reference facts, which inflates recall without leaving a trace.
    """
    sections = parse_sections(note)
    if not sections:
        raise UnknownSectionError(
            "Reference note has no section headers, so there is nothing to "
            "score against. Refusing to return an empty reference set, which "
            "would produce a meaningless accuracy. Check line endings first: "
            "the ACI-Bench CSVs use CRLF.")

    out: list[RefSection] = []
    for header, body in sections.items():
        try:
            bucket = HEADER_TO_BUCKET[header]
        except KeyError as exc:
            raise UnknownSectionError(
                f"Unmapped section header {header!r}. Dropping it would "
                f"silently remove this section's reference facts and inflate "
                f"recall, so add it to HEADER_TO_BUCKET instead."
            ) from exc

        acceptable = (frozenset({ASSESSMENT, PLAN}) if bucket == _FUSED_AP
                      else frozenset({bucket}))
        out.append(RefSection(header=header, body=body, acceptable=acceptable))

    return out
