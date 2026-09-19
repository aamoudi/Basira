from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from profile_excel import profile_workbook

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# Gemini 3.5 Flash-Lite is the fixed MVP model for this build.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CONFIDENCE_THRESHOLD = 0.90

SYSTEM_PROMPT = """
You are Basira's Financial Semantic Mapping Agent.

Your task is ONLY to understand an unknown business-data schema and map source columns
onto the predefined semantic financial fields supplied in the request.

Use context rather than exact string matching alone. Consider:
- column name and language
- data type
- null/unique percentages
- numeric statistics
- sample values
- other columns in the same sheet
- the table/transaction context
- relationships between columns, especially when a monetary amount is accompanied by a
  transaction type or category that determines whether the amount is revenue, expense,
  purchase, refund, sale, or another type

The source can be Arabic, English, mixed-language, abbreviated, camelCase, snake_case,
or poorly named.

Important:
- A monetary column may contain multiple financial meanings depending on another column
  such as Transaction Type.
- Do not assume that every monetary amount is Revenue when another column explicitly
  classifies the transaction.
- If a source contains one Amount column plus a Transaction Type column, map the Amount
  to Revenue as the shared monetary source when appropriate, map the classification column
  to Transaction Type, and let downstream normalization classify each row.

Customer fallback rule:
- Prefer a true Customer / Client column when one exists.
- If no Customer column exists, use Department / Division / Company / Account / Branch / Section or their
  Arabic equivalents as the source for the semantic field "Customer".
- Do not create a new semantic field such as "Department".
- This is a predefined fallback so that the normalized field remains "customer".



Product fallback rule:
- Prefer a true Product column when one exists.
- If Product does not exist, use Item / البند as the source for the semantic field "Product".
- If neither Product nor Item / البند exists, use Category / الفئة as the source for
  the semantic field "Product".
- Do not create new semantic fields such as "Item" or "Category".
- The priority must always be:
  Product → Item / البند → Category / الفئة.

Profit mapping rule:
- If a source column clearly represents Net Profit / Profit / Net Income,
  map it to "Net Profit".
- If a source column clearly represents Profit Margin / Margin %, map it
  to "Profit Margin".
- If these fields exist in the source, prefer them as direct source values
  instead of deriving the same value unnecessarily.
- Net Profit and Profit Margin are part of the mapping layer only; they do
  not represent new dashboard reports.  


Running Balance / Cumulative Balance Rule:
- Never map a Running Balance, Cumulative Balance, Accumulated Balance,
  Closing Balance, or الرصيد التراكمي / الرصيد المتراكم column to
  Revenue, COGS, Operating Expense, Net Profit, or Profit Margin.
- A running balance includes the effect of previous transactions.
  It is not an independent transaction amount and must never be summed
  to calculate revenue, expenses, or profit.
- If the source contains both a transaction amount and a running balance,
  map the transaction amount according to its transaction type.
  Leave the running balance unmapped.
- Do not interpret a cumulative balance as Net Profit, even if its values
  increase with revenue and decrease with expenses.  


UNIT COST RULES:
- Map unit cost columns (e.g. "تكلفة الوحدة", "Unit Cost",
  "Cost Per Unit") to "unit_cost".
- Map total cost of goods sold columns to "cogs".
- Never map unit cost directly to total "cogs".
- If both unit cost and quantity exist, preserve them
  separately so total COGS can be calculated later.    

Do not replace a higher-priority field with a lower-priority fallback when the
higher-priority field exists.
 

Do not invent mappings when evidence is weak. A source column may remain unmapped.
A semantic field should normally have at most one best source column per sheet.
Return a confidence between 0 and 1.
Use status "accepted" when confidence is high, "needs_review" when the mapping is
plausible but uncertain, and "rejected" only when the proposed mapping is not supported.
Do not invent source columns or semantic fields.
""".strip()

# Gemini Structured Output supports a subset of JSON Schema. Keep this schema simple
# and deliberately avoid unsupported JSON-Schema keywords such as additionalProperties.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sheet_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sheet_name": {"type": "string"},
                    "mappings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_column": {"type": "string"},
                                "semantic_field": {"type": "string"},
                                "confidence": {"type": "number"},
                                "status": {
                                    "type": "string",
                                    "enum": ["accepted", "needs_review", "rejected"],
                                },
                                "reason": {"type": "string"},
                            },
                            "required": [
                                "source_column",
                                "semantic_field",
                                "confidence",
                                "status",
                                "reason",
                            ],
                        },
                    },
                    "unmapped_semantic_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["sheet_name", "mappings", "unmapped_semantic_fields"],
            },
        }
    },
    "required": ["sheet_results"],
}


