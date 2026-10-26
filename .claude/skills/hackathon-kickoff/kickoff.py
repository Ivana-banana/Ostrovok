#!/usr/bin/env python3
"""Механическая часть /hackathon-kickoff: выход architecture-planner → репозиторий.

    python3 .claude/skills/hackathon-kickoff/kickoff.py apply  --arch <папка> [--repo .]
    python3 .claude/skills/hackathon-kickoff/kickoff.py issues --arch <папка> --confirmed

apply — переносит файлы планировщика в репозиторий и печатает план задач. Issues не заводит.
issues — заводит Issues по <папка>/kickoff-plan.json; без --confirmed отказывает: план
сначала подтверждает человек.

Только стандартная библиотека. Заголовки разделов планировщика — на русском или английском.
Содержимое 02-scenarios-holdout.md скрипт не печатает: только запечатывает.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ASK = "[СПРОСИТЬ]"
TODAY = dt.date.today().isoformat()

HEADINGS = {
    "goal": ["цель", "goal"],
    "judges": ["что запустит жюри", "what the judges will run"],
    "missing": ["чего нет", "what is missing"],
    "constraints": ["жёсткие ограничения", "жесткие ограничения", "hard constraints"],
    "weights": ["веса критериев", "веса", "criterion weights"],
    "submission": ["сдача", "формат сдачи", "submission"],
    "trivial": ["тривиальный ответ", "the trivial answer", "trivial answer"],
    "traps": ["найденные ловушки", "ловушки", "traps found", "traps"],
    "milestones": ["вехи", "milestones"],
    "tasks": ["задачи", "tasks"],
    "budget": ["бюджет времени", "time budget"],
    "jury_q": ["вопросы жюри", "what a jury will ask", "jury will ask"],
    "decision": ["decision", "решение"],
}
PRESET_WORDS = {
    "ros2": ["ros 2", "ros2", "colcon", "rclpy", "rosbag", "humble"],
    "web-fullstack": ["fastapi", "react", "vite", "typescript", "веб", "web", "frontend", "интерфейс"],
    "python-ml": ["scikit", "sklearn", "pytorch", "torch", "catboost", "lightgbm", "xgboost",
                  "регресс", "классификатор", "модель", "ml", "pandas"],
}


# --- разбор markdown -------------------------------------------------------------------------

def sections(text: str, level: int = 2) -> list[tuple[str, str]]:
    mark = "#" * level + " "
    out, head, buf = [], "", []
    for line in text.splitlines():
        if line.startswith(mark):
            out.append((head, "\n".join(buf).strip()))
            head, buf = line[len(mark):].strip(), []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf).strip()))
    return out


def find(secs: list[tuple[str, str]], key: str) -> str:
    names = HEADINGS[key]
    for head, body in secs:
        h = head.lower()
        if any(h.startswith(n) for n in names):
            return body
    for head, body in secs:
        if any(n in head.lower() for n in names):
            return body
    return ""


def table(body: str) -> list[dict[str, str]]:
    rows = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return []
    cells = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]
    head = [h.lower() for h in cells(rows[0])]
    out = []
    for ln in rows[1:]:
        c = cells(ln)
        if all(re.fullmatch(r":?-{2,}:?", x or "--") for x in c):
            continue
        out.append({head[i]: (c[i] if i < len(c) else "") for i in range(len(head))})
    return out


def col(row: dict[str, str], *names: str) -> str:
    for n in names:
        for k, v in row.items():
            if k == n or k.startswith(n):
                return v
    return ""


def title_of(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return re.sub(r"^(бриф задачи|task brief|adr-\d+)\s*:\s*", "", line[2:].strip(), flags=re.I)
    return ""


def bullets(body: str) -> list[str]:
    return [re.sub(r"^\s*[-*]\s+", "", ln).strip() for ln in body.splitlines()
            if re.match(r"^\s*[-*]\s+", ln)]


def first_line(body: str, *prefixes: str) -> str:
    for ln in body.splitlines():
        s = re.sub(r"^\s*[-*]\s+", "", ln).strip()
        for p in prefixes:
            if s.lower().startswith(p.lower()):
                return s[len(p):].lstrip(" :—-").strip()
    return ""


DATE_RE = r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)"


# --- чтение выхода планировщика --------------------------------------------------------------

class Arch:
    def __init__(self, path: Path):
        self.path = path
        self.missing: list[str] = []

    def read(self, name: str) -> str:
        p = self.path / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
        self.missing.append(name)
        return ""

    def glob1(self, pattern: str) -> Path | None:
        found = sorted(self.path.glob(pattern))
        if not found:
            self.missing.append(pattern)
        return found[0] if found else None


def parse_scenarios(text: str) -> list[dict]:
    cases = []
    for head, body in sections(text, 3):
        m = re.match(r"(S\d+)\s*[—-]\s*(.+?)\s*\[([^\]]+)\]\s*$", head)
        if not m:
            continue
        case = {"id": m.group(1), "title": m.group(2).strip(), "category": m.group(3).strip()}
        fields = {"input": ["input", "вход"], "context": ["context", "контекст"],
                  "expected": ["expected behaviour", "expected", "ожидаемое"],
                  "pass": ["pass criterion", "критерий прохождения", "критерий"],
                  "weight": ["weight", "вес"]}
        # a field runs from its label ("Ожидаемое поведение:") to the next label line
        label = re.compile(r"^\s*(?:[-*]\s+)?([^:\n]{1,60}):(.*)$")
        chunks, cur = {}, None
        for ln in body.splitlines():
            m = label.match(ln)
            if m and not ln.lstrip().startswith(("-", "*")):
                cur = m.group(1).strip().lower()
                chunks[cur] = [m.group(2).strip()]
            elif cur:
                chunks[cur].append(ln.strip())
        for key, names in fields.items():
            hit = next((k for n in names for k in chunks if k.startswith(n)), None)
            case[key] = "\n".join(chunks[hit]).strip() if hit else ""
        case["critical"] = "critical" in case["weight"].lower() or "крит" in case["weight"].lower()
        case["jury_click"] = "jury-click" in body.lower()
        cases.append(case)
    return cases


# --- запись в репозиторий --------------------------------------------------------------------

class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.written: list[str] = []
        self.questions: list[str] = []

    def write(self, rel: str, text: str | bytes) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        if rel not in self.written:
            self.written.append(rel)

    def read(self, rel: str) -> str:
        p = self.root / rel
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def ask(self, what: str) -> str:
        self.questions.append(what)
        return f"{ASK} {what}"


def fill_block(text: str, name: str, body: str) -> str:
    pat = re.compile(rf"(<!-- kickoff:{name} -->\n).*?(\n<!-- /kickoff:{name} -->)", re.S)
    if not pat.search(text):
        raise SystemExit(f"в CLAUDE.md нет блока kickoff:{name}")
    return pat.sub(lambda m: m.group(1) + body.strip("\n") + m.group(2), text)


def choose_preset(adr: str) -> tuple[str, list[str]]:
    low = adr.lower()
    score = {p: [w for w in ws if re.search(rf"(?<![a-zа-я]){re.escape(w)}", low)]
             for p, ws in PRESET_WORDS.items()}
    if score["ros2"]:
        return "ros2", score["ros2"]
    if score["web-fullstack"]:
        return "web-fullstack", score["web-fullstack"] + score["python-ml"]
    if score["python-ml"]:
        return "python-ml", score["python-ml"]
    return "", []


def stem_words(s: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-яё]{5,}", s.lower())}


def sealed_archive(files: dict[str, bytes]) -> bytes:
    """Детерминированный tar.gz: тот же вход — тот же sha256."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(files[name]), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(files[name]))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def apply(arch_dir: Path, root: Path, stage: bool) -> int:
    arch, repo = Arch(arch_dir), Repo(root)
    brief = arch.read("01-task-brief.md")
    probe = arch.read("00b-probe.md")
    domain = arch.read("00c-domain.md")
    scen = arch.read("02-scenarios.md")
    holdout = arch.read("02-scenarios-holdout.md")
    plan = arch.read("BUILD-PLAN.md")
    report = arch.read("ARCHITECTURE-REPORT.md")
    adr_path = arch.glob1("ADR-*.md")
    adr = adr_path.read_text(encoding="utf-8") if adr_path else ""
    bs = sections(brief)

    # 01-task-brief.md → CLAUDE.md
    claude = repo.read("CLAUDE.md")
    if not claude:
        raise SystemExit("нет CLAUDE.md в корне репозитория")
    goal = find(bs, "goal")
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(goal.split()))
    is_user = lambda x: re.match(r"^(пользователь|user|для кого|for whom)\s*:", x, re.I)
    user = next((re.sub(r"^[^:]+:\s*", "", x) for x in sentences if is_user(x)), "")
    goal_text = " ".join(x for x in sentences if x and not is_user(x))
    constraints = find(bs, "constraints")
    defense = (first_line(constraints, "Защита", "Defense", "Defence")
               or first_line(brief, "Защита", "Defense")).rstrip(". ")
    name = title_of(brief) or repo.ask("название задачи")
    claude = fill_block(claude, "task", "\n".join([
        f"**{name}.** {goal_text or repo.ask('цель и критерий успеха (бриф пуст)')}",
        "",
        f"- Пользователь: {user or repo.ask('кто пользователь и что он делает сейчас')}",
        "- Условия — `task/` (копия страницы хакатона). Исследование — `research/`,",
        "  справка по предметной области — `research/domain.md`.",
        f"- Защита: {defense or repo.ask('дата, время и регламент защиты')}. Вехи — `docs/timeline.md`.",
        "- Неизвестный факт пиши как `[СПРОСИТЬ]` и строкой в `docs/questions-for-organizers.md`.",
    ]))

    weights = table(find(bs, "weights"))
    crit = [(col(r, "критерий", "criterion"), col(r, "вес", "weight")) for r in weights]
    crit = [(c, w) for c, w in crit if c and re.search(r"\d", w) and float(re.sub(r"[^\d.]", "", w) or 0) > 0]
    if crit:
        rows = "\n".join(f"| {c} | {re.sub(r'[^0-9.]', '', w)}% |" for c, w in crit)
    else:
        rows = f"| {repo.ask('критерии жюри и веса')} | % |"
    claude = fill_block(claude, "criteria", "| Критерий | Вес |\n|---|---|\n" + rows)

    cons = [c for c in bullets(constraints) if not re.match(r"^(защита|defen[cs]e)\b", c, re.I)]
    claude = fill_block(claude, "constraints", "\n".join(f"- {c}" for c in cons)
                        or f"- {repo.ask('жёсткие ограничения организаторов')}")

    # 00b-probe.md → ловушки, тест на каждую, baseline
    ps = sections(probe)
    traps = []
    for i, r in enumerate(table(find(ps, "traps")), 1):
        t = col(r, "ловушка", "trap")
        if not t or t in {"—", "-"}:
            continue
        traps.append({"id": f"TRAP-{i}", "trap": t, "where": col(r, "где", "where"),
                      "evidence": col(r, "доказательство", "evidence"),
                      "consequence": col(r, "следствие", "consequence")})
    trivial = " ".join(find(ps, "trivial").split())
    trivial = re.sub(r"\s*(Это планка|This is the bar)\.?\s*$", "", trivial, flags=re.I).rstrip(". ")
    lines = [f"- `{t['id']}` {t['trap']} — {t['where']}: {t['consequence']}" for t in traps]
    if not traps:
        lines.append(f"- {repo.ask('ловушки данных (probe не нашёл или его нет)')}")
    lines.append(f"- Тривиальный ответ: {trivial or repo.ask('тривиальный ответ и его метрика')}."
                 " Это планка: `make eval` печатает её (`eval/baseline.json`).")
    lines.append("- Тест на каждую ловушку содержит её id (`TRAP-1`…); `make check` это проверяет.")
    claude = fill_block(claude, "traps", "\n".join(lines))
    repo.write("eval/traps.json", json.dumps(traps, ensure_ascii=False, indent=2))
    repo.write("eval/baseline.json", json.dumps(
        {"rule": trivial, "source": "00b-probe.md", "note": "планка: система должна быть лучше"},
        ensure_ascii=False, indent=2))

    judges = find(bs, "judges").strip()
    submission = find(bs, "submission") or first_line(judges, "Сдача", "Submission")
    judges_text = "\n".join(ln for ln in judges.splitlines()
                            if not re.match(r"^\s*(сдача|submission)\s*:", ln, re.I))
    claude = fill_block(claude, "jury-run", "\n".join([
        judges_text or repo.ask("что именно запустит жюри: команда, данные, условия, метрика"),
        "",
        f"Формат сдачи: {submission or repo.ask('формат и состав сдачи')}",
        "Демо-путь жюри — `make demo`; сквозной сценарий — `make e2e`.",
    ]))

    # ADR → docs/adr, D-001, пресет
    preset, why = choose_preset(adr)
    stack_now = re.search(r"^STACK\s*:?=\s*(\S+)", repo.read("stack.mk"), re.M)
    adr_title = title_of(adr) or repo.ask("ADR планировщика")
    adr_rel = f"docs/adr/{adr_path.name}" if adr_path else ""
    if adr_path:
        repo.write(adr_rel, adr)
    decision = find(sections(adr), "decision")
    stack_lines = [f"Архитектура — `{adr_rel or 'ADR нет'}`: {adr_title}."]
    if decision:
        stack_lines.append(" ".join(decision.split())[:400])
    if preset:
        stack_lines.append(f"Пресет: `{preset}` (`make setup PRESET={preset}`; признаки в ADR: {', '.join(why[:5])}).")
    else:
        stack_lines.append("Пресет не подходит: стек с нуля — свой `stack.mk` по контракту `Makefile`.")
    if stack_now and preset and stack_now.group(1) != preset:
        stack_lines.append(f"ВНИМАНИЕ: уже стоит пресет `{stack_now.group(1)}`, ADR просит `{preset}` — решает человек.")
    stack_lines.append("Контракты — `contracts/` (пишет kickoff по ADR); потребители — в README контракта.")
    claude = fill_block(claude, "stack", "\n".join(stack_lines))
    repo.write("CLAUDE.md", claude)

    dec = repo.read("docs/decisions.md") or "# Решения\n\nСтрока — что выбрали, до 200 символов. Почему — в коммите.\n"
    if "D-001" not in dec:
        line = f"- D-001 {TODAY}: архитектура — {adr_title}; стек — {('пресет ' + preset) if preset else 'с нуля'} ({adr_rel})."
        dec = dec.rstrip("\n") + "\n\n" + line[:200] + "\n"
        repo.write("docs/decisions.md", dec)

    # 00c-domain.md → research/domain.md
    if domain:
        repo.write("research/domain.md", f"<!-- из выхода планировщика: 00c-domain.md, {TODAY} -->\n" + domain)

    # 02-scenarios.md → eval/cases.json (e2e — сценарии jury-click)
    cases = parse_scenarios(scen)
    repo.write("eval/cases.json", json.dumps(cases, ensure_ascii=False, indent=2))

    # 02-scenarios-holdout.md → запечатанный eval-final
    seal = ""
    if holdout:
        files = {"scenarios.md": holdout.encode("utf-8"),
                 "cases.json": json.dumps(parse_scenarios(holdout), ensure_ascii=False, indent=2).encode()}
        blob = sealed_archive(files)
        seal = hashlib.sha256(blob).hexdigest()
        repo.write("eval/holdout.tar.gz", blob)
        repo.write("eval/holdout.sha256", f"{seal}  eval/holdout.tar.gz")
        if not repo.read("eval/final-log.md"):
            repo.write("eval/final-log.md", "# Журнал eval-final\n\n| Дата | Коммит | Кто | Печать | Итог | Код |\n|---|---|---|---|---|---|")
    else:
        repo.ask("holdout-сценарии (02-scenarios-holdout.md нет) — eval-final не запечатан")

    # «What a jury will ask» → pitch/QA.md
    qa = find(sections(report), "jury_q")
    repo.write("pitch/QA.md", "# Вопросы жюри\n\nИсточник — отчёт планировщика. Заготовка для людей к защите; "
               "агенты её не ведут.\n\n" + (qa or f"- {repo.ask('вопросы жюри (в отчёте нет раздела)')}"))

    # BUILD-PLAN.md → план задач и вехи
    pls = sections(plan)
    budget = find(pls, "budget")
    when = {m.group(1): m.group(2).replace("T", " ") for m in re.finditer(rf"\b(M\d)\b\s*[—:-]\s*{DATE_RE}", budget)}
    freeze = re.search(rf"(?:заморозка|freeze)\s*[—:-]\s*{DATE_RE}", budget, re.I)
    cut = first_line(budget, "Cut line", "Линия отсечения")
    cut_ids = set(re.findall(r"\bT\d+\b", cut))
    rows = ["| Веха | Когда | Что готово | Проверка |", "|---|---|---|---|"]
    for m in re.finditer(r"^\s*(M\d)\s*[—:-]\s*(.+)$", find(pls, "milestones"), re.M):
        rows.append(f"| {m.group(1)} | {when.get(m.group(1), ASK)} | {m.group(2).strip()} | jury перед вехой |")
    rows.append(f"| Заморозка | {freeze.group(1) if freeze else ASK} | cut line; `Режим: финал`; `make eval-final` | jury на коммите |")
    defense_dt = re.search(DATE_RE, defense or "")
    rows.append(f"| Защита | {defense_dt.group(1) if defense_dt else ASK} | {re.sub(DATE_RE + r',?\s*', '', defense or '') or 'регламент'} | питч делают люди; `jury`, вопросы жюри |")
    if not when:
        repo.ask("даты вех M0–M3 (в BUILD-PLAN нет бюджета времени)")
    repo.write("docs/timeline.md", "# Вехи\n\nДата — `ГГГГ-ММ-ДД ЧЧ:ММ`: её читает хук начала сессии.\n\n"
               + "\n".join(rows) + f"\n\nCut line: {cut or ASK}\n")

    tasks = []
    crit_stems = [(c, stem_words(c)) for c, _ in crit]
    by_scen = {c["id"]: c for c in cases}
    for r in table(find(pls, "tasks")):
        tid, title = col(r, "#", "id"), col(r, "задача", "task")
        if not title:
            continue
        ms = col(r, "веха", "milestone")
        deps = re.findall(r"\bT\d+\b", col(r, "зависит", "depends"))
        scen_ids = re.findall(r"\bS\d+\b", col(r, "сценари", "scenario"))
        area = col(r, "область", "area").strip().lower().removeprefix("area:")
        cx = col(r, "сложность", "complexity").lower() or "средняя"
        est = col(r, "оценка", "estimate")
        text = title + " " + " ".join(by_scen[s]["title"] for s in scen_ids if s in by_scen)
        best = max(crit_stems, key=lambda cs: len(cs[1] & stem_words(text)), default=("", set()))
        criterion = best[0] if best[0] and best[1] & stem_words(text) else "[уточнить на kickoff]"
        prio = "P2" if tid in cut_ids else ("P0" if ms in {"M0", "M1"} else "P1")
        accept = (f"`make eval` — проходят {', '.join(scen_ids)}" if scen_ids else "`make check` зелёный") + \
                 (" и тесты ловушек" if "ловушк" in col(r, "сценари", "scenario").lower() else "")
        tasks.append({"id": tid, "title": f"{tid}: {title}", "goal": title, "milestone": ms,
                      "depends": deps, "scenarios": scen_ids, "estimate": est,
                      "area": area or "", "priority": prio, "complexity": cx,
                      "model": "opus" if cx.startswith(("выс", "high")) else "sonnet",
                      "criterion": criterion, "accept": accept, "issue": None})
        if not area:
            repo.ask(f"область (area:) для {tid}")
    plan_file = arch_dir / "kickoff-plan.json"
    plan_file.write_text(json.dumps({"arch": str(arch_dir), "tasks": tasks}, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    # Всё, чего нет, — [СПРОСИТЬ] и строка вопросов организаторам.
    gaps = find(bs, "missing")
    for g in bullets(gaps) or ([gaps.strip()] if gaps.strip() else []):
        repo.questions.append(f"нет во входе: {g}")
    for f in arch.missing:
        repo.questions.append(f"нет файла планировщика {f}")
    qfile = repo.read("docs/questions-for-organizers.md") or \
        "# Вопросы организаторам\n\nСтрока — вопрос, откуда он, ответ. Отвечено — `[x]` и ответ после «→».\n"
    for q in repo.questions:
        if q not in qfile:
            qfile = qfile.rstrip("\n") + f"\n- [ ] {q} — kickoff {TODAY}"
    repo.write("docs/questions-for-organizers.md", qfile)

    if stage and subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True).returncode == 0:
        subprocess.run(["git", "-C", str(root), "add", "--", *repo.written], check=False)

    # Отчёт в чат
    print(f"# Kickoff: {name}\n")
    print("Записано: " + ", ".join(f"`{w}`" for w in repo.written))
    print(f"Критериев: {len(crit)}; ловушек: {len(traps)} (тест на каждую — TRAP-id); "
          f"видимых сценариев: {len(cases)}, из них jury-click (e2e): "
          f"{', '.join(c['id'] for c in cases if c['jury_click']) or 'нет'}")
    print(f"eval-final: {'запечатан, sha256 ' + seal[:12] if seal else 'НЕ запечатан'}")
    print(f"Пресет: {preset or 'стек с нуля'}" + (f" (уже стоит {stack_now.group(1)})" if stack_now else ""))
    print(f"[СПРОСИТЬ]: {len(repo.questions)} → docs/questions-for-organizers.md\n")
    print("## План задач (Issues НЕ заведены — нужно подтверждение человека)\n")
    print("| # | Задача | Веха | Приоритет | area | Зависит от | Приёмка | Критерий | Сложность / модель |")
    print("|---|---|---|---|---|---|---|---|---|")
    for t in tasks:
        print(f"| {t['id']} | {t['goal']} | {t['milestone']} | {t['priority']} | {t['area'] or ASK} | "
              f"{', '.join(t['depends']) or '—'} | {t['accept']} | {t['criterion']} | {t['complexity']} / {t['model']} |")
    print(f"\nПлан: {plan_file}. После «да» человека:\n"
          f"  python3 .claude/skills/hackathon-kickoff/kickoff.py issues --arch {arch_dir} --confirmed")
    return 0


def issues(arch_dir: Path, confirmed: bool, root: Path) -> int:
    plan_file = arch_dir / "kickoff-plan.json"
    if not confirmed:
        print("Issues заводятся только после подтверждения плана человеком: добавь --confirmed.", file=sys.stderr)
        return 2
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in plan["tasks"]}
    missing = [t["id"] for t in tasks.values() if not t["area"]]
    if missing:
        print(f"нет area: у {', '.join(missing)} — допиши в {plan_file}", file=sys.stderr)
        return 2
    areas = ",".join(sorted({f"area:{t['area']}" for t in tasks.values()}))
    sh = lambda *a: subprocess.run(["bash", str(root / "scripts/issues.sh"), *a], check=True,
                                   capture_output=True, text=True).stdout
    sh("labels", areas)
    done: set[str] = set()
    while len(done) < len(tasks):
        ready = [t for t in tasks.values() if t["id"] not in done
                 and all(d in done or d not in tasks for d in t["depends"])]
        if not ready:
            print("цикл в «Зависит от»: " + ", ".join(set(tasks) - done), file=sys.stderr)
            return 2
        for t in ready:
            if t["issue"] is None:
                deps = ", ".join(f"#{tasks[d]['issue']} ({d})" for d in t["depends"] if d in tasks) or "нет"
                body = (f"**Цель:** {t['goal']}\n"
                        f"**Критерий условий:** {t['criterion']}\n"
                        f"**Приёмка:** {t['accept']}\n"
                        f"**Зависит от:** {deps}\n"
                        f"**Сложность:** {t['complexity']} · **Модель:** {t['model']} · "
                        f"**Веха:** {t['milestone']} · **Оценка:** {t['estimate'] or '—'}\n"
                        f"**Сценарии:** {', '.join(t['scenarios']) or '—'}\n")
                url = subprocess.run(["bash", str(root / "scripts/issues.sh"), "create", t["title"],
                                      f"area:{t['area']},{t['priority']}", "-"], input=body, check=True,
                                     capture_output=True, text=True).stdout.strip()
                num = re.search(r"(\d+)\s*$", url)
                if not num:
                    print(f"gh не вернул номер задачи для {t['id']}: {url}", file=sys.stderr)
                    return 2
                t["issue"] = int(num.group(1))
                plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"{t['id']} → #{t['issue']} {url}")
            done.add(t["id"])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["apply", "issues"])
    ap.add_argument("--arch", required=True, type=Path, help="рабочая папка планировщика (_arch/ или .arch/<run>/)")
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--confirmed", action="store_true", help="план подтверждён человеком")
    ap.add_argument("--no-stage", action="store_true", help="не делать git add записанных файлов")
    a = ap.parse_args(argv)
    if not a.arch.is_dir():
        raise SystemExit(f"нет папки планировщика: {a.arch}")
    if a.command == "apply":
        return apply(a.arch.resolve(), a.repo.resolve(), not a.no_stage)
    return issues(a.arch.resolve(), a.confirmed, a.repo.resolve())


if __name__ == "__main__":
    sys.exit(main())
