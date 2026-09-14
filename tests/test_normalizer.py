import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from normalizer import build_normalized_transactions


def test_normalizer_maps_and_converts_rows():
    mapping = [
        {"source_column": "Date", "semantic_field": "Transaction Date", "confidence": 1.0, "status": "accepted"},
        {"source_column": "Customer", "semantic_field": "Customer", "confidence": 1.0, "status": "accepted"},
        {"source_column": "Quantity", "semantic_field": "Quantity", "confidence": 1.0, "status": "accepted"},
        {"source_column": "SalesValue", "semantic_field": "Revenue", "confidence": 0.95, "status": "accepted"},
        {"source_column": "ProductCost", "semantic_field": "COGS", "confidence": 0.95, "status": "accepted"},
        {"source_column": "VAT", "semantic_field": "Tax", "confidence": 1.0, "status": "accepted"},
        {"source_column": "PaymentStatus", "semantic_field": "Payment Status", "confidence": 1.0, "status": "accepted"},
    ]
    rows = [{
        "Date": "2026-01-05",
        "Customer": "ABC Corp",
        "Quantity": "2",
        "SalesValue": "2,000",
        "ProductCost": 1400,
        "VAT": None,
        "PaymentStatus": "Paid",
    }]
    result = build_normalized_transactions(rows, mapping)
    record = result["records"][0]
    assert record["transaction_date"] == "2026-01-05"
    assert record["quantity"] == 2
    assert record["revenue"] == 2000
    assert record["cogs"] == 1400
    assert record["tax"] is None
    assert "tax" in record["data_quality"]["missing_fields"]


def test_normalizer_flags_bad_values():
    mapping = [
        {"source_column": "Date", "semantic_field": "Transaction Date", "confidence": 1.0, "status": "accepted"},
        {"source_column": "Qty", "semantic_field": "Quantity", "confidence": 1.0, "status": "accepted"},
        {"source_column": "Revenue", "semantic_field": "Revenue", "confidence": 1.0, "status": "accepted"},
    ]
    rows = [{"Date": "not-a-date", "Qty": -2, "Revenue": "abc"}]
    result = build_normalized_transactions(rows, mapping)
    quality = result["records"][0]["data_quality"]
    assert "transaction_date" in quality["invalid_fields"]
    assert "quantity" in quality["invalid_fields"]
    assert "revenue" in quality["invalid_fields"]
