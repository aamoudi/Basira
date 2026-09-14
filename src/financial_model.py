from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def build_financial_model(normalized: dict[str, Any]) -> dict[str, Any]:
    records = normalized.get("records", [])
    if not records:
        raise ValueError("Normalized transaction data contains no records.")

    total_revenue = 0.0
    total_cogs = 0.0
    total_tax = 0.0
    by_period: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "revenue": 0.0,
            "cogs": 0.0,
            "tax": 0.0,
            "row_count": 0,
            "missing_revenue_rows": 0,
            "missing_cogs_rows": 0,
            "missing_tax_rows": 0,
            "invalid_revenue_rows": 0,
            "invalid_cogs_rows": 0,
            "invalid_tax_rows": 0,
        }
    )

    unknown_rows = 0
    for record in records:
        period = None
        date_value = record.get("transaction_date")
        if isinstance(date_value, str) and len(date_value) >= 7:
            period = date_value[:7]
        if not period:
            period = "unknown"
            unknown_rows += 1

        bucket = by_period[period]
        bucket["row_count"] += 1
        quality = record.get("data_quality", {})
        missing = set(quality.get("missing_fields", []))
        invalid = set(quality.get("invalid_fields", []))

        for field, key in (("revenue", "revenue"), ("cogs", "cogs"), ("tax", "tax")):
            value = _to_number(record.get(field))
            if field in missing:
                bucket[f"missing_{field}_rows"] += 1
            elif field in invalid or value is None:
                bucket[f"invalid_{field}_rows"] += 1
            elif value is not None:
                bucket[key] += value
                if field == "revenue":
                    total_revenue += value
                elif field == "cogs":
                    total_cogs += value
                else:
                    total_tax += value

    periods: list[dict[str, Any]] = []
    for period in sorted(by_period):
        bucket = by_period[period]
        gross_profit = bucket["revenue"] - bucket["cogs"]
        gross_margin = gross_profit / bucket["revenue"] if bucket["revenue"] else None
        periods.append(
            {
                "period": period,
                "revenue": _round(bucket["revenue"]),
                "cogs": _round(bucket["cogs"]),
                "gross_profit": _round(gross_profit),
                "gross_margin": _round(gross_margin),
                "tax": _round(bucket["tax"]),
                "row_count": bucket["row_count"],
                "data_quality": {
                    "missing_revenue_rows": bucket["missing_revenue_rows"],
                    "missing_cogs_rows": bucket["missing_cogs_rows"],
                    "missing_tax_rows": bucket["missing_tax_rows"],
                    "invalid_revenue_rows": bucket["invalid_revenue_rows"],
                    "invalid_cogs_rows": bucket["invalid_cogs_rows"],
                    "invalid_tax_rows": bucket["invalid_tax_rows"],
                },
            }
        )

    gross_profit = total_revenue - total_cogs
    gross_margin = gross_profit / total_revenue if total_revenue else None

    return {
        "model": "Basira Standard Financial Model",
        "version": "0.2",
        "currency": "SAR",
        "currency_source": "mvp_default",
        "period_basis": "transaction_date month",
        "metrics": {
            "revenue": {
                "value": _round(total_revenue),
                "source": "normalized_transactions.revenue",
                "status": "calculated_from_normalized_data",
            },
            "cogs": {
                "value": _round(total_cogs),
                "source": "normalized_transactions.cogs",
                "status": "calculated_from_normalized_data",
            },
            "gross_profit": {
                "value": _round(gross_profit),
                "formula": "revenue - cogs",
                "status": "calculated",
            },
            "gross_margin": {
                "value": _round(gross_margin),
                "formula": "gross_profit / revenue",
                "status": "calculated",
            },
            "tax": {
                "value": _round(total_tax),
                "source": "normalized_transactions.tax",
                "status": "calculated_from_normalized_data",
            },
        },
        "periods": periods,
        "data_quality": {
            "source_rows": len(records),
            "rows_with_unknown_period": unknown_rows,
            "normalizer_summary": normalized.get("quality_summary", {}),
        },
        "sources": {
            "normalized_workbook": normalized.get("source", {}).get("workbook"),
            "normalized_sheet": normalized.get("source", {}).get("sheet"),
        },
        "assumptions": [
            "Revenue is not assumed to be tax-inclusive or tax-exclusive beyond the source context.",
            "Tax is reported separately and is not subtracted from Revenue automatically.",
            "Currency is set to SAR as the MVP default for the Saudi/Gulf target market and is not inferred from source data.",
            "Gross profit is calculated as Revenue minus COGS from normalized transaction data.",
        ],
    }
