from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: py src/run_pipeline.py <file.xlsx>")
        raise SystemExit(2)

    root = Path(__file__).resolve().parents[1]
    excel_path = Path(sys.argv[1])
    mapper = root / "src" / "run_mapper.py"
    normalizer = root / "src" / "build_normalized_transactions.py"
    financial = root / "src" / "build_financial_model.py"
    insights = root / "src" / "build_insights.py"

    for command in (
        [sys.executable, str(mapper), str(excel_path)],
    ):
        subprocess.run(command, cwd=root, check=True)

    mapping_path = root / "outputs" / f"{excel_path.stem}_mapping.json"
    subprocess.run(
        [sys.executable, str(normalizer), str(excel_path), str(mapping_path)],
        cwd=root,
        check=True,
    )

    normalized_path = root / "outputs" / f"{excel_path.stem}_normalized_transactions.json"
    subprocess.run(
        [sys.executable, str(financial), str(normalized_path)],
        cwd=root,
        check=True,
    )

    # Quick machine-readable pipeline summary for demo/testing.
    model_path = root / "outputs" / f"{excel_path.stem}_financial_model.json"
    subprocess.run(
        [sys.executable, str(insights), str(normalized_path), str(model_path)],
        cwd=root,
        check=True,
    )

    model = json.loads(model_path.read_text(encoding="utf-8"))
    print("\n[Pipeline Complete]")
    print(f"  mapping: {mapping_path.name}")
    print(f"  normalized: {normalized_path.name}")
    insights_path = root / "outputs" / f"{excel_path.stem}_insights.json"
    print(f"  financial_model: {model_path.name}")
    print(f"  insights: {insights_path.name}")


if __name__ == "__main__":
    main()
