"""Shared pytest fixtures for the ESG pipeline test suite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from esg_pipeline.models import Finding, FindingSeverity, SupplierAudit


@pytest.fixture
def clean_audit() -> SupplierAudit:
    return SupplierAudit(
        supplier_id="SUP-0001",
        supplier_name="Meridian Textiles Ltd.",
        country="India",
        audit_date=date(2025, 3, 14),
        auditor="SGS",
        scheme="SMETA",
        overall_score=95,
        findings=(),
    )


@pytest.fixture
def poor_audit() -> SupplierAudit:
    return SupplierAudit(
        supplier_id="SUP-0002",
        supplier_name="Apex Apparels Pvt. Ltd.",
        country="Bangladesh",
        audit_date=date(2025, 1, 20),
        auditor="Intertek",
        scheme="BSCI",
        overall_score=48,
        findings=(
            Finding("child_labor", FindingSeverity.CRITICAL, "Two underage workers found."),
            Finding("health_safety", FindingSeverity.MAJOR, "Blocked emergency exit."),
            Finding("working_hours", FindingSeverity.MINOR, "Overtime records incomplete."),
        ),
    )


@pytest.fixture
def pdf_dir(tmp_path: Path):
    """Generate 6 deterministic synthetic PDFs and return the directory."""
    from esg_pipeline.synthetic import generate_audit_pdfs

    generate_audit_pdfs(tmp_path / "samples", count=6, seed=7)
    return tmp_path / "samples"
