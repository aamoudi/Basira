from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


DEFAULT_MODEL = "gemini-3.5-flash-lite"

DISPLAY_CURRENCY = {
    "SAR": "ريال سعودي",
}


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "positive_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recommendation": {"type": "string"},
    },
    "required": ["summary", "positive_points", "risks", "recommendation"],
}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_pct(value: float | None, denominator: float | None) -> float | None:
    if value is None or denominator in (None, 0):
        return None
    return round((value / denominator) * 100, 2)


def _format_amount(value: float | None, currency: str | None = None) -> str | None:
    if value is None:
        return None

    number = f"{value:,.2f}".rstrip("0").rstrip(".")
    display_currency = DISPLAY_CURRENCY.get(currency, currency)

    currency_labels = {
        "SAR": "ريال سعودي",
    }

    display_currency = currency_labels.get(currency, currency)

    return f"{number} {display_currency}" if display_currency else number

def _format_pct(value: float | None) -> str | None:
    if value is None:
        return None
    if float(value).is_integer():
        number = str(int(value))
    else:
        number = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{number}%"

def _fix_arabic_bidi(text: str) -> str:
    """
    Improve RTL rendering for Arabic text containing numbers and punctuation.
    Uses invisible Right-to-Left Marks around percentages and Arabic full stop
    for sentence-ending periods.
    """
    if not isinstance(text, str):
        return text

    # Right-to-Left Mark (invisible Unicode character)
    rlm = "\u200f"

    # Keep percentages visually attached to their number in RTL text.
    text = re.sub(
        r"(\d+(?:\.\d+)?)%",
        rf"{rlm}\1%{rlm}",
        text,
    )

    # Convert sentence-ending ASCII periods after Arabic text
    # to the Arabic full stop, without touching decimal numbers.
    text = re.sub(
        r"(?<=[\u0600-\u06FF])\.(?=\s|$)",
        "۔",
        text,
    )

    return text



def _period_deltas(periods: list[dict[str, Any]]) -> dict[str, Any]:
    period_count = len(periods)
    if period_count < 2:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "At least 2 periods are required for a comparison.",
            "period_count": period_count,
        }

    prev = periods[-2]
    curr = periods[-1]
    result: dict[str, Any] = {
        "available": True,
        "previous_period": prev.get("period"),
        "current_period": curr.get("period"),
        "period_count": period_count,
    }
    for key in ("revenue", "cogs", "gross_profit", "gross_margin", "tax"):
        prev_value = _num(prev.get(key))
        curr_value = _num(curr.get(key))
        result[key] = {
            "previous": prev_value,
            "current": curr_value,
            "absolute_change": None if prev_value is None or curr_value is None else round(curr_value - prev_value, 2),
            "percent_change": _safe_pct(curr_value - prev_value, prev_value)
            if prev_value not in (None, 0) and curr_value is not None
            else None,
        }
    return result


def _data_sufficiency(periods: list[dict[str, Any]]) -> dict[str, Any]:
    min_periods_for_trend = 3
    min_transactions_per_period = 5
    transaction_counts = [
        int(period.get("row_count", 0) or 0)
        for period in periods
        if period.get("period") not in (None, "unknown")
    ]
    min_count = min(transaction_counts) if transaction_counts else 0
    period_count = len(periods)

    if period_count < min_periods_for_trend:
        return {
            "trend_analysis": "unavailable",
            "confidence": "insufficient",
            "reason": "Trend analysis requires at least 3 periods.",
            "period_count": period_count,
            "min_transactions_per_period": min_transactions_per_period,
            "minimum_observed_transactions_per_period": min_count,
        }

    if min_count < min_transactions_per_period:
        return {
            "trend_analysis": "available_with_low_confidence",
            "confidence": "low",
            "reason": "At least one period contains fewer than 5 transactions.",
            "period_count": period_count,
            "min_transactions_per_period": min_transactions_per_period,
            "minimum_observed_transactions_per_period": min_count,
        }

    return {
        "trend_analysis": "available",
        "confidence": "normal",
        "reason": "Sufficient period count and transaction volume for the MVP trend check.",
        "period_count": period_count,
        "min_transactions_per_period": min_transactions_per_period,
        "minimum_observed_transactions_per_period": min_count,
    }

