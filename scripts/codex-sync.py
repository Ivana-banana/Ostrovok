#!/usr/bin/env python3
"""Слой Codex из определений Claude Code: один источник правды — .claude/.

    python3 scripts/codex-sync.py           # записать .codex/agents/*.toml и .agents/skills/*/SKILL.md
    python3 scripts/codex-sync.py --check   # код 1, если сгенерированное разошлось с .claude/

.claude/agents/<роль>.md      → .codex/agents/<роль>.toml (агент Codex: name, description,
                                developer_instructions = шапка-перевод + текст роли).
.claude/skills/<скилл>/SKILL.md → .agents/skills/<скилл>/SKILL.md (обёртка: Codex ищет скиллы
                                в .agents/skills; полный текст и файлы скилла остаются в .claude/).
Модель и усилие агентам не задаём: Codex берёт их из сессии участника (AGENTS.md).
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_SRC = ROOT / ".claude" / "agents"
SKILLS_SRC = ROOT / ".claude" / "skills"
AGENTS_DST = ROOT / ".codex" / "agents"
SKILLS_DST = ROOT / ".agents" / "skills"
DESC_MAX = 1024  # предел description в формате Agent Skills
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def front_matter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        sys.exit(f"{path.relative_to(ROOT)}: нет front matter")
    fields = {}
    for line in m.group(1).splitlines():
        k = re.match(r"([a-z_-]+):\s*(.*)$", line)
        if k:
            fields[k.group(1)] = scalar(k.group(2))
    for key in ("name", "description"):
        if not fields.get(key):
            sys.exit(f"{path.relative_to(ROOT)}: нет поля {key}")
    return fields, m.group(2)


def scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        return json.loads(raw)
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw


def short(desc: str) -> str:
    if len(desc) <= DESC_MAX:
        return desc
    cut = desc[: DESC_MAX - 1]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: end + 1] if end > DESC_MAX // 2 else cut).rstrip() + "…"


def agent_toml(src: Path) -> str:
    fields, body = front_matter(src)
    tools = {t.strip() for t in fields.get("tools", "").split(",") if t.strip()}
    rel = src.relative_to(ROOT).as_posix()
    edit = (
        "Правишь файлы через apply_patch, только в выданном worktree."
        if tools & WRITE_TOOLS
        else "Файлы не правишь: apply_patch не вызываешь, shell — только чтение и проверки."
    )
    head = (
        f"Ты — агент Codex с ролью `{fields['name']}`. Роль общая с Claude Code: источник — `{rel}`.\n"
        "Правила проекта — `CLAUDE.md` и `AGENTS.md` в корне репозитория.\n"
        "Перевод инструментов из текста роли: Read, Grep, Glob — чтение файлов и `rg` в shell; "
        "Edit, Write — apply_patch; Bash — shell.\n"
        f"Инструменты роли: {fields.get('tools', 'все')}. {edit}\n"
        "Строки `model:` в тексте роли — для Claude Code; модель и усилие задаёт сессия Codex.\n"
    )
    instructions = head + "\n" + body.strip() + "\n"
    if "'''" in instructions:
        sys.exit(f"{rel}: в тексте роли есть ''' — TOML literal string сломается")
    return (
        f"# Сгенерировано scripts/codex-sync.py из {rel} — не править руками.\n"
        f"name = {json.dumps(fields['name'], ensure_ascii=False)}\n"
        f"description = {json.dumps(fields['description'], ensure_ascii=False)}\n"
        f"developer_instructions = '''\n{instructions}'''\n"
    )


def skill_wrapper(src: Path) -> str:
    fields, _ = front_matter(src)
    name = fields["name"]
    rel = src.relative_to(ROOT).as_posix()
    folder = src.parent.relative_to(ROOT).as_posix()
    return (
        "---\n"
        f"name: {json.dumps(name, ensure_ascii=False)}\n"
        f"description: {json.dumps(short(fields['description']), ensure_ascii=False)}\n"
        "---\n"
        f"<!-- Сгенерировано scripts/codex-sync.py из {rel} — не править руками. -->\n\n"
        f"# ${name} (Codex)\n\n"
        f"Скилл общий с Claude Code. Полный текст — `{rel}` (путь от корня репозитория).\n"
        "Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,\n"
        f"файлы рядом) — от папки `{folder}/`.\n\n"
        "Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:\n\n"
        '- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`\n'
        "  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.\n"
        "- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.\n"
    )


def expected() -> dict[Path, str]:
    out = {}
    for src in sorted(AGENTS_SRC.glob("*.md")):
        out[AGENTS_DST / f"{src.stem}.toml"] = agent_toml(src)
    for src in sorted(SKILLS_SRC.glob("*/SKILL.md")):
        out[SKILLS_DST / src.parent.name / "SKILL.md"] = skill_wrapper(src)
    return out


def generated() -> set[Path]:
    """Файлы, которые генератор уже писал (по строке-метке)."""
    found = set()
    for path in list(AGENTS_DST.glob("*.toml")) + list(SKILLS_DST.glob("*/SKILL.md")):
        if "Сгенерировано scripts/codex-sync.py" in path.read_text(encoding="utf-8"):
            found.add(path)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="только проверить, ничего не писать")
    args = ap.parse_args()
    want = expected()
    stale = [p for p, text in want.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    extra = sorted(generated() - set(want))
    if args.check:
        if stale or extra:
            for p in stale:
                print(f"устарел: {p.relative_to(ROOT)}", file=sys.stderr)
            for p in extra:
                print(f"лишний: {p.relative_to(ROOT)}", file=sys.stderr)
            print("слой Codex разошёлся с .claude/: python3 scripts/codex-sync.py и закоммить", file=sys.stderr)
            return 1
        print(f"слой Codex совпадает с .claude/: {len(want)} файлов")
        return 0
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(want[p], encoding="utf-8")
        print(f"записан: {p.relative_to(ROOT)}")
    for p in extra:
        p.unlink()
        if not any(p.parent.iterdir()):
            p.parent.rmdir()
        print(f"удалён: {p.relative_to(ROOT)}")
    if not stale and not extra:
        print("слой Codex уже совпадает с .claude/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
