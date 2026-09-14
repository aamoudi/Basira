from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

MAX_SAMPLE_VALUES = 8
MAX_SAMPLE_ROWS = 12


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return str(value)


def infer_kind(series: pd.Series) -> str:
    nonnull = series.dropna()
    if nonnull.empty:
        return "unknown"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "integer" if pd.api.types.is_integer_dtype(series) else "float"

    # Use pandas' mixed parser explicitly to avoid inference warnings.
    sample = nonnull.astype(str).head(50)
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=False)
    if parsed.notna().mean() >= 0.8:
        return "date"
    return "text"


def profile_column(series: pd.Series) -> dict[str, Any]:
    kind = infer_kind(series)
    nonnull = series.dropna()
    out: dict[str, Any] = {
        "name": str(series.name),
        "kind": kind,
        "dtype": str(series.dtype),
        "rows": int(len(series)),
        "null_percentage": round(float(series.isna().mean() * 100), 2),
        "unique_percentage": round(
            float(nonnull.nunique(dropna=True) / max(len(nonnull), 1) * 100), 2
        ),
        "sample_values": [_jsonable(v) for v in nonnull.head(MAX_SAMPLE_VALUES).tolist()],
    }
    if kind in {"integer", "float"} and not nonnull.empty:
        nums = pd.to_numeric(nonnull, errors="coerce").dropna()
        if not nums.empty:
            out.update(
                {
                    "min": float(nums.min()),
                    "max": float(nums.max()),
                    "mean": round(float(nums.mean()), 4),
                }
            )
    return out


def profile_workbook(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    sheets = pd.read_excel(path, sheet_name=None)
    workbook: dict[str, Any] = {"file_name": path.name, "sheets": []}
    for sheet_name, df in sheets.items():
        df = df.copy()
        sheet = {
            "sheet_name": str(sheet_name),
            "rows": int(len(df)),
            "columns": [profile_column(df[c]) for c in df.columns],
            "column_names": [str(c) for c in df.columns],
            "sample_rows": [
                {str(k): _jsonable(v) for k, v in row.items()}
                for row in df.head(MAX_SAMPLE_ROWS).to_dict(orient="records")
            ],
        }
        workbook["sheets"].append(sheet)
    return workbook


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: py src/profile_excel.py <file.xlsx>")
        raise SystemExit(2)
    print(json.dumps(profile_workbook(sys.argv[1]), ensure_ascii=False, indent=2))