def call_gemini(profile: dict[str, Any], semantic_schema: dict[str, Any], model: str) -> dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. In Windows CMD run: set GEMINI_API_KEY=YOUR_KEY"
        )

    prompt = (
        SYSTEM_PROMPT
        + "\n\nSemantic fields:\n"
        + json.dumps(semantic_schema, ensure_ascii=False, indent=2)
        + "\n\nWorkbook profile:\n"
        + json.dumps(profile, ensure_ascii=False, indent=2)
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": OUTPUT_SCHEMA,
            "temperature": 0.1,
        },
    }

    response = requests.post(
        GEMINI_API_URL.format(model=model),
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        try:
            details = response.json()
        except ValueError:
            details = response.text[:1500]
        raise RuntimeError(f"Gemini API error {response.status_code}: {details}")

    data = response.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"No usable structured output found in Gemini response: {data}") from exc

    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON: {text[:1500]}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("sheet_results"), list):
        raise RuntimeError("Gemini returned JSON that does not match the expected Basira mapping structure.")
    return result

CUSTOMER_FALLBACK_GROUPS = (
    (
        "customer",
        "customer name",
        "client",
        "client name",
        "العميل",
        "اسم العميل",
    ),
    (
        "department",
        "department name",
        "division",
        "branch",
        "section",
        "business unit",
        "القسم",
        "اسم القسم",
        "الفرع",
        "الشعبة",
        "الإدارة",
    ),
)

# Columns representing a running/cumulative balance must never be
# mapped to revenue, expenses, profit, or profit margin.
BALANCE_COLUMN_TERMS = (
    "balance",
    "running balance",
    "cumulative balance",
    "accumulated balance",
    "closing balance",
    "الرصيد التراكمي",
    "الرصيد المتراكم",
    "الرصيد المرحل",
    "الرصيد الختامي",
    "رصيد تراكمي",
    "رصيد مرحل",
)

PRODUCT_FALLBACK_GROUPS = (
    (
        "product",
        "product name",
        "المنتج",
        "اسم المنتج",
    ),
    (
        "item",
        "item name",
        "line item",
        "البند",
        "اسم البند",
    ),
    (
        "category",
        "category name",
        "الفئة",
        "اسم الفئة",
    ),
)


def _normalize_column_label(value: Any) -> str:
    return (
        str(value)
        .strip()
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .replace("  ", " ")
    )

def _is_balance_column(source_column: str) -> bool:
    normalized = _normalize_column_label(source_column)

    return any(
        _normalize_column_label(term) in normalized
        for term in BALANCE_COLUMN_TERMS
    )


def _find_fallback_source(
    columns: list[str],
    groups: tuple[tuple[str, ...], ...],
) -> str | None:
    normalized_columns = {
        column: _normalize_column_label(column)
        for column in columns
    }

    for aliases in groups:
        normalized_aliases = {
            _normalize_column_label(alias)
            for alias in aliases
        }

        for column in columns:
            if normalized_columns[column] in normalized_aliases:
                return column

    return None


def _has_usable_mapping(
    mappings: list[dict[str, Any]],
    semantic_field: str,
) -> bool:
    return any(
        mapping.get("semantic_field") == semantic_field
        and mapping.get("status") in {"accepted", "needs_review"}
        for mapping in mappings
    )


def _apply_fallback_mapping(
    sheet_result: dict[str, Any],
    columns: list[str],
    semantic_field: str,
    fallback_groups: tuple[tuple[str, ...], ...],
    reason: str,
) -> None:
    mappings = sheet_result.get("mappings", [])

    # Do not override a valid mapping that already exists.
    if _has_usable_mapping(mappings, semantic_field):
        return

    source = _find_fallback_source(columns, fallback_groups)
    if source is None:
        return

    # Remove any previous mapping that used the selected fallback source.
    # The fallback rule is authoritative when the preferred semantic field
    # was not successfully mapped.
    mappings = [
        mapping
        for mapping in mappings
        if mapping.get("source_column") != source
    ]

    mappings.append(
        {
            "source_column": source,
            "semantic_field": semantic_field,
            "confidence": 1.0,
            "status": "accepted",
            "reason": reason,
        }
    )

    sheet_result["mappings"] = mappings

    sheet_result["unmapped_semantic_fields"] = [
        field
        for field in sheet_result.get("unmapped_semantic_fields", [])
        if field != semantic_field
    ]


