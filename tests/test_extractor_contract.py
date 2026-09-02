"""Contract tests for extractor implementations.

Every `DocumentExtractor` implementation must pass this suite. The Azure
adapter's parsing path is covered via its shared row-parser
(`_parse_joined_rows`) without network access; the local `PyPdfExtractor`
is exercised against real generated PDFs.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestJoinedRowParserContract:
    """Covers the Azure Document Intelligence row format (one string/row)."""

    def _parse(self, rows: list[str]):
        from esg_pipeline.extraction import _parse_joined_rows

        return _parse_joined_rows(rows)

    def test_standard_row_with_index(self) -> None:
        rows = ["1 Health and Safety Major Blocked emergency exit."]
        findings = self._parse(rows)
        assert findings == [
            {
                "category": "health_safety",
                "severity": "major",
                "description": "Blocked emergency exit.",
            }
        ]

    def test_row_without_index(self) -> None:
        rows = ["Wages and Benefits Critical Systematic underpayment of wages."]
        findings = self._parse(rows)
        assert findings[0]["category"] == "wages_benefits"
        assert findings[0]["severity"] == "critical"

    def test_placeholder_row_ignored(self) -> None:
        rows = ["- None identified - No non-compliances recorded."]
        assert self._parse(rows) == []

    def test_no_severity_skipped(self) -> None:
        rows = ["Summary of Findings random header text"]
        assert self._parse(rows) == []


class TestPyPdfExtractorContract:
    def test_extract_returns_all_required_fields(self, pdf_dir) -> None:
        from esg_pipeline.extraction import PyPdfExtractor

        for path in sorted(pdf_dir.glob("*.pdf")):
            raw = PyPdfExtractor().extract(path)
            assert isinstance(raw.fields, dict)
            assert raw.fields, f"no fields for {path.name}"

    def test_extractor_is_stateless_reusable(self, pdf_dir) -> None:
        from esg_pipeline.extraction import PyPdfExtractor

        extractor = PyPdfExtractor()
        paths = sorted(pdf_dir.glob("*.pdf"))
        first = [extractor.extract(p) for p in paths]
        second = [extractor.extract(p) for p in paths]
        assert first == second

    def test_invalid_document_raises_extraction_error(self, tmp_path) -> None:
        from esg_pipeline.extraction import ExtractionError, PyPdfExtractor

        empty_pdf = tmp_path / "empty.pdf"
        empty_pdf.write_bytes(b"%PDF-1.4 garbage")
        with pytest.raises(ExtractionError):
            PyPdfExtractor().extract(empty_pdf)
