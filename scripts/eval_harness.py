#!/usr/bin/env python3
"""Харнес eval для пресетов: сценарии планировщика → проверки проекта → таблица и RESULT.

    python3 scripts/eval_harness.py --checks <файл проверок> [--cases eval/cases.json]
    python3 scripts/eval_harness.py --checks <файл> --holdout-dir <dir>   # только из eval-final

Файл проверок проекта (Python) определяет:
    def check_S1(case: dict) -> bool | tuple[bool, str]   — проверка сценария S1;
    def metric(split: str) -> dict | None                  — {"name", "system", "baseline"}.
Сценарий без check_<id> — «не автоматизирован» (не пройден). Baseline — eval/baseline.json
(тривиальный ответ из probe). Последняя строка — «RESULT: …»: её пишет журнал eval-final.
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path


def _force_utf8_output() -> None:
    """Вывод в UTF-8 независимо от кодировки консоли (Windows cp1251 и т.п.)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_force_utf8_output()


def load_checks(path: Path):
    spec = importlib.util.spec_from_file_location("project_checks", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"нет файла проверок: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


def run(cases: list[dict], mod) -> tuple[list[tuple[dict, str, str]], int, int, int, int]:
    rows, passed, crit_total, crit_passed, automated = [], 0, 0, 0, 0
    for case in cases:
        fn = getattr(mod, f"check_{case['id']}", None)
        critical = bool(case.get("critical"))
        crit_total += critical
        if fn is None:
            rows.append((case, "не автоматизирован", ""))
            continue
        automated += 1
        try:
            res = fn(case)
            ok, note = (res if isinstance(res, tuple) else (bool(res), ""))
        except Exception as exc:  # noqa: BLE001 — проверка проекта не должна ронять харнес
            ok, note = False, f"исключение: {exc!r}"
            traceback.print_exc(file=sys.stderr)
        rows.append((case, "pass" if ok else "FAIL", note))
        passed += ok
        crit_passed += ok and critical
    return rows, passed, automated, crit_total, crit_passed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", required=True, type=Path)
    ap.add_argument("--cases", default=Path("eval/cases.json"), type=Path)
    ap.add_argument("--holdout-dir", type=Path)
    ap.add_argument("--baseline", default=Path("eval/baseline.json"), type=Path)
    a = ap.parse_args(argv)

    split = "holdout" if a.holdout_dir else "visible"
    cases_file = a.holdout_dir / "cases.json" if a.holdout_dir else a.cases
    if not cases_file.is_file():
        print(f"нет {cases_file}: сценарии кладёт /hackathon-kickoff из выхода планировщика")
        print("RESULT: сценариев нет")
        return 0
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    mod = load_checks(a.checks)

    rows, passed, automated, crit_total, crit_passed = run(cases, mod)
    print(f"## eval ({split}): {cases_file}\n")
    print("| Сценарий | Категория | Критический | Результат | Заметка |")
    print("|---|---|---|---|---|")
    for case, res, note in rows:
        print(f"| {case['id']} {case.get('title', '')} | {case.get('category', '')} | "
              f"{'да' if case.get('critical') else ''} | {res} | {note} |")

    base = json.loads(a.baseline.read_text(encoding="utf-8")) if a.baseline.is_file() else {}
    print(f"\nBaseline (тривиальный ответ): {base.get('rule') or 'нет eval/baseline.json'}")
    m = getattr(mod, "metric", None)
    mres: dict | None = m(split) if callable(m) else None
    metric_txt = ""
    if mres:
        metric_txt = f"; {mres['name']}: система {mres['system']} против baseline {mres['baseline']}"
        print(f"Метрика {mres['name']}: система {mres['system']}, baseline {mres['baseline']}")
        if mres["system"] is not None and mres["baseline"] is not None and mres["system"] <= mres["baseline"]:
            print("ВНИМАНИЕ: система не лучше тривиального ответа")
    else:
        print("Метрика: не реализована (metric() в файле проверок)")
    print(f"\nRESULT: {passed}/{len(cases)} сценариев (критических {crit_passed}/{crit_total}; "
          f"автоматизировано {automated}){metric_txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
