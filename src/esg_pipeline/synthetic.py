"""Deterministic synthetic supplier-audit PDF generator.

Produces realistic social-compliance audit reports so the pipeline can be
demoed and tested end-to-end without access to confidential supplier data.
The generator is seeded, so the same input always yields byte-identical PDFs
(reproducible builds, deterministic CI).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from esg_pipeline.models import (
    FINDING_CATEGORIES,
    Finding,
    FindingSeverity,
    SupplierAudit,
)

_AUDIT_SCHEMES = ("SMETA", "SA8000", "BSCI", "WRAP", "SLCP")
_COUNTRIES = (
    "India", "Vietnam", "Bangladesh", "China", "Turkey",
    "Mexico", "Portugal", "Indonesia", "Cambodia", "Sri Lanka",
)
_COMPANY_PREFIXES = (
    "Meridian", "Apex", "Sunrise", "Global", "Pinnacle", "Orbit",
    "Vertex", "Crestline", "Northbridge", "Silverstone",
)
_COMPANY_SUFFIXES = ("Textiles", "Apparels", "Manufacturing", "Industries", "Garments")
_COMPANY_FORMS = ("Ltd.", "Inc.", "Co.", "Pvt. Ltd.", "Corp.")
_AUDITORS = (
    "SGS", "Intertek", "TUV Rheinland", "Bureau Veritas", "UL Solutions", "Elevate",
)

_SEVERITY_WEIGHTS = (
    (FindingSeverity.MINOR, 0.55),
    (FindingSeverity.MAJOR, 0.33),
    (FindingSeverity.CRITICAL, 0.12),
)

_FINDING_TEMPLATES: dict[str, dict[FindingSeverity, str]] = {
    "child_labor": {
        FindingSeverity.MINOR: "Age-verification records incomplete for 2 seasonal workers.",
        FindingSeverity.MAJOR: "One worker under legal age found in packaging area.",
        FindingSeverity.CRITICAL: "Two workers below legal employment age on production floor.",
    },
    "forced_labor": {
        FindingSeverity.MINOR: "Personal documents held for 3 workers pending renewal.",
        FindingSeverity.MAJOR: "Recruitment fees charged to migrant workers, partially repaid.",
        FindingSeverity.CRITICAL: "Passports confiscated; workers cannot leave freely.",
    },
    "health_safety": {
        FindingSeverity.MINOR: "Two fire extinguishers past inspection date.",
        FindingSeverity.MAJOR: "Blocked emergency exit on the second floor of the weaving unit.",
        FindingSeverity.CRITICAL: "No functioning fire alarm in the worker dormitory building.",
    },
    "working_hours": {
        FindingSeverity.MINOR: "Overtime records exceeded 4 hours/week for 8 workers in one month.",
        FindingSeverity.MAJOR: "Mandatory overtime above legal limits during peak season.",
        FindingSeverity.CRITICAL: "Workers reported 84-hour weeks for two consecutive months.",
    },
    "wages_benefits": {
        FindingSeverity.MINOR: "Payslips missing itemized overtime breakdown.",
        FindingSeverity.MAJOR: "Wages paid below legal minimum for 12 workers.",
        FindingSeverity.CRITICAL: "Systematic underpayment of wages across the cutting department.",
    },
    "discrimination": {
        FindingSeverity.MINOR: "No documented anti-discrimination policy on site.",
        FindingSeverity.MAJOR: "Gender-based pay gap unexplained for equivalent roles.",
        FindingSeverity.CRITICAL: "Pregnancy testing required as condition of employment.",
    },
    "disciplinary_practices": {
        FindingSeverity.MINOR: "Verbal warning records lack employee signature.",
        FindingSeverity.MAJOR: "Monetary fines used as a disciplinary measure.",
        FindingSeverity.CRITICAL: "Physical discipline incident reported by two workers.",
    },
    "freedom_of_association": {
        FindingSeverity.MINOR: "No worker committee established for grievances.",
        FindingSeverity.MAJOR: "Union meeting requests denied by management twice.",
        FindingSeverity.CRITICAL: "Union organizers dismissed without cause.",
    },
    "environment": {
        FindingSeverity.MINOR: "Waste segregation labels missing in the canteen area.",
        FindingSeverity.MAJOR: "Wastewater discharged without required permit.",
        FindingSeverity.CRITICAL: "Hazardous chemicals stored above ground near the water table.",
    },
}

assert set(_FINDING_TEMPLATES) == set(FINDING_CATEGORIES)


@dataclass(frozen=True, slots=True)
class SyntheticProfile:
    """A fully specified synthetic audit, so PDF text and ground truth agree."""

    audit: SupplierAudit
    narrative_seed: int

    @property
    def filename(self) -> str:
        return f"{self.audit.supplier_id}.pdf"


def _supplier_name(rng: random.Random) -> str:
    return (
        f"{rng.choice(_COMPANY_PREFIXES)} {rng.choice(_COMPANY_SUFFIXES)} "
        f"{rng.choice(_COMPANY_FORMS)}"
    )


def _supplier_id(rng: random.Random, index: int) -> str:
    return f"SUP-{rng.randrange(10, 100):02d}{index:04d}"


def _pick_severity(rng: random.Random) -> FindingSeverity:
    roll = rng.random()
    cumulative = 0.0
    for severity, weight in _SEVERITY_WEIGHTS:
        cumulative += weight
        if roll < cumulative:
            return severity
    return FindingSeverity.MINOR


def _findings_for_score(
    rng: random.Random, overall_score: int
) -> tuple[Finding, ...]:
    """Generate findings consistent with the target overall score.

    High-scoring audits get few/minor findings; low-scoring audits get more
    and worse findings. Zero-tolerance categories are rare and drive the
    score down hard, mirroring real social-compliance schemes.
    """
    if overall_score >= 90:
        n_findings = rng.randrange(0, 2)
    elif overall_score >= 75:
        n_findings = rng.randrange(1, 3)
    elif overall_score >= 55:
        n_findings = rng.randrange(2, 5)
    else:
        n_findings = rng.randrange(3, 7)

    findings: list[Finding] = []
    used_categories: set[str] = set()

    # Zero-tolerance (child/forced labor) occasionally appears in poor audits.
    if overall_score < 70 and rng.random() < 0.35:
        category = rng.choice(("child_labor", "forced_labor"))
        findings.append(
            Finding(
                category=category,
                severity=_pick_severity(rng),
                description=_FINDING_TEMPLATES[category][FindingSeverity.MAJOR],
            )
        )
        used_categories.add(category)

    safe_categories = [c for c in FINDING_CATEGORIES if c not in used_categories]
    rng.shuffle(safe_categories)
    for category in safe_categories[: max(n_findings - len(findings), 0)]:
        severity = _pick_severity(rng)
        if severity is FindingSeverity.CRITICAL and overall_score >= 75:
            severity = FindingSeverity.MAJOR  # keep score/findings consistent
        findings.append(
            Finding(
                category=category,
                severity=severity,
                description=_FINDING_TEMPLATES[category][severity],
            )
        )

    findings.sort(key=lambda f: (f.severity.value, f.category))
    return tuple(findings)


def generate_profile(
    index: int, *, seed: int = 42, audit_date: date | None = None
) -> SyntheticProfile:
    """Create one deterministic synthetic audit profile."""
    rng = random.Random(f"audit-{seed}-{index}")
    overall_score = rng.randrange(38, 99)
    findings = _findings_for_score(rng, overall_score)
    if audit_date is None:
        audit_date = date(
            rng.randrange(2019, 2026), rng.randrange(1, 13), rng.randrange(1, 28)
        )
    audit = SupplierAudit(
        supplier_id=_supplier_id(rng, index),
        supplier_name=_supplier_name(rng),
        country=rng.choice(_COUNTRIES),
        audit_date=audit_date,
        auditor=rng.choice(_AUDITORS),
        scheme=rng.choice(_AUDIT_SCHEMES),
        overall_score=overall_score,
        findings=findings,
    )
    return SyntheticProfile(audit=audit, narrative_seed=rng.randrange(0, 2**31))


def _severities_table(findings: tuple[Finding, ...]) -> Table:
    header = ["#", "Category", "Severity", "Description"]
    rows = [
        [
            str(i + 1),
            f.category.replace("_", " ").title(),
            f.severity.value.title(),
            f.description,
        ]
        for i, f in enumerate(findings)
    ] or [["-", "None identified", "-", "No non-compliances recorded."]]

    table = Table([header, *rows], colWidths=[0.3 * inch, 1.5 * inch, 0.9 * inch, 3.6 * inch])
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
        ])
    )
    return table


def render_audit_pdf(profile: SyntheticProfile, out_path: Path) -> Path:
    """Render one synthetic audit profile to a realistic-looking PDF."""
    audit = profile.audit
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontSize=16,
            alignment=TA_CENTER,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHead",
            parent=styles["Heading2"],
            fontSize=11,
            spaceBefore=12,
            spaceAfter=4,
        )
    )

    story: list[object] = [
        Paragraph("SOCIAL COMPLIANCE AUDIT REPORT", styles["ReportTitle"]),
        Paragraph(f"Audit Scheme: {audit.scheme}", styles["Normal"]),
        Spacer(1, 10),
    ]

    summary_rows = [
        ["Supplier ID:", audit.supplier_id],
        ["Supplier Name:", audit.supplier_name],
        ["Country:", audit.country],
        ["Audit Date:", audit.audit_date.isoformat()],
        ["Auditing Body:", audit.auditor],
        ["Overall Score:", str(audit.overall_score)],
    ]
    summary = Table(summary_rows, colWidths=[1.6 * inch, 4.7 * inch])
    summary.setStyle(
        TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.append(summary)
    story.append(Paragraph("Summary of Findings", styles["SectionHead"]))
    story.append(_severities_table(audit.findings))
    footer = "Report generated for demonstration purposes with synthetic data."
    story.append(Paragraph(footer, styles["Italic"]))

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        title=f"Social Compliance Audit - {audit.supplier_id}",
        author=audit.auditor,
        invariant=1,  # deterministic output: no embedded timestamp/ID
    )
    doc.build(story)
    return out_path


def generate_audit_pdfs(
    out_dir: Path,
    *,
    count: int = 12,
    seed: int = 42,
    audit_date: date | None = None,
) -> list[Path]:
    """Generate `count` deterministic audit PDFs into `out_dir`."""
    if count < 1:
        msg = "count must be >= 1"
        raise ValueError(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        profile = generate_profile(index, seed=seed, audit_date=audit_date)
        paths.append(render_audit_pdf(profile, out_dir / profile.filename))
    return paths
