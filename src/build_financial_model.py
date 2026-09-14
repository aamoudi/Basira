from __future__ import annotations

import json
import sys
from pathlib import Path

from financial_model import build_financial_model


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: py src/build_financial_model.py <normalized_transactions.json>")
        raise SystemExit(2)

    input_path = Path(sys.argv[1])
    root = Path(__file__).resolve().parents[1]
    normalized = json.loads(input_path.read_text(encoding="utf-8"))
    model = build_financial_model(normalized)
    model["normalized_file"] = input_path.name

    output_path = root / "outputs" / f"{input_path.stem.replace('_normalized_transactions', '')}_financial_model.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved: {output_path}")
    print("\n[Basira Standard Financial Model]")
    for key in ("revenue", "cogs", "gross_profit", "gross_margin", "tax"):
        value = model["metrics"][key]["value"]
        if key == "gross_margin" and value is not None:
            print(f"  {key}: {value:.2%}")
        else:
            print(f"  {key}: {value}")
    print(f"  periods: {len(model['periods'])}")


if __name__ == "__main__":
    main()
