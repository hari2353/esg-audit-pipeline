"""Generate deterministic synthetic audit PDFs (and ground truth JSON).

Usage:
    python scripts/generate_samples.py --count 12 --out data/samples --seed 42
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from esg_pipeline.synthetic import generate_audit_pdfs, generate_profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--out", type=Path, default=Path("data/samples"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ground-truth", type=Path, default=None,
                        help="also write ground-truth JSON to this path")
    args = parser.parse_args()

    paths = generate_audit_pdfs(args.out, count=args.count, seed=args.seed)
    print(f"Generated {len(paths)} audit PDFs in {args.out}")

    if args.ground_truth:
        truth = {}
        for index in range(args.count):
            profile = generate_profile(index, seed=args.seed)
            truth[profile.filename] = {
                "supplier_id": profile.audit.supplier_id,
                "supplier_name": profile.audit.supplier_name,
                "country": profile.audit.country,
                "audit_date": profile.audit.audit_date.isoformat(),
                "auditor": profile.audit.auditor,
                "scheme": profile.audit.scheme,
                "overall_score": profile.audit.overall_score,
                "findings": [
                    {
                        "category": f.category,
                        "severity": f.severity.value,
                        "description": f.description,
                    }
                    for f in profile.audit.findings
                ],
            }
        args.ground_truth.parent.mkdir(parents=True, exist_ok=True)
        with args.ground_truth.open("w", encoding="utf-8") as f:
            json.dump(truth, f, indent=2)
        print(f"Wrote ground truth to {args.ground_truth}")


if __name__ == "__main__":
    main()
