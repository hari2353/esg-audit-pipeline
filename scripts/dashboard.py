"""Streamlit dashboard: ESG supplier risk & CAPA tracking.

Run with:
    streamlit run scripts/dashboard.py -- data/results.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from esg_pipeline.pipeline import DefaultPortfolioMonitor

st.set_page_config(page_title="ESG Supplier Risk", page_icon="🌍", layout="wide")


@st.cache_data
def load_records(json_path: Path) -> list[dict]:
    with json_path.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    json_arg = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "data/results.json"
    json_path = Path(json_arg)
    if not json_path.exists():
        st.error(f"No results file found at {json_path}. Run scripts/run_pipeline.py first.")
        st.stop()

    st.title("🌍 ESG Supplier Risk & Corrective Actions")
    st.caption(
        "Public portfolio demo - processes synthetic social-compliance audit reports "
        "(a public replica of an enterprise Altria-style ESG solution: Document "
        "Intelligence-style extraction, risk scoring, CAPA tracking, monitoring)."
    )

    raw = load_records(json_path)
    if not raw:
        st.warning("Results file is empty.")
        st.stop()

    summary = DefaultPortfolioMonitor(today=date.today()).summarize_from_dicts(raw)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Suppliers", summary.total_suppliers)
    col2.metric("Portfolio health", summary.health)
    col3.metric("Open CAPAs", summary.open_capas)
    col4.metric("Overdue CAPAs", summary.overdue_capas)
    col5.metric("Failed extractions", summary.failed_extractions)

    st.subheader("Risk band distribution")
    band_df = pd.DataFrame(
        sorted(summary.band_counts.items()), columns=["band", "suppliers"]
    )
    st.plotly_chart(
        px.bar(band_df, x="band", y="suppliers", color="band", text="suppliers"),
        use_container_width=True,
    )

    st.subheader("Top risk categories (open CAPAs)")
    if summary.top_risk_categories:
        cat_df = pd.DataFrame(summary.top_risk_categories, columns=["category", "open_capas"])
        st.plotly_chart(
            px.bar(cat_df, x="category", y="open_capas", color="category"),
            use_container_width=True,
        )
    else:
        st.info("No open corrective actions.")

    st.subheader("Supplier risk table")
    rows = []
    for r in raw:
        rows.append(
            {
                "supplier": r["supplier_id"],
                "name": r["supplier_name"],
                "country": r["country"],
                "audit_score": r["overall_score"],
                "risk_total": r["risk"]["total"],
                "band": r["risk"]["band"],
                "findings": len(r["findings"]),
                "open_capas": sum(
                    1 for c in r["corrective_actions"] if c["status"] == "open"
                ),
            }
        )
    table = pd.DataFrame(rows).sort_values("risk_total", ascending=False)
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("Corrective action detail")
    capas = []
    for r in raw:
        for c in r["corrective_actions"]:
            capas.append(
                {
                    "supplier": r["supplier_id"],
                    "category": c["category"],
                    "severity": c["severity"],
                    "due_date": c["due_date"],
                    "status": c["status"],
                    "action": c["action"],
                }
            )
    if capas:
        capa_df = pd.DataFrame(capas).sort_values("due_date")
        st.dataframe(capa_df, use_container_width=True, hide_index=True)
    else:
        st.info("No corrective actions were raised.")


if __name__ == "__main__":
    main()
