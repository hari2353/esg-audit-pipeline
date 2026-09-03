"""CI verification: end-to-end pipeline output must be complete and valid."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    results = json.loads(Path("data/results.json").read_text(encoding="utf-8"))
    assert len(results) == 5, f"expected 5 results, got {len(results)}"
    bands = {"low", "medium", "high", "critical"}
    assert all(r["risk"]["band"] in bands for r in results), "invalid risk band found"
    assert all(r["supplier_id"] for r in results), "missing supplier id"
    print("end-to-end check passed:", len(results), "audits processed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