def _aggregate_dimension(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = {}
    for row in records:
        label = row.get(key)
        if label is None or str(label).strip() == "":
            label = "Unknown"
        label = str(label)
        bucket = buckets.setdefault(label, {"revenue": 0.0, "transaction_count": 0.0})
        revenue = _num(row.get("revenue"))
        if revenue is not None:
            bucket["revenue"] += revenue
        bucket["transaction_count"] += 1
    items = [
        {
            key: label,
            "revenue": round(values["revenue"], 2),
            "transaction_count": int(values["transaction_count"]),
        }
        for label, values in buckets.items()
    ]
    return sorted(items, key=lambda item: item["revenue"], reverse=True)


def _payment_status_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, int] = {}
    for row in records:
        status = row.get("payment_status")
        label = "Unknown" if status is None or str(status).strip() == "" else str(status)
        buckets[label] = buckets.get(label, 0) + 1
    total = len(records)
    return [
        {"status": label, "transaction_count": count, "share_pct": round(count / total * 100, 2) if total else 0.0}
        for label, count in sorted(buckets.items(), key=lambda item: item[1], reverse=True)
    ]


def build_insight_context(financial_model: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    records = normalized.get("records", [])
    metrics = financial_model.get("metrics", {})
    periods = financial_model.get("periods", [])
    revenue = _num(metrics.get("revenue", {}).get("value")) or 0.0
    gross_profit = _num(metrics.get("gross_profit", {}).get("value"))
    tax = _num(metrics.get("tax", {}).get("value"))
    unpaid_labels = {"unpaid", "overdue", "late", "pending"}
    unpaid_count = sum(
        1 for row in records
        if str(row.get("payment_status") or "").strip().lower() in unpaid_labels
    )

    customer_rank = _aggregate_dimension(records, "customer")
    product_rank = _aggregate_dimension(records, "product")

    sufficiency = _data_sufficiency(periods)
    currency = financial_model.get("currency")
    cogs = _num(metrics.get("cogs", {}).get("value"))
    gross_margin = _num(metrics.get("gross_margin", {}).get("value"))

    return {
        "currency": currency,
        "metrics": {
            "revenue": revenue,
            "cogs": cogs,
            "gross_profit": gross_profit,
            "gross_margin": gross_margin,
            "tax": tax,
        },
        "formatted_metrics": {
            "revenue": _format_amount(revenue, currency),
            "cogs": _format_amount(cogs, currency),
            "gross_profit": _format_amount(gross_profit, currency),
            "gross_margin": _format_pct(gross_margin * 100 if gross_margin is not None else None),
            "tax": _format_amount(tax, currency),
        },
        "period_comparison": _period_deltas(periods),
        "data_sufficiency_check": sufficiency,
        "customer_rank": customer_rank[:10],
        "product_rank": product_rank[:10],
        "payment_status": _payment_status_summary(records),
        "transaction_count": len(records),
        "unpaid_or_overdue_transaction_count": unpaid_count,
        "unpaid_or_overdue_transaction_share_pct": round(unpaid_count / len(records) * 100, 2) if records else 0.0,
        "data_quality": financial_model.get("data_quality", {}),
    }


def _validate_insights(result: dict[str, Any]) -> dict[str, Any]:
    required = ("summary", "positive_points", "risks", "recommendation")
    missing = [key for key in required if key not in result]
    if missing:
        raise ValueError(f"Insight response missing fields: {missing}")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise ValueError("Insight summary is empty.")
    for key in ("positive_points", "risks"):
        if not isinstance(result[key], list) or not all(isinstance(item, str) for item in result[key]):
            raise ValueError(f"Insight field '{key}' must be a list of strings.")
    if not isinstance(result["recommendation"], str) or not result["recommendation"].strip():
        raise ValueError("Insight recommendation is empty.")
    if len(result["positive_points"]) > 3:
        result["positive_points"] = result["positive_points"][:3]

    if len(result["risks"]) > 2:
        result["risks"] = result["risks"][:2]

    # Normalize Arabic RTL rendering after Gemini response.
    result["summary"] = _fix_arabic_bidi(result["summary"])

    result["positive_points"] = [
        _fix_arabic_bidi(item)
        for item in result["positive_points"]
    ]

    result["risks"] = [
        _fix_arabic_bidi(item)
        for item in result["risks"]
    ]

    result["recommendation"] = _fix_arabic_bidi(result["recommendation"])

    return result


def build_prompt(context: dict[str, Any]) -> str:
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"""
أنت طبقة صياغة لغوية فقط داخل منتج Basira للتحليلات المالية.

مهمتك:
- تحويل الحقائق المحسوبة في السياق أدناه إلى استنتاجات مالية عربية واضحة ومهنية.
- لا تقم بإعادة حساب أي رقم.
- لا تخترع أرقاماً أو نسباً أو تواريخ أو أسباباً غير موجودة في السياق.
- لا تستخدم أي معلومة خارج السياق.
- لا تطلق حكماً على السيولة أو الربحية أو المخاطر إلا إذا كان السياق يدعم ذلك صراحة.
- إذا كانت data_sufficiency_check.trend_analysis = "unavailable" فلا تذكر أي نمو أو انخفاض أو اتجاه بين الفترات. وضّح أن البيانات غير كافية لاستنتاج اتجاه موثوق.
- إذا كانت data_sufficiency_check.trend_analysis = "available_with_low_confidence" فلا تقدّم المقارنة كاتجاه مؤكد؛ استخدم صياغة حذرة واذكر أن العينة صغيرة.
- إذا لم تكفِ البيانات لإثبات استنتاج قوي، استخدم صياغة حذرة مثل: "يستحق المتابعة" أو "تظهر إشارة تستحق المراجعة".
- يجب أن تكون الإجابة باللغة العربية.
- عند ذكر أي نسبة مئوية، استخدم صيغة العرض المئوية مثل "26%" أو "61.95%"، وليس قيمة عشرية مثل "0.26".
- استخدم علامة % مباشرة بعد الرقم دون مسافة.
- استخدم علامة الوقف العربية "۔" في نهاية الجمل العربية بدل النقطة الإنجليزية ".".
- عند ذكر أي مبلغ، استخدم صيغة العرض الموجودة في `formatted_metrics` الخاصة بالسياق، مع فواصل آلاف وإظهار العملة باللغة العربية، مثل "15,600 ريال سعودي".
- لا تستخدم رمز العملة "SAR" في النص الموجه للمستخدم إذا كان الاسم العربي للعملة متاحاً.
- لا تعرض القيمة العشرية الخام لهامش الربح إذا كانت صيغة العرض المئوية متاحة.
- لا تغيّر قيمة الأرقام أثناء تنسيقها؛ التنسيق فقط مسموح.
- أعد JSON مطابقاً للمخطط المطلوب.

المطلوب:
1) summary: ملخص مالي من جملتين.
2) positive_points: حتى 3 نقاط إيجابية مدعومة بالبيانات.
3) risks: حتى نقطتين تستحقان الانتباه، ويجب أن تكونا مدعومتين بالبيانات.
4) recommendation: توصية عملية واحدة مرتبطة مباشرة بإحدى النتائج.

السياق المحسوب من Basira:
{context_json}
""".strip()


def call_gemini(prompt: str, model: str | None = None, timeout: int = 90) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    selected_model = model or os.getenv("BASIRA_MODEL", DEFAULT_MODEL)
    url = GEMINI_URL.format(model=selected_model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": INSIGHT_SCHEMA,
            "temperature": 0.2,
        },
    }
    response = requests.post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Gemini API error {response.status_code}: {response.text[:1600]}")
    body = response.json()
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not parse Gemini structured response: {body}") from exc
    return _validate_insights(result)


def build_insights(financial_model: dict[str, Any], normalized: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    context = build_insight_context(financial_model, normalized)
    prompt = build_prompt(context)
    result = call_gemini(prompt, model=model)
    return {
        "model": "Basira Financial Insights",
        "version": "0.1",
        "language": "ar",
        "insights": result,
        "evidence": context,
        "source": {
            "financial_model_version": financial_model.get("version"),
            "workbook": normalized.get("source", {}).get("workbook"),
            "sheet": normalized.get("source", {}).get("sheet"),
        },
        "rules": [
            "Numerical analysis and comparisons are calculated by Python before the LLM call.",
            "Gemini is used for Arabic wording and synthesis only.",
            "Insights must be grounded in the supplied evidence context.",
            "Trend claims are allowed only when the data_sufficiency_check permits them.",
        ],
    }
