from datetime import date

from esg_pipeline.evaluation import evaluate_findings
from esg_pipeline.models import Finding, FindingSeverity, SupplierAudit


def _audit(scheme: str, findings: tuple[Finding, ...], country: str = "IN") -> SupplierAudit:
    return SupplierAudit(
        supplier_id=f"s-{scheme}",
        supplier_name="Supplier",
        country=country,
        audit_date=date(2026, 1, 1),
        auditor="Auditor",
        scheme=scheme,
        overall_score=80,
        findings=findings,
    )


CHILD = Finding("child_labor", FindingSeverity.CRITICAL, "child labor found")
WAGE = Finding("wages_benefits", FindingSeverity.MAJOR, "unpaid overtime")


def test_perfect_predictions_score_one():
    gold = _audit("SMETA", (CHILD, WAGE))
    out = evaluate_findings([(gold, gold)], min_support=1)
    assert out["micro"].precision == 1.0
    assert out["micro"].recall == 1.0
    assert out["micro"].f1 == 1.0
    assert out["macro_f1"] == 1.0


def test_false_positive_and_false_negative_math():
    gold = _audit("SMETA", (CHILD,))
    predicted = _audit("SMETA", (WAGE,))
    out = evaluate_findings([(gold, predicted)], min_support=1)
    micro = out["micro"]
    assert (micro.tp, micro.fp, micro.fn) == (0, 1, 1)
    assert micro.f1 == 0.0


def test_slices_are_scored_separately_and_sorted():
    good = _audit("SMETA", (CHILD, WAGE))
    bad_gold = _audit("BSCI", (CHILD,), country="BD")
    bad_pred = _audit("BSCI", (), country="BD")
    out = evaluate_findings([(good, good), (bad_gold, bad_pred)], min_support=1)
    assert list(out["slices"]) == ["BSCI", "SMETA"]
    assert out["slices"]["SMETA"].f1 == 1.0
    assert out["slices"]["BSCI"].f1 == 0.0
    assert out["macro_f1"] == 0.5
    assert out["low_support_slices"] == []


def test_low_support_slices_are_excluded_from_macro_but_listed():
    big = _audit("SMETA", (CHILD, WAGE))
    small_gold = _audit("BSCI", (CHILD,))
    small_pred = _audit("BSCI", ())
    out = evaluate_findings(
        [(big, big), (small_gold, small_pred)],
        min_support=2,
    )
    assert out["low_support_slices"] == ["BSCI"]
    assert out["macro_f1"] == 1.0


def test_slice_by_country():
    a = _audit("SMETA", (CHILD,), country="IN")
    b = _audit("SMETA", (WAGE,), country="BD")
    out = evaluate_findings([(a, a), (b, b)], slice_by="country", min_support=1)
    assert list(out["slices"]) == ["BD", "IN"]
    assert out["macro_f1"] == 1.0


def test_invalid_inputs_rejected():
    gold = _audit("SMETA", ())
    for kwargs in (
        {"cases": [], "slice_by": "scheme", "min_support": 1},
        {"cases": [(gold, gold)], "slice_by": "supplier_name", "min_support": 1},
        {"cases": [(gold, gold)], "slice_by": "scheme", "min_support": 0},
    ):
        cases = kwargs.pop("cases")
        try:
            evaluate_findings(cases, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid input: {kwargs}")
