#!/usr/bin/env bash
# PreToolUse на Bash для Codex (.codex/hooks.json): запрещённые команды git и gh.
# У Claude Code тот же список — `permissions.deny` в .claude/settings.json; у Codex такого
# списка в репозитории нет, поэтому его держит этот хук. Правило — CLAUDE.md, «Как работаем»:
# force-push, rebase, --amend, add -A, push в main, reset --hard, clean, --no-verify,
# squash/rebase/admin merge, удаление репозитория.
# Команда разбирается по сегментам (; && || | и перевод строки); тело heredoc — данные.
# Отказ — код 2, причина в stderr. Нет jq или python3 — пропуск (остаются .githooks).
set -uo pipefail

input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
HOOKS="$(cd "$(dirname "$0")" && pwd)"
. "$HOOKS/lib.sh"
[ "$(printf '%s' "$input" | jq -r '.tool_name // ""')" = Bash ] || exit 0
cmd="$(printf '%s' "$input" | hook_command | awk '{print} /<</{exit}')"
printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_-])(git|gh)([[:space:]]|$)' || exit 0

reason="$(printf '%s' "$cmd" | python3 -I -c '
import re, shlex, sys

def segments(text):
    for part in re.split(r"&&|\|\||[;|\n&()]", text):
        try:
            yield shlex.split(part, comments=True)
        except ValueError:
            yield part.split()

def git_args(tokens):
    """Аргументы после «git [-C dir] [-c k=v]» или None."""
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens = tokens[1:]                      # VAR=value git ...
    if not tokens or tokens[0].rsplit("/", 1)[-1] != "git":
        return None
    a = tokens[1:]
    while a and a[0] in ("-C", "-c", "--git-dir", "--work-tree"):
        a = a[2:]
    return a

def git_reason(a):
    if not a:
        return None
    sub, rest = a[0], a[1:]
    opts = [x for x in rest if x.startswith("-")]
    refs = [x for x in rest if not x.startswith("-")]
    if sub == "push":
        if any(o in ("-f", "--force", "--force-with-lease", "--force-if-includes") or o.startswith("--force") for o in opts):
            return "force-push"
        if "--no-verify" in opts:
            return "push --no-verify"
        if any(r.startswith("+") for r in refs[1:]):
            return "force-push через +refspec"
        deleting = "--delete" in opts or "-d" in opts
        for r in refs[1:]:
            dst = r.split(":")[-1]
            if dst in ("main", "refs/heads/main"):
                return "удаление main" if deleting or r.startswith(":") else "push в main"
        return None
    if sub == "add":
        if any(o in ("-A", "--all", "-u", "--update") for o in opts) or "." in refs:
            return "git add -A / . / -u — добавляй явные пути"
        return None
    if sub == "commit":
        if any(o in ("-a", "--all", "--amend", "--no-verify", "-n") or re.fullmatch(r"-[a-zA-Z]*a[a-zA-Z]*", o) for o in opts):
            return "commit -a / --amend / --no-verify"
        return None
    if sub == "rebase":
        return "rebase"
    if sub == "pull" and any(o in ("--rebase", "-r") or o.startswith("--rebase=") and o != "--rebase=false" for o in opts):
        return "pull --rebase"
    if sub == "reset" and "--hard" in opts:
        return "reset --hard"
    if sub == "clean":
        return "git clean"
    if sub == "filter-branch":
        return "filter-branch"
    if sub == "lfs" and refs[:1] == ["migrate"]:
        return "git lfs migrate"
    if sub == "branch" and any(o in ("-d", "-D", "--delete") for o in opts) and "main" in refs:
        return "удаление ветки main"
    return None

def gh_reason(tokens):
    if not tokens or tokens[0].rsplit("/", 1)[-1] != "gh":
        return None
    a = tokens[1:]
    if a[:2] == ["pr", "merge"] and any(o in ("--squash", "-s", "--rebase", "-r", "--admin") for o in a[2:]):
        return "gh pr merge --squash / --rebase / --admin — только --merge (finish.sh)"
    if a[:2] == ["repo", "delete"]:
        return "gh repo delete"
    return None

for t in segments(sys.stdin.read()):
    why = gh_reason(t) or git_reason(git_args(t))
    if why:
        print(why)
        break
')"

[ -n "$reason" ] || exit 0
cwd="$(printf '%s' "$input" | jq -r '.cwd // "."')"
(cd "$cwd" 2>/dev/null && hook_log check-git "$reason")
echo "Запрещено правилами (CLAUDE.md, «Как работаем»; GIT.md): $reason. Нужен откат — GIT.md, «Безопасный откат»; финиш — bash scripts/finish.sh." >&2
exit 2
