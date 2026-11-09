"""A deterministic baseline extractor: keyword rules, no model.

It exists so the email path works end to end before any LLM (M5), and so a model-based
extractor has a measured baseline to beat (M9). Its limits are deliberate and known:

- **kind**: the first rule whose phrase occurs in the subject or body, most specific rule
  first (a "port closed" email that also says "delay" is port congestion, not a mere delay);
  no phrase -> no report (UNRECOGNISED), never a guess;
- **summary**: the subject, or the first non-empty body line;
- **references**: identifiers that look like TRACER-style business keys (SUP-001, SHIP-501,
  SKU-P9, ORD-101); other numbering schemes are not recognised (a per-tenant pattern list is
  configuration, for when a real customer's formats are known);
- **impact window**: not extracted (free-text dates are too ambiguous for rules).

The text is only matched against fixed patterns; nothing in it is executed or obeyed.
"""

import re
from typing import Final

from tracer.modules.ingestion.application.intake import ReportFields
from tracer.modules.ingestion.domain.email import ParsedEmail
from tracer.modules.ingestion.domain.report import MAX_KEYS_PER_KIND, ExternalReferences

MAX_SUMMARY_CHARACTERS: Final = 2000

# (kind, phrases): most specific first. Kinds are supply_chain DisruptionKind values; a test
# pins that every kind here is one the consumer accepts.
KIND_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("quality_issue", ("quality issue", "defect", "recall", "contaminat", "failed inspection")),
    (
        "supplier_failure",
        (
            "force majeure",
            "bankrupt",
            "insolven",
            "factory fire",
            "plant shutdown",
            "cannot fulfil",
            "cannot fulfill",
            "unable to fulfil",
            "unable to fulfill",
        ),
    ),
    ("port_congestion", ("port congestion", "port closed", "port closure", "congestion at")),
    (
        "capacity_shortage",
        ("capacity shortage", "out of stock", "shortage", "insufficient capacity"),
    ),
    (
        "shipment_delay",
        ("delayed", "delay", "postponed", "rescheduled", "running late", "late delivery"),
    ),
)

_KEY_PATTERNS: Final = {
    "suppliers": re.compile(r"\bSUP-[A-Z0-9]{1,20}\b"),
    "shipments": re.compile(r"\bSHIP-[A-Z0-9]{1,20}\b"),
    "products": re.compile(r"\bSKU-[A-Z0-9]{1,20}\b"),
    "customer_orders": re.compile(r"\bORD-[A-Z0-9]{1,20}\b"),
}


def _kind(text: str) -> str | None:
    lowered = text.lower()
    for kind, phrases in KIND_RULES:
        if any(phrase in lowered for phrase in phrases):
            return kind
    return None


def _keys(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(pattern.findall(text)))[:MAX_KEYS_PER_KIND]


def _summary(email: ParsedEmail) -> str:
    if email.subject.strip():
        return email.subject.strip()[:MAX_SUMMARY_CHARACTERS]
    first_line = next((line.strip() for line in email.body_text.splitlines() if line.strip()), "")
    return first_line[:MAX_SUMMARY_CHARACTERS]


class KeywordDisruptionExtractor:
    def extract(self, email: ParsedEmail) -> ReportFields | None:
        text = f"{email.subject}\n{email.body_text}"
        kind = _kind(text)
        summary = _summary(email)
        if kind is None or not summary:
            return None
        return ReportFields(
            kind=kind,
            summary=summary,
            impact_start=None,
            impact_end=None,
            references=ExternalReferences(
                suppliers=_keys(_KEY_PATTERNS["suppliers"], text),
                shipments=_keys(_KEY_PATTERNS["shipments"], text),
                products=_keys(_KEY_PATTERNS["products"], text),
                customer_orders=_keys(_KEY_PATTERNS["customer_orders"], text),
            ),
        )
