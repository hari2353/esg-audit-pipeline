"""Document extraction layer.

Defines the `DocumentExtractor` protocol (the abstraction the rest of the
pipeline depends on) plus two implementations:

* `PyPdfExtractor`      - offline, text + line-parser based; used for demos/CI.
* `AzureDocIntelligenceExtractor` - thin adapter over the Azure Document
  Intelligence service; used when credentials are configured.

Any new source (OCR engine, LLM-based parser, ...) only needs to satisfy the
protocol - no pipeline changes required (open/closed + dependency inversion).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from esg_pipeline.models import (
    FINDING_CATEGORIES,
    Finding,
    FindingSeverity,
    SupplierAudit,
)


class ExtractionError(Exception):
    """Raised when a document cannot be parsed into a SupplierAudit."""


@dataclass(frozen=True, slots=True)
class RawExtraction:
    """Provider-agnostic raw fields pulled from one document."""

    fields: dict[str, str]
    findings: tuple[dict[str, str], ...] = ()


class DocumentExtractor(Protocol):
    """Anything that can turn a file into raw extracted fields."""

    def extract(self, path: Path) -> RawExtraction: ...


# ---------------------------------------------------------------------------
# Shared line-level parsing helpers (used by both extractors)
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z /]{1,30}):(?P<value>.*)$")

_SEVERITIES = {"minor", "major", "critical"}

_NO_FINDINGS_RE = re.compile(r"^(none identified|no non-?compliances)", re.IGNORECASE)

_FOOTER_RE = re.compile(r"^report generated", re.IGNORECASE)

_CATEGORY_ALIASES: dict[str, str] = {
    "child labor": "child_labor",
    "forced labor": "forced_labor",
    "health safety": "health_safety",
    "health and safety": "health_safety",
    "working hours": "working_hours",
    "wages benefits": "wages_benefits",
    "wages and benefits": "wages_benefits",
    "discrimination": "discrimination",
    "disciplinary practices": "disciplinary_practices",
    "freedom of association": "freedom_of_association",
    "environment": "environment",
}


def _category_from_text(text: str) -> str:
    key = " ".join(text.split()).lower()
    canonical = _CATEGORY_ALIASES.get(key)
    if canonical is not None:
        return canonical
    for alias, canonical_name in _CATEGORY_ALIASES.items():
        if key.startswith(alias):
            return canonical_name
    msg = f"cannot map finding category text: {text!r}"
    raise ExtractionError(msg)


def _parse_labeled_fields(lines: list[str]) -> dict[str, str]:
    """Collect ``Label: value`` pairs; values may sit on the following line.

    Table-based PDFs (and Azure layout output) frequently split a key and its
    value across lines, so an empty-value label consumes the next line when
    that line is not itself a label.
    """
    fields: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _LABEL_RE.match(line)
        if match:
            value = match.group("value").strip()
            if not value and i + 1 < len(lines) and not _LABEL_RE.match(lines[i + 1]):
                value = lines[i + 1].strip()
                i += 1
            if value:
                fields.setdefault(match.group("label").strip(), value)
        i += 1
    return fields


def _parse_finding_rows(lines: list[str]) -> list[dict[str, str]]:
    """Parse finding table rows that arrive as one cell per line.

    Each logical row is ``[index,] category, severity, description...`` where
    the description may wrap across several extracted lines. Parsing is
    robust to wrapped descriptions, placeholder rows, and page breaks.
    """
    findings: list[dict[str, str]] = []
    try:
        header_idx = next(
            i for i, line in enumerate(lines) if line.strip().lower() == "description"
        )
    except StopIteration:
        return findings

    i = header_idx + 1
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line or _FOOTER_RE.match(line):
            break
        # Optional leading row index ("3" or "-").
        if line.isdigit() or line == "-":
            i += 1
            if i >= n:
                break
            line = lines[i].strip()
        if not line or _FOOTER_RE.match(line):
            break
        if _NO_FINDINGS_RE.match(line):
            i += 3  # placeholder row: "None identified", "-", "No non-compliances..."
            continue
        try:
            category = _category_from_text(line)
        except ExtractionError:
            i += 1  # not a category row (e.g. stray header fragment); skip line
            continue
        i += 1
        if i >= n:
            break
        severity = lines[i].strip().lower()
        if severity not in _SEVERITIES:
            continue
        i += 1
        description_parts: list[str] = []
        while i < n:
            nxt = lines[i].strip()
            if (
                not nxt
                or _FOOTER_RE.match(nxt)
                or nxt.isdigit()
                or nxt == "-"
                or _NO_FINDINGS_RE.match(nxt)
            ):
                break
            description_parts.append(nxt)
            i += 1
        description = " ".join(description_parts).strip()
        if description:
            findings.append(
                {"category": category, "severity": severity, "description": description}
            )
    return findings


def _strip_row_index(joined_row: str) -> str:
    """Drop a leading numeric index token from a joined table row."""
    tokens = joined_row.split()
    if tokens and (tokens[0].isdigit() or tokens[0] == "-"):
        return " ".join(tokens[1:])
    return joined_row


# ---------------------------------------------------------------------------
# Local extractor
# ---------------------------------------------------------------------------


class PyPdfExtractor:
    """Offline extractor: pypdf text + the shared line parsers.

    Works on text-based PDFs (such as the synthetic reports and most real
    audit PDFs). Scanned images need the Azure extractor or OCR upstream.
    """

    def extract(self, path: Path) -> RawExtraction:
        from pypdf import PdfReader

        try:
            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            msg = f"failed to read PDF {path}: {exc}"
            raise ExtractionError(msg) from exc
        if not text.strip():
            msg = f"no extractable text in {path} (scanned PDF? use the Azure extractor)"
            raise ExtractionError(msg) from None

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return RawExtraction(
            fields=_parse_labeled_fields(lines),
            findings=tuple(_parse_finding_rows(lines)),
        )


# ---------------------------------------------------------------------------
# Azure adapter
# ---------------------------------------------------------------------------


class AzureDocIntelligenceExtractor:
    """Adapter for Azure AI Document Intelligence (prebuilt-layout).

    Requires `azure-ai-documentintelligence`. Reconstructs table rows and
    key/value pairs from the layout result, then reuses the exact same
    normalization logic as the local extractor so downstream behavior is
    identical for cloud and local sources.
    """

    def __init__(self, endpoint: str, key: str) -> None:
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential
        except ImportError as exc:  # pragma: no cover - optional dependency
            msg = (
                "azure-ai-documentintelligence is required for the Azure extractor; "
                "install with: pip install esg-pipeline[azure]"
            )
            raise ImportError(msg) from exc
        self._client = DocumentIntelligenceClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        )

    def extract(self, path: Path) -> RawExtraction:
        poller = self._client.begin_analyze_document(
            "prebuilt-layout", path.read_bytes()
        )
        result = poller.result()

        fields: dict[str, str] = {}
        for kv in result.key_value_pairs or []:
            if kv.key and kv.value and kv.value.content:
                fields.setdefault(kv.key.content.strip(), kv.value.content.strip())

        lines: list[str] = []
        for table in result.tables or []:
            rows: dict[int, list[str]] = {}
            for cell in table.cells:
                rows.setdefault(cell.row_index, []).append(cell.content.strip())
            for row_idx in sorted(rows):
                lines.append(" ".join(rows[row_idx]))
        return RawExtraction(
            fields=fields,
            findings=tuple(_parse_joined_rows(lines)),
        )


def _parse_joined_rows(joined_rows: list[str]) -> list[dict[str, str]]:
    """Parse finding rows where each row arrives as one joined string."""
    findings: list[dict[str, str]] = []
    for joined in joined_rows:
        row = _strip_row_index(joined)
        if not row or _NO_FINDINGS_RE.match(row):
            continue
        tokens = row.split()
        severity_idx = next(
            (i for i, t in enumerate(tokens) if t.lower() in _SEVERITIES), None
        )
        if severity_idx is None or severity_idx == 0:
            continue
        category = _category_from_text(" ".join(tokens[:severity_idx]))
        description = " ".join(tokens[severity_idx + 1 :]).strip()
        if description:
            findings.append(
                {
                    "category": category,
                    "severity": tokens[severity_idx].lower(),
                    "description": description,
                }
            )
    return findings


# ---------------------------------------------------------------------------
# Normalization to domain model
# ---------------------------------------------------------------------------

_REQUIRED_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "supplier_id": ("Supplier ID", "Supplier Id", "SupplierID"),
    "supplier_name": ("Supplier Name", "Supplier"),
    "country": ("Country",),
    "audit_date": ("Audit Date",),
    "auditor": ("Auditing Body", "Auditor", "Audit Firm"),
    "scheme": ("Audit Scheme", "Scheme"),
    "overall_score": ("Overall Score", "Score"),
}


def _lookup_field(raw: RawExtraction, field: str) -> str | None:
    for alias in _REQUIRED_FIELD_ALIASES[field]:
        for label, value in raw.fields.items():
            if label.lower() == alias.lower():
                return value
    return None


def normalize_extraction(raw: RawExtraction) -> SupplierAudit:
    """Validate and normalize raw fields into a domain `SupplierAudit`.

    Raises `ExtractionError` listing every missing/invalid field so callers
    get one actionable message instead of a trickle of failures.
    """
    errors: list[str] = []

    supplier_id = _lookup_field(raw, "supplier_id")
    supplier_name = _lookup_field(raw, "supplier_name")
    country = _lookup_field(raw, "country")
    audit_date_raw = _lookup_field(raw, "audit_date")
    auditor = _lookup_field(raw, "auditor")
    scheme = _lookup_field(raw, "scheme")
    overall_score_raw = _lookup_field(raw, "overall_score")

    if not supplier_id:
        errors.append("missing field: Supplier ID")
    if not supplier_name:
        errors.append("missing field: Supplier Name")
    if not country:
        errors.append("missing field: Country")
    if not auditor:
        errors.append("missing field: Auditing Body")
    if not scheme:
        errors.append("missing field: Audit Scheme")
    if not audit_date_raw:
        errors.append("missing field: Audit Date")
    if not overall_score_raw:
        errors.append("missing field: Overall Score")

    audit_date: date | None = None
    if audit_date_raw:
        try:
            audit_date = date.fromisoformat(audit_date_raw.strip())
        except ValueError:
            errors.append(f"invalid Audit Date: {audit_date_raw!r} (expected YYYY-MM-DD)")

    overall_score: int | None = None
    if overall_score_raw:
        match = re.search(r"\d{1,3}", overall_score_raw)
        if match:
            overall_score = int(match.group())
        if overall_score is None:
            errors.append(f"invalid Overall Score: {overall_score_raw!r}")

    if errors:
        raise ExtractionError("; ".join(errors))

    assert supplier_id and supplier_name and country and audit_date and auditor
    assert scheme and overall_score is not None

    parsed_findings: list[Finding] = []
    for i, row in enumerate(raw.findings):
        severity_str = row.get("severity", "").lower()
        try:
            severity = FindingSeverity(severity_str)
        except ValueError:
            errors.append(f"finding {i}: invalid severity {severity_str!r}")
            continue
        category = row.get("category", "")
        if category not in FINDING_CATEGORIES:
            errors.append(f"finding {i}: unknown category {category!r}")
            continue
        description = (row.get("description") or "").strip()
        if not description:
            errors.append(f"finding {i}: empty description")
            continue
        parsed_findings.append(
            Finding(category=category, severity=severity, description=description)
        )

    if errors:
        raise ExtractionError("; ".join(errors))

    return SupplierAudit(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        country=country,
        audit_date=audit_date,
        auditor=auditor,
        scheme=scheme,
        overall_score=overall_score,
        findings=tuple(parsed_findings),
    )


def load_ground_truth(path: Path) -> dict[str, dict]:
    """Load a JSON ground-truth file produced by `scripts/generate_samples.py`."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)
