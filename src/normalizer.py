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

    # Source-provided profit fields are normalized into the existing
    # Basira profit columns. No new dashboard columns are created.
    "Net Profit": "gross_profit",
    "Profit Margin": "gross_margin",
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


def _to_margin_percent(value: Any) -> float | None:
    """
    Convert a source profit margin into percentage points.

    Examples:
      0.25   -> 25.0
      25     -> 25.0
      "25%"  -> 25.0
    """
    if _is_null(value):
        return None

    text = str(value).strip()

    has_percent_sign = "%" in text

    number = _to_number(text)
    if number is None:
        return None

    if has_percent_sign:
        return float(number)

    # Decimal ratio such as 0.25 means 25%.
    if -1 <= number <= 1:
        return float(number * 100)

    return float(number)


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

        # ------------------------------------------------------------------
        # Profit
        #
        # Priority:
        # 1) Use source Net Profit when available.
        # 2) Otherwise derive:
        #       Revenue - COGS - Operating Expense
        #
        # This works even when Revenue and Operating Expense come from
        # the same source amount column and are separated by Transaction Type,
        # because that classification has already happened above.
        # ------------------------------------------------------------------

        profit_source = source_by_canonical.get("gross_profit")

        if profit_source:
            source_profit = _to_number(row.get(profit_source))

            if source_profit is not None:
                record["gross_profit"] = round(
                    float(source_profit),
                    2,
                )

        # If the source does not provide Net Profit for this row,
        # derive it from the normalized components.
        if record["gross_profit"] is None:
            revenue_value = _to_number(record.get("revenue"))
            cogs_value = _to_number(record.get("cogs"))
            operating_expense_value = _to_number(
                record.get("operating_expense")
            )

            if any(
                value is not None
                for value in (
                    revenue_value,
                    cogs_value,
                    operating_expense_value,
                )
            ):
                revenue_amount = revenue_value or 0.0
                cogs_amount = abs(cogs_value or 0.0)
                operating_expense_amount = abs(operating_expense_value or 0.0)

                record["gross_profit"] = round(
                    revenue_amount
                    - cogs_amount
                    - operating_expense_amount,
                    2,
                )

        # ------------------------------------------------------------------
        # Source Profit Margin
        #
        # Do NOT calculate row-level margin here.
        # Keep the source value temporarily so the monthly logic below
        # can place exactly one value on the last transaction date.
        # ------------------------------------------------------------------

        margin_source = source_by_canonical.get("gross_margin")

        if margin_source:
            source_margin = _to_margin_percent(
                row.get(margin_source)
            )

            if source_margin is not None:
                record["_source_profit_margin"] = round(
                    source_margin,
                    2,
                )

                
        record["data_quality"] = quality
        records.append(record)

        # ----------------------------------------------------------------------
    
    
    
    # Monthly Profit Margin
    #
    # Exactly one gross_margin value per month:
    # the record having the last transaction date in that month.
    #
    # Priority:
    # 1) Source Profit Margin, if provided.
    # 2) Otherwise:
    #       Monthly Profit / Monthly Revenue * 100
    #
    # All other records receive None.
    # ----------------------------------------------------------------------

    source_margin_available = (
        source_by_canonical.get("gross_margin") is not None
    )

    monthly_records: dict[str, list[tuple[int, dict[str, Any]]]] = {}

    for record_index, record in enumerate(records):
        transaction_date = record.get("transaction_date")

        if (
            isinstance(transaction_date, str)
            and len(transaction_date) >= 7
        ):
            period = transaction_date[:7]

            monthly_records.setdefault(
                period,
                [],
            ).append(
                (record_index, record)
            )

    for period_records in monthly_records.values():

        # First clear all row-level margin values.
        for _, record in period_records:
            record["gross_margin"] = None

        # Find the last transaction date in this month.
        valid_dates = [
            record.get("transaction_date")
            for _, record in period_records
            if record.get("transaction_date")
        ]

        if not valid_dates:
            continue

        last_date = max(valid_dates)

        # Choose exactly one record on the last date.
        target_record = None

        for _, record in reversed(period_records):
            if record.get("transaction_date") == last_date:
                target_record = record
                break

        if target_record is None:
            continue

        # --------------------------------------------------------------
        # Case 1: Profit Margin exists in the source.
        # --------------------------------------------------------------
        if source_margin_available:

            source_margin_value = None

            # Prefer the margin from the final date itself.
            for _, record in reversed(period_records):
                if record.get("transaction_date") != last_date:
                    continue

                candidate = record.get("_source_profit_margin")

                if candidate is not None:
                    source_margin_value = _to_number(candidate)
                    break

            # If the last-date row has no margin, use the latest
            # available source margin within the month.
            if source_margin_value is None:
                for _, record in reversed(period_records):
                    candidate = record.get(
                        "_source_profit_margin"
                    )

                    if candidate is not None:
                        source_margin_value = _to_number(
                            candidate
                        )
                        break

            if source_margin_value is not None:
                target_record["gross_margin"] = round(
                    source_margin_value,
                    2,
                )

        # --------------------------------------------------------------
        # Case 2: No Profit Margin in the source.
        # Calculate monthly margin.
        # --------------------------------------------------------------
        else:

            monthly_revenue = 0.0
            monthly_profit = 0.0

            has_revenue = False
            has_profit = False

            for _, record in period_records:

                revenue_value = _to_number(
                    record.get("revenue")
                )

                profit_value = _to_number(
                    record.get("gross_profit")
                )

                if revenue_value is not None:
                    monthly_revenue += float(
                        revenue_value
                    )
                    has_revenue = True

                if profit_value is not None:
                    monthly_profit += float(
                        profit_value
                    )
                    has_profit = True

            if (
                has_revenue
                and has_profit
                and monthly_revenue != 0
            ):
                target_record["gross_margin"] = round(
                    (
                        monthly_profit
                        / monthly_revenue
                    ) * 100,
                    2,
                )

    # Remove temporary source-only field before returning the
    # normalized transaction data.
    for record in records:
        record.pop(
            "_source_profit_margin",
            None,
        )

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
