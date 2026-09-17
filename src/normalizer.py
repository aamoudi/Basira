from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CANONICAL_FIELDS = [
    "transaction_date",
    "customer",
    "product",
    "quantity",
    "revenue",
    "cogs",
    "tax",
    "payment_status",
    "transaction_type",
]

# These are derived from normalized transaction data; they are not source mappings.
DERIVED_FIELDS = [
    "operating_expense",
    "gross_profit",
    "gross_margin",
]

SEMANTIC_TO_CANONICAL = {
    "Transaction Date": "transaction_date",
    "Customer": "customer",
    "Product": "product",
    "Quantity": "quantity",
    "Revenue": "revenue",
    "COGS": "cogs",
    "Tax": "tax",
    "Payment Status": "payment_status",
    "Transaction Type": "transaction_type",
}

NULL_MARKERS = {"", "n/a", "na", "null", "none", "unknown", "-"}


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in NULL_MARKERS:
        return True
    return False


def _to_number(value: Any) -> float | int | None:
    if _is_null(value) or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    text = str(value).strip().replace(",", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        # Handle common currency symbols conservatively.
        cleaned = re.sub(r"[^0-9.+-]", "", text)
        if not cleaned or cleaned in {".", "+", "-", "+.", "-."}:
            return None
        try:
            number = Decimal(cleaned)
        except InvalidOperation:
            return None
    return int(number) if number == number.to_integral_value() else float(number)


def _to_quantity(value: Any) -> float | int | None:
    number = _to_number(value)
    if number is None:
        return None
    # Negative and fractional quantities are kept as values but flagged by quality.
    return number


def _to_date(value: Any) -> str | None:
    if _is_null(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _is_invalid_canonical(field: str, original: Any, converted: Any) -> bool:
    if _is_null(original):
        return False
    if converted is None:
        return True
    if field == "quantity" and isinstance(converted, (int, float)):
        return converted < 0
    if field in {"revenue", "cogs", "tax"} and isinstance(converted, (int, float)):
        return not math.isfinite(float(converted))
    if field == "transaction_date" and converted is None:
        return True
    return False


def _convert(field: str, value: Any) -> Any:
    if _is_null(value):
        return None
    if field == "transaction_date":
        return _to_date(value)
    if field == "quantity":
        return _to_quantity(value)
    if field in {"revenue", "cogs", "tax"}:
        return _to_number(value)
    return value


def build_normalized_transactions(
    rows: list[dict[str, Any]],
    mapping_items: list[dict[str, Any]],
    *,
    source_workbook: str | None = None,
    source_sheet: str | None = None,
) -> dict[str, Any]:
    accepted_items = [
        item
        for item in mapping_items
        if item.get("status") in {"accepted", "needs_review"}
        and item.get("semantic_field") in SEMANTIC_TO_CANONICAL
    ]

    source_by_canonical: dict[str, str] = {}
    meta_by_canonical: dict[str, dict[str, Any]] = {}
    for item in accepted_items:
        canonical = SEMANTIC_TO_CANONICAL[item["semantic_field"]]
        if canonical not in source_by_canonical:
            source_by_canonical[canonical] = item["source_column"]
            meta_by_canonical[canonical] = {
                "semantic_field": item["semantic_field"],
                "source_column": item["source_column"],
                "confidence": item.get("confidence"),
                "status": item.get("status"),
            }

    records: list[dict[str, Any]] = []
    missing_by_field = {field: 0 for field in CANONICAL_FIELDS}
    invalid_by_field = {field: 0 for field in CANONICAL_FIELDS}

    for index, row in enumerate(rows, start=2):
        record: dict[str, Any] = {"source_row": index}
        quality = {"missing_fields": [], "invalid_fields": []}

        for canonical in CANONICAL_FIELDS:
            source_column = source_by_canonical.get(canonical)
            if not source_column:
                record[canonical] = None
                quality["missing_fields"].append(canonical)
                missing_by_field[canonical] += 1
                continue

            original = row.get(source_column)
            converted = _convert(canonical, original)
            record[canonical] = converted

            if _is_null(original):
                quality["missing_fields"].append(canonical)
                missing_by_field[canonical] += 1
            elif _is_invalid_canonical(canonical, original, converted):
                quality["invalid_fields"].append(canonical)
                invalid_by_field[canonical] += 1

        # Derived financial fields. These are initialized for every row so the
        # transaction table has a stable schema even when the source omits them.
        record["operating_expense"] = None
        record["gross_profit"] = None
        record["gross_margin"] = None

        # A single monetary source can represent different transaction types.
        # Use Transaction Type to classify the amount instead of treating every
        # amount as revenue. Do not guess when the type is unknown.
        revenue_source = source_by_canonical.get("revenue")
        expense_source = source_by_canonical.get("operating_expense")
        type_source = source_by_canonical.get("transaction_type")

        # If the same source amount is mapped to Revenue or Operating Expense
        # and Transaction Type determines its meaning, classify it row by row.
        shared_amount_source = None
        if type_source:
            if revenue_source and (not expense_source or revenue_source == expense_source):
                shared_amount_source = revenue_source
            elif expense_source and not revenue_source:
                shared_amount_source = expense_source

        if shared_amount_source and type_source:
            raw_amount = row.get(shared_amount_source)
            amount = _to_number(raw_amount)
            tx_type = str(row.get(type_source) or "").strip().lower()

            expense_terms = (
                "expense", "expenses", "operating expense", "cost",
                "purchase", "purchases", "مصروف", "مصروفات", "مصاريف",
                "شراء", "مشتريات",
            )
            revenue_terms = (
                "revenue", "income", "sale", "sales", "إيراد", "إيرادات",
                "دخل", "مبيعات", "بيع",
            )

            if amount is not None and tx_type:
                if any(term in tx_type for term in expense_terms):
                    record["revenue"] = None
                    record["operating_expense"] = amount
                elif any(term in tx_type for term in revenue_terms):
                    record["revenue"] = amount
                    record["operating_expense"] = None
                else:
                    record["revenue"] = None
                    record["operating_expense"] = None

        # Missing COGS is not zero COGS. Gross profit and margin are therefore
        # unavailable unless both Revenue and COGS exist for this row.
        revenue_value = _to_number(record.get("revenue"))
        cogs_value = _to_number(record.get("cogs"))
        if revenue_value is not None and cogs_value is not None:
            gross_profit = revenue_value - cogs_value
            record["gross_profit"] = round(gross_profit, 2)
            record["gross_margin"] = (
                round((gross_profit / revenue_value) * 100, 2)
                if revenue_value != 0 else None
            )

        record["data_quality"] = quality
        records.append(record)

    return {
        "model": "Basira Normalized Transactions",
        "version": "0.1",
        "schema": CANONICAL_FIELDS + DERIVED_FIELDS,
        "records": records,
        "mapping": meta_by_canonical,
        "quality_summary": {
            "rows": len(records),
            "missing_by_field": missing_by_field,
            "invalid_by_field": invalid_by_field,
        },
        "source": {"workbook": source_workbook, "sheet": source_sheet},
    }
