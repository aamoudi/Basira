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

The source can be Arabic, English, mixed-language, abbreviated, camelCase, snake_case,
or poorly named.

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


def validate(result: dict[str, Any], profile: dict[str, Any], threshold: float = CONFIDENCE_THRESHOLD) -> dict[str, Any]:
    valid_columns = {
        sheet["sheet_name"]: set(sheet["column_names"])
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
        "Invoice Number",
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
                mapping["reason"] = (mapping.get("reason") or "") + " | Source column does not exist in the supplied sheet."
            elif semantic not in allowed_semantics:
                mapping["status"] = "rejected"
                mapping["reason"] = (mapping.get("reason") or "") + " | Semantic field is outside the Basira schema."
            elif semantic in seen_semantics:
                mapping["status"] = "needs_review"
                mapping["reason"] = (mapping.get("reason") or "") + " | Duplicate semantic target in the same sheet."
            elif confidence < threshold and mapping.get("status") == "accepted":
                mapping["status"] = "needs_review"
                mapping["reason"] = (mapping.get("reason") or "") + f" | Confidence below {threshold:.2f}."
            seen_semantics.add(semantic)

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
