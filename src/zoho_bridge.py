"""
Basira Zoho Bridge  v0.1
=========================
Uploads Basira pipeline output to Zoho Analytics workspace.

Creates / refreshes three tables:
  • basira_transactions    — normalized transaction rows
  • basira_financial_model — monthly period metrics
  • basira_insights        — AI-generated insight text

Usage:
    py src/zoho_bridge.py outputs/01_clear_english_normalized_transactions.json ^
                          outputs/01_clear_english_financial_model.json ^
                          outputs/01_clear_english_insights.json
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load credentials/config from the project root .env when this script is run
# from the project root or from src/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ── CREDENTIALS / CONFIG ───────────────────────────────────────────────────────
# Secrets and IDs are loaded from the project-root .env file.
# GEMINI_API_KEY is intentionally defined in the same .env for the rest of
# Basira, although this bridge does not use it directly.

CLIENT_ID     = os.environ.get("ZOHO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN")
WORKSPACE_ID  = os.environ.get("ZOHO_WORKSPACE_ID")
ORG_ID        = os.environ.get("ZOHO_ORG_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

ZOHO_TOKEN_URL = os.environ.get(
    "ZOHO_TOKEN_URL", "https://accounts.zoho.com/oauth/v2/token"
)
ZOHO_API_BASE = os.environ.get(
    "ZOHO_ANALYTICS_BASE", "https://analyticsapi.zoho.com/restapi/v2"
)
ZOHO_API_V1 = "https://analyticsapi.zoho.com/api"

def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Put it in the project-root .env file."
        )
    return value


def validate_config() -> None:
    # Gemini is not required by zoho_bridge.py itself.
    # These are only the Zoho values required by this script.
    for name in (
        "ZOHO_CLIENT_ID",
        "ZOHO_CLIENT_SECRET",
        "ZOHO_REFRESH_TOKEN",
        "ZOHO_WORKSPACE_ID",
        "ZOHO_ORG_ID",
    ):
        require_env(name)


# ── AUTHENTICATION ────────────────────────────────────────────────────────────

def get_access_token() -> str:
    """Exchange refresh token for a fresh short-lived access token."""
    resp = requests.post(ZOHO_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")
    return token


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Zoho-oauthtoken {token}",
        "ZANALYTICS-ORGID": ORG_ID,
        "Accept":        "application/json",
    }


# ── VIEW (TABLE) MANAGEMENT ───────────────────────────────────────────────────

def list_views(token: str) -> dict[str, str]:
    """Return {view_name: view_id} for all views in the workspace."""
    url  = f"{ZOHO_API_BASE}/workspaces/{WORKSPACE_ID}/views"
    resp = requests.get(url, headers=_auth_headers(token), timeout=30)
    resp.raise_for_status()
    views = resp.json().get("data", {}).get("views", [])
    return {v["viewName"]: v["viewId"] for v in views}

def import_via_v1(token, email, workspace_name, table_name, csv_data, label):
    url = f"{ZOHO_API_V1}/{email}/{workspace_name}/{table_name}"
    resp = requests.post(
        url,
        params={
            "ZOHO_ACTION":        "IMPORT",
            "ZOHO_OUTPUT_FORMAT": "JSON",
            "ZOHO_ERROR_FORMAT":  "JSON",
            "ZOHO_API_VERSION":   "1.0",
        },
        headers={"Authorization": f"Zoho-oauthtoken {token}"},
        data={
            "ZOHO_FILE_TYPE":      "CSV",
            "ZOHO_IMPORT_DATA":    csv_data,
            "ZOHO_AUTO_IDENTIFY":  "TRUE",
            "ZOHO_ON_IMPORT_ERROR":"ABORT",
            "ZOHO_CREATE_TABLE":   "TRUE",
            "ZOHO_IMPORT_TYPE":    "TRUNCATEADD",
        },
        timeout=60,
    )
    body = resp.json()
    if body.get("status") == "failure":
        raise RuntimeError(
            f"v1 import failed for '{label}': {body.get('summary')} — "
            f"{body.get('data', {}).get('errorMessage', '')}"
        )
    imported = body.get("data", {}).get("importResult", {}).get("importedRows", "?")
    print(f"    ✓  {imported} rows imported")
    return  # ← نهاية الدالة

# ── CSV HELPERS ───────────────────────────────────────────────────────────────

def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ── DATA IMPORT ───────────────────────────────────────────────────────────────



# ── DATA TRANSFORMERS ─────────────────────────────────────────────────────────

def build_transactions_rows(norm: dict) -> list[dict]:
    """Flatten normalized_transactions records — skip internal quality fields."""
    skip = {"source_row", "data_quality"}
    rows = []
    for rec in norm.get("records", []):
        row = {k: v for k, v in rec.items() if k not in skip}
        rows.append(row)
    return rows


def build_financial_model_rows(model: dict) -> list[dict]:
    """One row per period from the financial model."""
    currency = model.get("currency", "SAR")
    rows = []
    for p in model.get("periods", []):
        rows.append({
            "period":       p.get("period"),
            "revenue":      p.get("revenue"),
            "cogs":         p.get("cogs"),
            "gross_profit": p.get("gross_profit"),
            "gross_margin": round(p.get("gross_margin", 0) * 100, 2),  # store as %
            "tax":          p.get("tax"),
            "transactions": p.get("row_count"),
            "currency":     currency,
        })
    return rows


def build_insights_rows(ins: dict) -> list[dict]:
    """
    One row per insight item so Zoho can display them as a table.
    Each row: { category, content, order }
    """
    data = ins.get("insights", {})
    rows: list[dict] = []
    order = 1

    rows.append({"order": order, "category": "ملخص", "content": data.get("summary", "")})
    order += 1

    for i, point in enumerate(data.get("positive_points", []), 1):
        rows.append({"order": order, "category": f"نقطة إيجابية {i}", "content": point})
        order += 1

    for i, risk in enumerate(data.get("risks", []), 1):
        rows.append({"order": order, "category": f"خطر {i}", "content": risk})
        order += 1

    rows.append({"order": order, "category": "توصية", "content": data.get("recommendation", "")})

    return rows


# ── CONNECTIVITY TEST ─────────────────────────────────────────────────────────

def test_connection(token: str) -> None:
    """Quick sanity check — verifies we can reach the workspace."""
    url  = f"{ZOHO_API_BASE}/workspaces/{WORKSPACE_ID}"
    resp = requests.get(url, headers=_auth_headers(token), timeout=15)
    if resp.status_code == 200:
        name = resp.json().get("data", {}).get("workspaces", {}).get("workspaceName", "?")
        print(f"    ✓  Connected to workspace: '{name}'")
    else:
        raise RuntimeError(
            f"Cannot reach workspace {WORKSPACE_ID}: "
            f"HTTP {resp.status_code} — {resp.text[:300]}"
        )

def get_workspace_meta(token: str) -> tuple[str, str]:
    """Return (owner_email, workspace_name) from v2 workspace info."""
    url  = f"{ZOHO_API_BASE}/workspaces/{WORKSPACE_ID}"
    resp = requests.get(url, headers=_auth_headers(token), timeout=15)
    resp.raise_for_status()
    ws = resp.json().get("data", {}).get("workspaces", {})
    return ws.get("createdBy", ""), ws.get("workspaceName", "")

# ── PIPELINE ENTRY POINT ──────────────────────────────────────────────────────

def run(norm_path: str, model_path: str, insights_path: str) -> None:
    validate_config()
    norm     = json.loads(Path(norm_path).read_text(encoding="utf-8"))
    model    = json.loads(Path(model_path).read_text(encoding="utf-8"))
    insights = json.loads(Path(insights_path).read_text(encoding="utf-8"))

    print("\n══════════════════════════════════════")
    print("  Basira → Zoho Analytics Bridge")
    print("══════════════════════════════════════")

    print("\n[1/5]  Authenticating with Zoho...")
    token = get_access_token()
    test_connection(token)
    email, workspace_name = get_workspace_meta(token)
    print(f"    ✓  Workspace owner: {email}")

    # Table → (rows, human label)
    tables = [
        ("basira_transactions",    build_transactions_rows(norm),    "Transactions"),
        ("basira_financial_model", build_financial_model_rows(model), "Financial Model"),
        ("basira_insights",        build_insights_rows(insights),     "Insights"),
    ]

    for step, (table_name, rows, label) in enumerate(tables, start=2):
        print(f"\n[{step}/5]  {label} → '{table_name}'  ({len(rows)} rows)")
        csv_data = rows_to_csv(rows)
        import_via_v1(token, email, workspace_name, table_name, csv_data, label)

    print("\n[5/5]  ✅  All data uploaded successfully!")
    print("\n──────────────────────────────────────")
    print("  Open your Zoho Analytics workspace:")
    print(f"  https://analytics.zoho.com/workspace/{WORKSPACE_ID}/")
    print("\n  Tables available for Dashboard building:")
    print("    • basira_transactions    — transaction-level data")
    print("    • basira_financial_model — period KPIs (revenue, margin, ...)")
    print("    • basira_insights        — Arabic AI insights text")
    print("──────────────────────────────────────\n")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "Usage:\n"
            "  py src/zoho_bridge.py "
            "<normalized.json> <financial_model.json> <insights.json>"
        )
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
