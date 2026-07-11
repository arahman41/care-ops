"""Rules-based care gap detection. Deterministic and auditable for v1.

Each rule is a keyword or pattern trigger mapped to a standard screening
or follow-up guideline. Embedding-based matching is a v2 candidate. The
LLM is not used for the core match, only optionally to phrase evidence.
"""
from __future__ import annotations

import re

# rule_id -> (trigger regex, gap description)
RULES = {
    "A1C_OVERDUE": (r"\b(diabet|a1c|hba1c|blood sugar)\b",
                    "Consider A1c screening if not done in the last 3 months"),
    "BP_FOLLOWUP": (r"\b(hypertens|blood pressure|elevated bp)\b",
                    "Blood pressure follow-up may be due"),
    "LIPID_PANEL": (r"\b(cholesterol|statin|lipid)\b",
                    "Lipid panel screening may be overdue"),
    "SMOKING_COUNSEL": (r"\b(smok|tobacco|vaping)\b",
                        "Tobacco cessation counseling recommended"),
}


def find_gaps(text: str) -> list[dict]:
    hits = []
    lower = text.lower()
    for rule_id, (pattern, gap) in RULES.items():
        m = re.search(pattern, lower)
        if m:
            hits.append({"gap": gap, "rule_id": rule_id,
                         "evidence": f"matched '{m.group(0)}'"})
    return hits
