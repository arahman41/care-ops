"""P2-4 coding routing benchmark orchestration.

Builds byte-identical SoapNote inputs from ACI reference notes, runs both arms
through the shared parse path with per-arm (model, effort) overrides, caches a
serialized LLMResult per call, and forms the analysis set as the intersection of
notes both arms parsed. Touches the API and DB only at run time; every function
here is unit-tested with fakes.

Never calls agent.run(): that logs to agent_decisions with int encounter ids and
NOT NULL foreign keys, and ACI ids are strings like D2N068 (spec §7).
"""
from __future__ import annotations

from governance.aci_sections import SOAP_BUCKETS, bucket_sections
from shared.schemas import SoapNote


def build_soap_from_reference(reference_note: str) -> SoapNote:
    """Concatenate the bodies of all sections sharing a `primary` bucket,
    joined with two newlines (spec §3). Fused ASSESSMENT AND PLAN reports as
    assessment via RefSection.primary, which is why a fused-only note has an
    empty plan."""
    by_bucket: dict[str, list[str]] = {b: [] for b in SOAP_BUCKETS}
    for section in bucket_sections(reference_note):
        by_bucket[section.primary].append(section.body)
    return SoapNote(**{b: "\n\n".join(by_bucket[b]) for b in SOAP_BUCKETS})


def plan_is_empty(soap: SoapNote) -> bool:
    """The stratification variable (spec §3): 27 empty, 93 non-empty."""
    return soap.plan == ""
