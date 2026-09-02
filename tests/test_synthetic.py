"""Unit tests for the synthetic audit PDF generator."""

from __future__ import annotations

from pathlib import Path


class TestDeterminism:
    def test_same_seed_same_profile(self) -> None:
        from esg_pipeline.synthetic import generate_profile

        a = generate_profile(3, seed=42)
        b = generate_profile(3, seed=42)
        assert a == b

    def test_different_seed_different_profile(self) -> None:
        from esg_pipeline.synthetic import generate_profile

        a = generate_profile(3, seed=42)
        b = generate_profile(3, seed=99)
        assert (
            a.audit.supplier_name != b.audit.supplier_name
            or a.audit.overall_score != b.audit.overall_score
        )

    def test_generated_pdfs_are_deterministic(self, tmp_path: Path) -> None:
        from esg_pipeline.synthetic import generate_audit_pdfs

        first = generate_audit_pdfs(tmp_path / "a", count=4, seed=42)
        second = generate_audit_pdfs(tmp_path / "b", count=4, seed=42)
        assert len(first) == len(second) == 4
        for p, q in zip(first, second, strict=True):
            assert p.name == q.name
            assert p.read_bytes() == q.read_bytes()


class TestGeneration:
    def test_creates_pdfs(self, tmp_path: Path) -> None:
        from esg_pipeline.synthetic import generate_audit_pdfs

        paths = generate_audit_pdfs(tmp_path, count=3, seed=1)
        assert len(paths) == 3
        for path in paths:
            assert path.suffix == ".pdf"
            assert path.stat().st_size > 1000

    def test_filename_matches_supplier_id(self, tmp_path: Path) -> None:
        from esg_pipeline.synthetic import generate_audit_pdfs

        paths = generate_audit_pdfs(tmp_path, count=2, seed=1)
        for path in paths:
            assert path.stem.startswith("SUP-")

    def test_zero_count_rejected(self, tmp_path: Path) -> None:
        import pytest

        from esg_pipeline.synthetic import generate_audit_pdfs

        with pytest.raises(ValueError, match="count"):
            generate_audit_pdfs(tmp_path, count=0)

    def test_scores_consistent_with_findings(self, tmp_path: Path) -> None:
        """High-scoring audits must not contain critical findings."""
        from esg_pipeline.models import FindingSeverity
        from esg_pipeline.synthetic import generate_profile

        for idx in range(30):
            profile = generate_profile(idx, seed=123)
            if profile.audit.overall_score >= 90:
                assert all(
                    f.severity is not FindingSeverity.CRITICAL
                    for f in profile.audit.findings
                ), f"idx={idx} score={profile.audit.overall_score}"
