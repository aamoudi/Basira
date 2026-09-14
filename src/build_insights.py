from __future__ import annotations

import json
import sys
from pathlib import Path

from insights_engine import build_insights


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: py src/build_insights.py <normalized.json> <financial_model.json>")
        raise SystemExit(2)

    normalized_path = Path(sys.argv[1])
    financial_path = Path(sys.argv[2])
    root = Path(__file__).resolve().parents[1]

    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    financial_model = json.loads(financial_path.read_text(encoding="utf-8"))
    result = build_insights(financial_model, normalized)

    output_path = root / "outputs" / f"{normalized_path.stem.replace('_normalized_transactions', '')}_insights.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved: {output_path}")
    print(json.dumps(result["insights"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
