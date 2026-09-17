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


def _detect_currency(normalized: dict[str, Any]) -> str:
    """Detect a human-readable Arabic currency from mapped monetary headers."""
    mapping = normalized.get("mapping", {})
    headers = [
        str(info.get("source_column", ""))
        for key, info in mapping.items()
        if key in {"revenue", "cogs", "tax", "operating_expense"}
    ]
    text = " ".join(headers).lower()

    if any(token in text for token in ("$", "usd", "dollar", "دولار")):
        return "دولار أمريكي"
    if any(token in text for token in ("€", "eur", "euro", "يورو")):
        return "يورو"
    if any(token in text for token in ("£", "gbp", "pound", "جنيه")):
        return "جنيه إسترليني"
    if any(token in text for token in ("ر.س", "sar", "ريال")):
        return "ريال سعودي"
    if any(token in text for token in ("د.إ", "aed", "درهم")):
        return "درهم إماراتي"
    if any(token in text for token in ("د.ك", "kwd", "دينار كويتي")):
        return "دينار كويتي"
    if any(token in text for token in ("د.ب", "bhd", "دينار بحريني")):
        return "دينار بحريني"
    if any(token in text for token in ("ر.ق", "qar", "ريال قطري")):
        return "ريال قطري"
    if any(token in text for token in ("د.أ", "jod", "دينار أردني")):
        return "دينار أردني"
    return "ريال سعودي"


def build_financial_model(normalized: dict[str, Any]) -> dict[str, Any]:
    records = normalized.get("records", [])
    if not records:
        raise ValueError("Normalized transaction data contains no records.")

    total_revenue = 0.0
    total_cogs = 0.0
    total_tax = 0.0
    total_operating_expense = 0.0
    has_any_cogs = False

    by_period: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "revenue": 0.0,
            "cogs": 0.0,
            "tax": 0.0,
            "operating_expense": 0.0,
            "has_cogs": False,
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
        date_value = record.get("transaction_date")
        period = date_value[:7] if isinstance(date_value, str) and len(date_value) >= 7 else None
        if not period:
            period = "unknown"
            unknown_rows += 1

        bucket = by_period[period]
        bucket["row_count"] += 1
        quality = record.get("data_quality", {})
        missing = set(quality.get("missing_fields", []))
        invalid = set(quality.get("invalid_fields", []))

        for field in ("revenue", "cogs", "tax"):
            value = _to_number(record.get(field))
            if field in missing:
                bucket[f"missing_{field}_rows"] += 1
            elif field in invalid or value is None:
                bucket[f"invalid_{field}_rows"] += 1
            else:
                bucket[field] += value
                if field == "revenue":
                    total_revenue += value
                elif field == "cogs":
                    total_cogs += value
                    bucket["has_cogs"] = True
                    has_any_cogs = True
                else:
                    total_tax += value

        operating_expense = _to_number(record.get("operating_expense"))
        if operating_expense is not None:
            bucket["operating_expense"] += operating_expense
            total_operating_expense += operating_expense

    periods: list[dict[str, Any]] = []
    for period in sorted(by_period):
        bucket = by_period[period]
        if bucket["has_cogs"]:
            gross_profit = bucket["revenue"] - bucket["cogs"]
            gross_margin = gross_profit / bucket["revenue"] if bucket["revenue"] else None
        else:
            gross_profit = None
            gross_margin = None

        periods.append({
            "period": period,
            "revenue": _round(bucket["revenue"]),
            "cogs": _round(bucket["cogs"]) if bucket["has_cogs"] else None,
            "operating_expense": _round(bucket["operating_expense"]) if bucket["operating_expense"] else None,
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
        })

    if has_any_cogs:
        gross_profit = total_revenue - total_cogs
        gross_margin = gross_profit / total_revenue if total_revenue else None
    else:
        gross_profit = None
        gross_margin = None

    currency = _detect_currency(normalized)

    return {
        "model": "Basira Standard Financial Model",
        "version": "0.3",
        "currency": currency,
        "currency_source": "source_header" if currency != "ريال سعودي" else "mvp_default",
        "period_basis": "transaction_date month",
        "metrics": {
            "revenue": {
                "value": _round(total_revenue),
                "source": "normalized_transactions.revenue",
                "status": "calculated_from_normalized_data",
            },
            "cogs": {
                "value": _round(total_cogs) if has_any_cogs else None,
                "source": "normalized_transactions.cogs",
                "status": "calculated_from_normalized_data" if has_any_cogs else "unavailable_missing_cogs",
            },
            "gross_profit": {
                "value": _round(gross_profit),
                "formula": "revenue - cogs",
                "status": "calculated" if gross_profit is not None else "unavailable_missing_cogs",
            },
            "gross_margin": {
                "value": _round(gross_margin),
                "formula": "gross_profit / revenue",
                "status": "calculated" if gross_margin is not None else "unavailable_missing_cogs",
            },
            "operating_expense": {
                "value": _round(total_operating_expense) if total_operating_expense else None,
                "source": "normalized_transactions.operating_expense",
                "status": "calculated_from_normalized_data" if total_operating_expense else "unavailable",
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
            "Currency is detected from monetary source headers when a recognizable currency marker is present; otherwise the MVP default is Arabic Saudi Riyal wording.",
            "Gross profit is calculated as Revenue minus COGS only when COGS is available; missing COGS is not treated as zero.",
            "Operating expenses are kept separate from COGS and are shown in the monthly revenue-and-cost report when available.",
        ],
    }
