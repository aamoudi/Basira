import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insights_engine import build_insight_context


def sample_normalized():
    return {
        "records": [
            {"transaction_date": "2026-01-05", "customer": "A", "product": "P1", "revenue": 1000, "payment_status": "Paid"},
            {"transaction_date": "2026-02-05", "customer": "A", "product": "P1", "revenue": 2000, "payment_status": "Unpaid"},
            {"transaction_date": "2026-02-06", "customer": "B", "product": "P2", "revenue": 500, "payment_status": "Paid"},
        ],
        "source": {"workbook": "x.xlsx", "sheet": "Transactions"},
    }


def sample_model():
    return {
        "version": "0.2",
        "metrics": {
            "revenue": {"value": 3500},
            "cogs": {"value": 2000},
            "gross_profit": {"value": 1500},
            "gross_margin": {"value": 0.4286},
            "tax": {"value": 525},
        },
        "periods": [
            {"period": "2026-01", "revenue": 1000, "cogs": 600, "gross_profit": 400, "gross_margin": 0.4, "tax": 150},
            {"period": "2026-02", "revenue": 2500, "cogs": 1400, "gross_profit": 1100, "gross_margin": 0.44, "tax": 375},
        ],
        "data_quality": {"source_rows": 3},
    }


def test_context_is_deterministic_and_grounded():
    context = build_insight_context(sample_model(), sample_normalized())
    assert context["metrics"]["revenue"] == 3500.0
    assert context["period_comparison"]["available"] is True
    assert context["data_sufficiency_check"]["trend_analysis"] == "unavailable"
    assert context["data_sufficiency_check"]["confidence"] == "insufficient"
    assert context["period_comparison"]["revenue"]["current"] == 2500.0
    assert context["unpaid_or_overdue_transaction_count"] == 1
    assert context["customer_rank"][0]["customer"] == "A"
    assert context["product_rank"][0]["product"] == "P1"
