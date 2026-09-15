"""
Basira MVP — FastAPI Backend  v0.1
====================================
Receives an Excel file, runs the full Basira pipeline, and returns
the Zoho Analytics Dashboard link.

Start command (Render):
    uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
import pandas as pd

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Add src/ to path so we can import Basira modules ─────────────────────────
SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from profile_excel   import profile_workbook          # noqa: E402
from run_mapper      import call_gemini, validate      # noqa: E402
from normalizer import build_normalized_transactions  # noqa: E402
from financial_model import build_financial_model      # noqa: E402
from insights_engine import build_insights             # noqa: E402
from zoho_bridge     import (                          # noqa: E402
    get_access_token,
    get_workspace_meta,
    import_via_v1,
    rows_to_csv,
    build_transactions_rows,
    build_financial_model_rows,
    build_insights_rows,
    WORKSPACE_ID,
)

import json
import os

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Basira API", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ZOHO_DASHBOARD_URL = os.environ.get(
    "ZOHO_DASHBOARD_URL",
    f"https://analytics.zoho.com/workspace/{WORKSPACE_ID}/"
)


# ── Upload page ───────────────────────────────────────────────────────────────
UPLOAD_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>بصيرة — التحليل المالي الذكي</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
      background: #0d1117;
      color: #e6edf3;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }

    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 16px;
      padding: 2.5rem;
      max-width: 520px;
      width: 100%;
      text-align: center;
    }

    .logo {
      font-size: 2rem;
      font-weight: 700;
      color: #1D9E75;
      margin-bottom: 0.4rem;
      letter-spacing: -0.5px;
    }

    .tagline {
      font-size: 0.95rem;
      color: #8b949e;
      margin-bottom: 2rem;
    }

    .drop-zone {
      border: 2px dashed #30363d;
      border-radius: 12px;
      padding: 2.5rem 1.5rem;
      cursor: pointer;
      transition: all 0.2s;
      margin-bottom: 1.5rem;
      position: relative;
    }

    .drop-zone:hover, .drop-zone.dragover {
      border-color: #1D9E75;
      background: rgba(29, 158, 117, 0.05);
    }

    .drop-icon { font-size: 2.5rem; margin-bottom: 0.75rem; }

    .drop-text {
      font-size: 1rem;
      font-weight: 500;
      margin-bottom: 0.4rem;
    }

    .drop-sub {
      font-size: 0.82rem;
      color: #8b949e;
    }

    #fileInput { display: none; }

    .file-selected {
      display: none;
      background: rgba(29, 158, 117, 0.1);
      border: 1px solid #1D9E75;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      font-size: 0.9rem;
      color: #1D9E75;
      margin-bottom: 1.5rem;
      align-items: center;
      gap: 0.5rem;
    }

    .btn {
      width: 100%;
      padding: 0.85rem;
      background: #1D9E75;
      color: white;
      border: none;
      border-radius: 10px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
      font-family: inherit;
    }

    .btn:hover { background: #178a65; }
    .btn:disabled { background: #30363d; cursor: not-allowed; color: #8b949e; }

    .progress {
      display: none;
      margin-top: 1.5rem;
      text-align: center;
    }

    .spinner {
      width: 36px; height: 36px;
      border: 3px solid #30363d;
      border-top-color: #1D9E75;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin: 0 auto 1rem;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    .progress-text {
      font-size: 0.9rem;
      color: #8b949e;
    }

    .step-list {
      list-style: none;
      text-align: right;
      margin-top: 1rem;
    }

    .step-list li {
      padding: 0.3rem 0;
      font-size: 0.85rem;
      color: #8b949e;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .step-list li.done { color: #1D9E75; }
    .step-list li.active { color: #e6edf3; }

    .error-box {
      display: none;
      background: rgba(232, 72, 72, 0.1);
      border: 1px solid #e84848;
      border-radius: 8px;
      padding: 1rem;
      margin-top: 1rem;
      font-size: 0.85rem;
      color: #f85149;
      text-align: right;
    }

    .footer {
      margin-top: 2rem;
      font-size: 0.8rem;
      color: #30363d;
    }
  </style>
</head>
<body>

<div class="card">
  <div class="logo">بصيرة</div>
  <div class="tagline">ارفع ملفك المالي — واحصل على تحليل ذكي فوري</div>

  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <div class="drop-icon">📂</div>
    <div class="drop-text">اسحب ملفك هنا أو اضغط للاختيار</div>
    <div class="drop-sub">Excel أو CSV — أي هيكلية مالية</div>
    <input type="file" id="fileInput" accept=".xlsx,.xls,.csv">
  </div>

  <div class="file-selected" id="fileSelected">
    <span>📄</span>
    <span id="fileName"></span>
  </div>

  <button class="btn" id="submitBtn" disabled onclick="submitFile()">
    ابدأ التحليل
  </button>

  <div class="progress" id="progress">
    <div class="spinner"></div>
    <div class="progress-text">جاري تحليل بياناتك...</div>
    <ul class="step-list" id="stepList">
      <li id="s1">⏳ قراءة الملف وتحليل الهيكلية</li>
      <li id="s2">⏳ التعرف على الأعمدة المالية</li>
      <li id="s3">⏳ بناء النموذج المالي المعياري</li>
      <li id="s4">⏳ توليد الاستنتاجات الذكية</li>
      <li id="s5">⏳ رفع البيانات للوحة التحكم</li>
    </ul>
  </div>

  <div class="error-box" id="errorBox"></div>
</div>

<div class="footer">Powered by Basira — تقنية التحليل المالي الذكي</div>

<script>
  const dropZone   = document.getElementById('dropZone');
  const fileInput  = document.getElementById('fileInput');
  const submitBtn  = document.getElementById('submitBtn');
  const fileSelected = document.getElementById('fileSelected');
  const fileName   = document.getElementById('fileName');
  const progress   = document.getElementById('progress');
  const errorBox   = document.getElementById('errorBox');

  let selectedFile = null;

  fileInput.addEventListener('change', e => {
    if (e.target.files[0]) selectFile(e.target.files[0]);
  });

  dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });

  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
  });

  function selectFile(file) {
    selectedFile = file;
    fileName.textContent = file.name;
    fileSelected.style.display = 'flex';
    submitBtn.disabled = false;
    errorBox.style.display = 'none';
  }

  const STEP_MESSAGES = [
    'قراءة الملف وتحليل الهيكلية',
    'التعرف على الأعمدة المالية',
    'بناء النموذج المالي المعياري',
    'توليد الاستنتاجات الذكية',
    'رفع البيانات للوحة التحكم',
  ];

  let stepInterval = null;

  function startStepAnimation() {
    let current = 0;
    const steps = document.querySelectorAll('#stepList li');

    stepInterval = setInterval(() => {
      if (current < steps.length) {
        if (current > 0) {
          steps[current - 1].classList.remove('active');
          steps[current - 1].classList.add('done');
          steps[current - 1].textContent = '✅ ' + STEP_MESSAGES[current - 1];
        }
        steps[current].classList.add('active');
        steps[current].textContent = '⏳ ' + STEP_MESSAGES[current];
        current++;
      }
    }, 12000);  // ~60s total / 5 steps
  }

  async function submitFile() {
    if (!selectedFile) return;

    submitBtn.disabled = true;
    progress.style.display = 'block';
    errorBox.style.display  = 'none';
    startStepAnimation();

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const resp = await fetch('/analyze', {
        method: 'POST',
        body: formData,
      });

      clearInterval(stepInterval);

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'خطأ غير معروف');
      }

      const data = await resp.json();
      // Redirect to Zoho Dashboard
      window.location.href = data.dashboard_url;

    } catch (err) {
      clearInterval(stepInterval);
      progress.style.display = 'none';
      submitBtn.disabled = false;
      errorBox.style.display = 'block';
      errorBox.textContent = '⚠ ' + err.message;
    }
  }
</script>

</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def upload_page():
    """Serve the Arabic upload page."""
    return UPLOAD_HTML


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    """
    Full Basira pipeline:
      1. Profile Excel
      2. Gemini Semantic Mapping
      3. Normalize
      4. Financial Model
      5. Insights
      6. Zoho Bridge
    Returns: { dashboard_url }
    """
    # Validate file type
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(
            status_code=400,
            detail="الملف يجب أن يكون Excel أو CSV"
        )

    try:
        # ── Save uploaded file to temp ────────────────────────────────────
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # ── 1. Profile ────────────────────────────────────────────────────
        profile = profile_workbook(tmp_path)

        # ── 2. Semantic Mapping ───────────────────────────────────────────
        schema_path = SRC / "semantic_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        model_name = os.environ.get("BASIRA_MODEL", "gemini-3.5-flash-lite")
        mapping_json = call_gemini(profile, schema, model_name)
        mapping_json = validate(mapping_json, profile)

        # ── 3. Normalize ──────────────────────────────────────────────────
        mapping_items = []
        for sheet in mapping_json.get("sheet_results", []):
            mapping_items.extend(sheet.get("mappings", []))

        if not mapping_items:
            raise ValueError("لم نتمكن من التعرف على أعمدة مالية في هذا الملف.")

        xl = pd.ExcelFile(tmp_path)
        best_sheet = max(xl.sheet_names, key=lambda s: xl.parse(s).shape[0])
        raw_rows = xl.parse(best_sheet).to_dict(orient="records")

        norm_result = build_normalized_transactions(
            raw_rows,
            mapping_items,
            source_workbook=file.filename,
            source_sheet=best_sheet,
        )
        transactions = norm_result["records"]

        # ── 4. Financial Model ────────────────────────────────────────────
        
        df = pd.DataFrame([
            {k: v for k, v in t.items() if not k.startswith("_")}
            for t in transactions
        ])

        from financial_model import build_financial_model as fm_build
        financial_model = fm_build(norm_result)

        # ── 5. Insights ───────────────────────────────────────────────────
        norm_result = {
            "records": [
                {k: v for k, v in t.items() if not k.startswith("_")}
                for t in transactions
            ]
        }
        insights = build_insights(norm_result, financial_model)

        # ── 6. Zoho Bridge ────────────────────────────────────────────────
        token = get_access_token()
        email, workspace_name = get_workspace_meta(token)

        tables = [
            ("basira_transactions",
             build_transactions_rows(norm_result),
             "Transactions"),
            ("basira_financial_model",
             build_financial_model_rows(financial_model),
             "Financial Model"),
            ("basira_insights",
             build_insights_rows(insights),
             "Insights"),
        ]

        for table_name, rows, label in tables:
            csv_data = rows_to_csv(rows)
            import_via_v1(token, email, workspace_name, table_name, csv_data, label)

        return JSONResponse({"dashboard_url": ZOHO_DASHBOARD_URL})

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في معالجة الملف: {str(e)[:200]}"
        )
    finally:
        # Clean up temp file
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/health")
async def health():
    """Health check for Render."""
    return {"status": "ok", "service": "Basira MVP"}
