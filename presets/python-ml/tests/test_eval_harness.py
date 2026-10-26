import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_harness(tmp_path: Path, cases: list[dict]) -> str:
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    out = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/eval_harness.py"),
            "--checks",
            str(ROOT / "src/app/checks.py"),
            "--cases",
            str(tmp_path / "cases.json"),
            "--baseline",
            str(tmp_path / "none.json"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return out.stdout


def test_harness_counts_automated_and_missing(tmp_path):
    out = run_harness(
        tmp_path,
        [
            {"id": "EXAMPLE", "title": "пример", "critical": True, "input": "текст"},
            {"id": "S99", "title": "нет проверки", "critical": False},
        ],
    )
    assert "| EXAMPLE пример |" in out and "pass" in out
    assert "не автоматизирован" in out
    assert (
        out.strip()
        .splitlines()[-1]
        .startswith("RESULT: 1/2 сценариев (критических 1/1; автоматизировано 1)")
    )
