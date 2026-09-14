import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from financial_model import build_financial_model


def test_financial_model_from_normalized_data():
    normalized = {
        "records": [
            {"transaction_date": "2026-01-05", "revenue": 5000, "cogs": 3800, "tax": 750, "data_quality": {}},
            {"transaction_date": "2026-01-07", "revenue": 2800, "cogs": 2100, "tax": 420, "data_quality": {}},
            {"transaction_date": "2026-02-02", "revenue": 1800, "cogs": 1200, "tax": 270, "data_quality": {}},
        ],
        "quality_summary": {},
        "source": {"workbook": "test.xlsx", "sheet": "Transactions"},
    }
    result = build_financial_model(normalized)
    assert result["metrics"]["revenue"]["value"] == 9600.0
    assert result["metrics"]["cogs"]["value"] == 7100.0
    assert result["metrics"]["gross_profit"]["value"] == 2500.0
    assert result["metrics"]["gross_margin"]["value"] == 0.26
    assert result["metrics"]["tax"]["value"] == 1440.0
    assert result["currency"] == "SAR"
    assert result["currency_source"] == "mvp_default"
    assert result["periods"][0]["period"] == "2026-01"
    assert result["periods"][1]["period"] == "2026-02"
