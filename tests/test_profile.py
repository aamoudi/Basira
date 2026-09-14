import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from profile_excel import profile_workbook


def test_profile_clear_file():
    p = Path(__file__).resolve().parents[1] / "samples" / "01_clear_english.xlsx"
    profile = profile_workbook(p)
    assert profile["sheets"][0]["rows"] == 5
    names = profile["sheets"][0]["column_names"]
    assert "SalesValue" in names
    assert any(c["name"] == "ProductCost" and c["kind"] in {"integer", "float"} for c in profile["sheets"][0]["columns"])
