"""Unit tests for extraction: field parsing, finding rows, normalization."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from esg_pipeline.extraction import (
    ExtractionError,
    RawExtraction,
    _parse_finding_rows,
    _parse_labeled_fields,
    normalize_extraction,
)
from esg_pipeline.models import Finding, FindingSeverity


class TestLabeledFieldParsing:
    def test_inline_value(self) -> None:
        fields = _parse_labeled_fields(["Supplier ID: SUP-1234", "Country: India"])
        assert fields == {"Supplier ID": "SUP-1234", "Country": "India"}

    def test_value_on_next_line(self) -> None:
        lines = ["Supplier ID:", "SUP-1234", "Audit Date:", "2025-03-14"]
        fields = _parse_labeled_fields(lines)
        assert fields["Supplier ID"] == "SUP-1234"
        assert fields["Audit Date"] == "2025-03-14"

    def test_first_label_wins_on_duplicates(self) -> None:
        fields = _parse_labeled_fields(["Country: India", "Country: China"])
        assert fields["Country"] == "India"

    def test_plain_lines_ignored(self) -> None:
        fields = _parse_labeled_fields(["SOCIAL COMPLIANCE AUDIT REPORT", "no colon line"])
        assert fields == {}


class TestFindingRowParsing:
    def _lines(self, *rows: str) -> list[str]:
        base = ["Summary of Findings", "#", "Category", "Severity", "Description"]
        return base + list(rows)

    def test_simple_row(self) -> None:
        rows = self._lines("1", "Health and Safety", "Major", "Blocked exit.")
        findings = _parse_finding_rows(rows)
        assert findings == [
            {"category": "health_safety", "severity": "major", "description": "Blocked exit."}
        ]

    def test_no_findings_placeholder(self) -> None:
        rows = self._lines("-", "None identified", "-", "No non-compliances recorded.")
        assert _parse_finding_rows(rows) == []

    def test_wrapped_description(self) -> None:
        rows = self._lines(
            "2", "Working Hours", "Minor",
            "Overtime records exceeded", "4 hours/week for 8 workers.",
        )
        findings = _parse_finding_rows(rows)
        assert len(findings) == 1
        assert findings[0]["description"] == (
            "Overtime records exceeded 4 hours/week for 8 workers."
        )

    def test_multiple_rows(self) -> None:
        rows = self._lines(
            "1", "Environment", "Minor", "Waste labels missing.",
            "2", "Wages and Benefits", "Critical", "Systematic underpayment.",
        )
        findings = _parse_finding_rows(rows)
        assert [f["category"] for f in findings] == ["environment", "wages_benefits"]
        assert [f["severity"] for f in findings] == ["minor", "critical"]

    def test_footer_stops_parsing(self) -> None:
        rows = self._lines(
            "1", "Environment", "Minor", "Waste labels missing.",
            "Report generated for demonstration purposes.",
            "3", "Discrimination", "Minor", "Should not be parsed.",
        )
        findings = _parse_finding_rows(rows)
        assert len(findings) == 1

    def test_missing_header_yields_nothing(self) -> None:
        assert _parse_finding_rows(["No table here"]) == []


class TestNormalizeExtraction:
    def _raw(self, **overrides: str) -> RawExtraction:
        fields = {
            "Supplier ID": "SUP-01",
            "Supplier Name": "Meridian Textiles Ltd.",
            "Country": "India",
            "Audit Date": "2025-03-14",
            "Auditing Body": "SGS",
            "Audit Scheme": "SMETA",
            "Overall Score": "82",
        }
        fields.update(overrides)
        return RawExtraction(fields=fields)

    def test_full_normalization(self) -> None:
        raw = self._raw()
        audit = normalize_extraction(raw)
        assert audit.supplier_id == "SUP-01"
        assert audit.supplier_name == "Meridian Textiles Ltd."
        assert audit.country == "India"
        assert audit.audit_date == date(2025, 3, 14)
        assert audit.auditor == "SGS"
        assert audit.scheme == "SMETA"
        assert audit.overall_score == 82
        assert audit.findings == ()

    def test_all_missing_fields_reported_at_once(self) -> None:
        with pytest.raises(ExtractionError) as excinfo:
            normalize_extraction(RawExtraction(fields={}))
        message = str(excinfo.value)
        for field in ("Supplier ID", "Country", "Audit Date", "Overall Score"):
            assert field in message, f"{field} not reported: {message}"

    def test_invalid_date_reported(self) -> None:
        with pytest.raises(ExtractionError, match="invalid Audit Date"):
            normalize_extraction(self._raw(**{"Audit Date": "14/03/2025"}))

    def test_invalid_score_reported(self) -> None:
        with pytest.raises(ExtractionError, match="invalid Overall Score"):
            normalize_extraction(self._raw(**{"Overall Score": "N/A"}))

    def test_findings_parsed(self) -> None:
        raw = self._raw()
        raw = RawExtraction(
            fields=raw.fields,
            findings=(
                {
                    "category": "health_safety",
                    "severity": "major",
                    "description": "Blocked exit.",
                },
                {
                    "category": "child_labor",
                    "severity": "critical",
                    "description": "Underage workers.",
                },
            ),
        )
        audit = normalize_extraction(raw)
        assert audit.findings == (
            Finding("health_safety", FindingSeverity.MAJOR, "Blocked exit."),
            Finding("child_labor", FindingSeverity.CRITICAL, "Underage workers."),
        )

    def test_bad_finding_severity_reported(self) -> None:
        raw = RawExtraction(
            fields=self._raw().fields,
            findings=(
                {"category": "environment", "severity": "catastrophic", "description": "x"},
            ),
        )
        with pytest.raises(ExtractionError, match="invalid severity"):
            normalize_extraction(raw)

    def test_out_of_range_score_rejected_by_model(self) -> None:
        # A score of 150 parses as an int but violates the model contract;
        # it must surface as a clear error, never be silently clamped.
        with pytest.raises((ExtractionError, ValueError)):
            normalize_extraction(self._raw(**{"Overall Score": "150"}))


class TestPyPdfExtractorErrors:
    def test_missing_file_raises_extraction_error(self, tmp_path: Path) -> None:
        from esg_pipeline.extraction import PyPdfExtractor

        with pytest.raises(ExtractionError, match="failed to read PDF"):
            PyPdfExtractor().extract(tmp_path / "nope.pdf")

    def test_non_pdf_raises_extraction_error(self, tmp_path: Path) -> None:
        from esg_pipeline.extraction import PyPdfExtractor

        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"this is not a pdf")
        with pytest.raises(ExtractionError, match="failed to read PDF"):
            PyPdfExtractor().extract(bad)


class TestPdfRoundTrip:
    def test_extracted_audit_equals_ground_truth(self, pdf_dir: Path) -> None:
        from esg_pipeline.extraction import PyPdfExtractor
        from esg_pipeline.synthetic import generate_profile

        profiles = {
            generate_profile(idx, seed=7).filename: generate_profile(idx, seed=7)
            for idx in range(6)
        }
        extractor = PyPdfExtractor()
        for path in sorted(pdf_dir.glob("*.pdf")):
            assert path.name in profiles, f"unexpected file {path.name}"
            raw = extractor.extract(path)
            audit = normalize_extraction(raw)
            assert audit == profiles[path.name].audit, f"round-trip mismatch for {path.name}"