def validate(result: dict[str, Any], profile: dict[str, Any], threshold: float = CONFIDENCE_THRESHOLD) -> dict[str, Any]:
    valid_columns = {
    sheet["sheet_name"]: set(sheet["column_names"])
    for sheet in profile.get("sheets", [])
    }

    sheet_columns = {
        sheet["sheet_name"]: list(sheet["column_names"])
        for sheet in profile.get("sheets", [])
    }
    allowed_semantics = {
        "Revenue",
        "COGS",
        "Operating Expense",
        "Tax",
        "Customer",
        "Product",
        "Transaction Date",
        "Quantity",
        "Payment Status",
        "Transaction Type",
        "Invoice Number",
        "Net Profit",
        "Profit Margin",
        "unit_cost"
    }

    for sheet_result in result.get("sheet_results", []):
        sheet_name = sheet_result.get("sheet_name", "")
        seen_semantics: set[str] = set()
        for mapping in sheet_result.get("mappings", []):
            source = mapping.get("source_column")
            semantic = mapping.get("semantic_field")
            confidence = max(0.0, min(1.0, float(mapping.get("confidence", 0.0))))
            mapping["confidence"] = round(confidence, 4)

            if source not in valid_columns.get(sheet_name, set()):
                mapping["status"] = "rejected"
                mapping["reason"] = (
                    (mapping.get("reason") or "")
                    + " | Source column does not exist in the supplied sheet."
                )

            elif (
                _is_balance_column(str(source))
                and semantic in {
                    "Revenue",
                    "COGS",
                    "Operating Expense",
                    "Net Profit",
                    "Profit Margin",
                }
            ):
                mapping["status"] = "rejected"
                mapping["reason"] = (
                    (mapping.get("reason") or "")
                    + " | Running/cumulative balance columns cannot be used "
                    "as revenue, expenses, net profit, or profit margin."
                )

            elif semantic not in allowed_semantics:
                mapping["status"] = "rejected"
                mapping["reason"] = (
                    (mapping.get("reason") or "")
                    + " | Semantic field is outside the Basira schema."
                )
            elif semantic in seen_semantics:
                mapping["status"] = "needs_review"
                mapping["reason"] = (mapping.get("reason") or "") + " | Duplicate semantic target in the same sheet."
            elif confidence < threshold and mapping.get("status") == "accepted":
                mapping["status"] = "needs_review"
                mapping["reason"] = (mapping.get("reason") or "") + f" | Confidence below {threshold:.2f}."
            seen_semantics.add(semantic)
        # Apply deterministic Basira fallback rules after validating the AI mapping.
        # Customer: Customer → Department/Branch/etc.
        # Product: Product → Item/البند → Category/الفئة.
        for sheet_result in result.get("sheet_results", []):
            sheet_name = sheet_result.get("sheet_name", "")
            columns = sheet_columns.get(sheet_name, [])

            _apply_fallback_mapping(
                sheet_result,
                columns,
                "Customer",
                CUSTOMER_FALLBACK_GROUPS,
                "Deterministic Basira fallback: Customer was unavailable, so an organizational unit column was mapped to Customer.",
            )

            _apply_fallback_mapping(
                sheet_result,
                columns,
                "Product",
                PRODUCT_FALLBACK_GROUPS,
                "Deterministic Basira fallback: Product was unavailable, so the highest-priority available Item/Category column was mapped to Product.",
            )

    

    return result


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: py src/run_mapper.py <file.xlsx>")
        raise SystemExit(2)

    path = Path(sys.argv[1])
    root = Path(__file__).resolve().parents[1]
    semantic_schema = json.loads((root / "src" / "semantic_schema.json").read_text(encoding="utf-8"))
    profile = profile_workbook(path)
    model = os.environ.get("BASIRA_MODEL", DEFAULT_MODEL)
    if model != DEFAULT_MODEL:
        print(f"Warning: BASIRA_MODEL override is active: {model}")

    result = validate(call_gemini(profile, semantic_schema, model), profile)
    output_path = root / "outputs" / f"{path.stem}_mapping.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved: {output_path}")
    for sheet in result.get("sheet_results", []):
        print(f"\n[{sheet['sheet_name']}]")
        for mapping in sheet.get("mappings", []):
            print(
                f"  {mapping['source_column']} -> {mapping['semantic_field']} "
                f"| {mapping['confidence']:.0%} | {mapping['status']}"
            )
        if sheet.get("unmapped_semantic_fields"):
            print("  Unmapped:", ", ".join(sheet["unmapped_semantic_fields"]))


if __name__ == "__main__":
    main()
