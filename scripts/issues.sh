#!/usr/bin/env bash
# GitHub Issues — очередь задач команды на сборке (клиент — gh). Очередь team.py
# планировщика живёт только на этапе проектирования; после /hackathon-kickoff задачи — только здесь.
#
#   bash scripts/issues.sh free [метка]        открытые и никем не взятые
#   bash scripts/issues.sh mine                мои открытые
#   bash scripts/issues.sh view <N>            задача целиком
#   bash scripts/issues.sh take <N> [--human]  взять. Занятая area: — предупреждение, не отказ.
#                                              Чужая задача без движения > TAKEOVER_MIN — перехват
#                                              с комментарием; --human — прямая просьба человека
#                                              «возьми #N»: перехват всегда, тоже с комментарием
#   bash scripts/issues.sh create <заголовок> <метки,через,запятую> <файл описания|->
#   bash scripts/issues.sh note <N> <файл|-|текст>   комментарий: итог, вопрос соседу, где остановился
#   bash scripts/issues.sh board               сводка для хука начала сессии
#   bash scripts/issues.sh check [путь]        можно ли здесь править: worktree задачи, задача моя
#   bash scripts/issues.sh labels [area:a,…]   создать метки команды (идемпотентно)
#   bash scripts/issues.sh bootstrap           час 0: метки + задачи «окружение» и «kickoff»
#
# TAKEOVER_MIN — минуты без движения до перехвата: окружение, иначе .env, иначе 45.
# Движение задачи — самое позднее из: обновление задачи, обновление её PR, последний коммит
# в ветке worktree-<N>-* на origin. Неизвестное движение не считается отсутствием движения.
# ISSUES_NOW (epoch) — «сейчас» для тестов.
set -euo pipefail

cmd="${1:-help}"
[ $# -gt 0 ] && shift

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

# Белый список: рабочие папки планировщика (.arch/<run> — worktree ветки arch/<run>,
# <input>/_arch — solo) и scratchpad агента. Это не код проекта и не работа по задаче.
whitelisted() {
  case "/$1/" in
    */.arch/*|*/_arch/*|*/scratchpad/*) return 0 ;;
  esac
  return 1
}

# check — правило «без взятой задачи файлы не меняем». Ветка проверяется без сети;
# назначение задачи сверяется с GitHub и кешируется на 30 минут. GitHub недоступен —
# предупреждение, не запрет: номер в ветке уже есть.
if [ "$cmd" = check ]; then
  target="${1:-.}"
  whitelisted "$target" && exit 0
  while [ ! -d "$target" ] && [ "$target" != / ] && [ "$target" != . ]; do target="$(dirname "$target")"; done
  git -C "$target" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0   # вне git — не наше
  home="$(cd "$(dirname "$0")" && cd "$(git rev-parse --git-common-dir)" && pwd -P)"
  cd "$target"
  [ "$(cd "$(git rev-parse --git-common-dir)" && pwd -P)" = "$home" ] || exit 0  # другой репозиторий
  branch="$(git branch --show-current 2>/dev/null || true)"
  case "$branch" in arch/*) exit 0 ;; esac   # worktree team.py планировщика
  how="Сначала задача: bash scripts/issues.sh free → take <N> → git worktree add .claude/worktrees/<N>-<тема> -b worktree-<N>-<тема> origin/main (GIT.md)"
  if [ "$(git rev-parse --absolute-git-dir)" = "$(cd "$(git rev-parse --git-common-dir)" && pwd -P)" ]; then
    err "Основная копия (${branch:-detached}): здесь файлы не меняют и не коммитят. $how"
    exit 1
  fi
  n="$(printf '%s' "$branch" | sed -n 's/^worktree-\([0-9][0-9]*\)-.*/\1/p')"
  if [ -z "$n" ]; then
    err "Ветка «$branch» без номера задачи: работа начинается только со взятой задачи. $how"
    exit 1
  fi
  cache="$(git rev-parse --git-dir)/issue-check"
  if [ -f "$cache" ] && [ "$(cat "$cache")" = "$n" ] && [ -z "$(find "$cache" -mmin +30 2>/dev/null)" ]; then
    exit 0
  fi
  if ! command -v gh >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    err "[issues] нет gh или jq — задача #$n не сверена с GitHub (GIT.md, «Задачи»)"
    exit 0
  fi
  if ! verdict="$(ISSUES_TIMEOUT=6 bash "$0" verify "$n" 2>&1)"; then
    case "$verdict" in
      нет:*) err "#$n: ${verdict#нет: }. $how"; exit 1 ;;
      *) err "[issues] GitHub недоступен — задача #$n не сверена (${verdict:0:200})"; exit 0 ;;
    esac
  fi
  printf '%s' "$n" > "$cache"
  exit 0
fi

if [ "$cmd" = help ] || [ "$cmd" = -h ] || [ "$cmd" = --help ]; then
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

command -v gh >/dev/null 2>&1 || { err "нет gh: установите GitHub CLI и выполните gh auth login"; exit 2; }
command -v jq >/dev/null 2>&1 || { err "нет jq: установите jq ≥ 1.6"; exit 2; }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MAIN="$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
now="${ISSUES_NOW:-$(date +%s)}"

env_get() { # env_get <ключ>: окружение, затем .env worktree, затем .env основной копии
  local k="$1" f v
  v="${!k:-}"
  [ -n "$v" ] && { printf '%s' "$v"; return; }
  for f in "$ROOT/.env" "${MAIN:-$ROOT}/.env"; do
    [ -f "$f" ] || continue
    v="$(sed -n "s/^[[:space:]]*$k[[:space:]]*=[[:space:]]*[\"']\{0,1\}\([0-9][0-9]*\).*/\1/p" "$f" | tail -1)"
    [ -n "$v" ] && { printf '%s' "$v"; return; }
  done
}
takeover_min="$(env_get TAKEOVER_MIN)"
takeover_min="${takeover_min:-45}"
takeover_s=$((takeover_min * 60))

gh_() { timeout "${ISSUES_TIMEOUT:-20}" gh "$@"; }
me_login() { gh_ api user --jq .login; }
issue_fields=number,title,state,labels,assignees,createdAt,updatedAt
open_issues() { gh_ issue list --state open --limit 300 --json "$issue_fields"; }

# activity: открытые PR и ветки worktree-* с origin. Ошибка — код 1: «не знаю» ≠ «движения нет».
prs='[]' brs='[]' known=false
activity() {
  local p b
  p="$(gh_ pr list --state open --limit 300 --json number,headRefName,updatedAt 2>/dev/null)" || return 1
  printf '%s' "$p" | jq -e 'type == "array"' >/dev/null 2>&1 || return 1
  GIT_TERMINAL_PROMPT=0 timeout 20 git fetch -q --prune origin \
    '+refs/heads/worktree-*:refs/remotes/origin/worktree-*' 2>/dev/null || return 1
  b="$(git for-each-ref --format='%(refname:lstrip=3)	%(committerdate:unix)' 'refs/remotes/origin/worktree-*' |
    jq -Rsc '[split("\n")[] | select(length > 0) | split("\t") | {n: .[0], t: (.[1] | tonumber)}]')"
  prs="$p" brs="$b" known=true
}

# jq-библиотека: moved — движение задачи (epoch), branch — её ветка, fresh — движение свежее порога.
lib='def epoch: sub("\\.[0-9]+"; "") | fromdateiso8601;
  def prefix: "worktree-\(.number)-";
  def branches: prefix as $p | ((.createdAt // null) | if . == null then 0 else epoch end) as $c
    | [$brs[] | select(.n | startswith($p)) | select(.t >= $c)];
  def moved: prefix as $p
    | ([.updatedAt | select(. != null) | epoch]
       + [$prs[] | select(.headRefName | startswith($p)) | .updatedAt | epoch]
       + [branches[].t]) | max // 0;
  def branch: branches | max_by(.t) | .n // "";
  def fresh: ($now - moved) <= $stale;
  def logins: [.assignees[].login];
  def others($me): [logins[] | select(. != $me)];
  def hm: strflocaltime("%d.%m %H:%M");
  def mins: (. / 60 | floor | tostring) + " мин";
  def areas: [.labels[].name | select(startswith("area:"))];'
q() { # q [аргументы jq] <фильтр> < json
  local f="${*: -1}"
  set -- "${@:1:$#-1}"
  jq -r --argjson prs "$prs" --argjson brs "$brs" --argjson now "$now" \
    --argjson stale "$takeover_s" "$@" "$lib $f"
}

row='"#\(.number) \(.title) [\([.labels[].name] | join(", "))] — \(if (.assignees | length) > 0
  then ([.assignees[].login] | join(", ")) else "свободна" end)"'

case "$cmd" in
  free)
    open_issues | jq -r --arg l "${1:-}" \
      ".[] | select((.assignees | length) == 0) | select(\$l == \"\" or any(.labels[]; .name == \$l)) | $row"
    ;;
  mine)
    me="$(me_login)"
    open_issues | jq -r --arg me "$me" \
      ".[] | select(any(.assignees[]; .login == \$me)) | $row"
    ;;
  view)
    gh_ issue view "${1:?номер задачи}"
    ;;
  verify)   # для check: задача открыта и назначена на меня
    n="${1:?номер}"
    me="$(me_login)" || { echo "gh api user не ответил"; exit 2; }
    issue="$(gh_ issue view "$n" --json "$issue_fields" 2>&1)" || {
      case "$issue" in *"Could not resolve"*|*"not found"*) echo "нет: задачи #$n нет"; exit 1 ;; esac
      echo "gh issue view: ${issue:0:200}"; exit 2; }
    [ "$(printf '%s' "$issue" | jq -r .state)" = OPEN ] || { echo "нет: задача закрыта"; exit 1; }
    printf '%s' "$issue" | jq -e --arg me "$me" 'any(.assignees[]; .login == $me)' >/dev/null \
      || { echo "нет: не назначена на $me (bash scripts/issues.sh take $n)"; exit 1; }
    ;;
  take)
    n="${1:?номер задачи}" human=false
    [ "${2:-}" = --human ] && human=true
    me="$(me_login)" || { err "gh api user не ответил — gh auth login"; exit 2; }
    issue="$(gh_ issue view "$n" --json "$issue_fields" 2>/dev/null)" || issue=""
    if ! printf '%s' "$issue" | jq -e --argjson n "$n" \
      'type == "object" and .number == $n and .state == "OPEN" and (.assignees | type == "array")' >/dev/null 2>&1; then
      err "#$n: задача не прочитана или не открыта — ничего не меняю."
      exit 1
    fi
    activity || true
    other="$(printf '%s' "$issue" | q --arg me "$me" 'others($me) | join(", ")')"
    moved="$(printf '%s' "$issue" | q 'moved')"
    branch="$(printf '%s' "$issue" | q 'branch')"
    age=$((now - moved))
    since="$(q -n "$moved | hm") ($(q -n "$age | mins") назад)"
    note=""
    if [ -n "$other" ]; then
      if [ "$human" = true ]; then
        note="Перехвачена $me у $other по прямой просьбе человека; последнее движение $since."
      elif [ "$known" != true ]; then
        err "#$n взята: $other. GitHub не отдал PR или ветки — движение неизвестно, не перехватываю."
        err "Прямая просьба человека «возьми #$n»: bash scripts/issues.sh take $n --human"
        exit 1
      elif [ "$age" -gt "$takeover_s" ]; then
        note="Перехвачена $me у $other: без движения с $since, порог TAKEOVER_MIN=$takeover_min."
      else
        err "#$n взята: $other; последнее движение $since."
        err "Перехват без просьбы человека — после $(q -n "$((moved + takeover_s)) | hm") (TAKEOVER_MIN=$takeover_min)."
        err "Сейчас — другую: bash scripts/issues.sh free. Прямая просьба человека: take $n --human"
        exit 1
      fi
      if [ -n "$branch" ]; then note="$note Ветка: $branch — продолжаю её."; else note="$note Ветки нет."; fi
    fi
    # Занятость area: — мягко: предупреждение, не отказ.
    for area in $(printf '%s' "$issue" | jq -r '.labels[].name | select(startswith("area:"))'); do
      busy="$(open_issues | q --arg me "$me" --argjson n "$n" --arg area "$area" --argjson known "$known" \
        '[.[] | select(.number != $n and any(.labels[]; .name == $area) and (others($me) | length) > 0
            and (($known | not) or fresh))
          | "#\(.number) \(others($me) | join(",")), движение \(moved | hm)"] | join("; ")')"
      [ -n "$busy" ] && err "[внимание] $area уже в работе: $busy. Беру, но правки в одних файлах — договоритесь в задаче (bash scripts/issues.sh note)."
    done
    args=(issue edit "$n" --add-assignee "$me")
    for o in $(printf '%s' "$issue" | q --arg me "$me" 'others($me)[]'); do args+=(--remove-assignee "$o"); done
    gh_ "${args[@]}" >/dev/null
    if [ -n "$note" ]; then
      printf '%s' "$note" | gh_ issue comment "$n" --body-file - >/dev/null
      say "#$n перехвачена: $me (у $other). Комментарий в задаче: $note"
    else
      say "#$n взята: $me."
    fi
    if [ -n "$branch" ]; then
      say "Ветка уже есть — продолжайте её, чужие коммиты не переписывать:"
      say "  git fetch origin $branch && git worktree add .claude/worktrees/${branch#worktree-} -b $branch origin/$branch"
    else
      say "Worktree: git worktree add .claude/worktrees/$n-<тема> -b worktree-$n-<тема> origin/main"
    fi
    ;;
  create)
    title="${1:?заголовок}" labels="${2:-}" body="${3:?файл описания или -}"
    args=(issue create --title "$title" --body-file "$body")
    [ -n "$labels" ] && args+=(--label "$labels")
    gh_ "${args[@]}"
    ;;
  note)
    n="${1:?номер задачи}" body="${2:?файл, - или текст}"
    if [ "$body" = - ] || [ -f "$body" ]; then
      gh_ issue comment "$n" --body-file "$body"
    else
      printf '%s' "$body" | gh_ issue comment "$n" --body-file -
    fi
    ;;
  board)
    me="$(me_login)"
    all="$(open_issues)"
    activity || true
    printf '%s' "$all" | jq -r '[.[] | select(any(.labels[]; .name == "blocker"))] as $b
      | if ($b | length) > 0 then "[blocker] открыты — раньше своей задачи:",
          ($b[] | "  #\(.number) \(.title)") else empty end'
    mine="$(printf '%s' "$all" | jq -r --arg me "$me" \
      '.[] | select(any(.assignees[]; .login == $me)) | "  #\(.number) \(.title) [\([.labels[].name] | join(", "))]"')"
    if [ -n "$mine" ]; then say "[issues] мои открытые ($me):"; say "$mine"
    else say "[issues] за $me открытых задач нет. Взять: bash scripts/issues.sh free → take <N>"; fi
    printf '%s' "$all" | q --arg me "$me" --argjson known "$known" \
      '[.[] | select((others($me) | length) > 0 and (any(.assignees[]; .login == $me) | not)
          and (($known | not) or fresh))
        | {a: (areas | join(", ") | if . == "" then "без area" else . end),
           w: "#\(.number) \(others($me) | join(",")) (движение \(moved | hm))"}]
      | if length > 0 then "[issues] в работе у других (та же area: — предупреждение при take):",
          (group_by(.a)[] | "  \(.[0].a): \([.[].w] | join("; "))") else empty end'
    if [ "$known" = true ]; then
      printf '%s' "$all" | q --arg me "$me" --arg m "$takeover_min" \
        '[.[] | select((others($me) | length) > 0 and (any(.assignees[]; .login == $me) | not) and (fresh | not))
          | "#\(.number) \(others($me) | join(",")), \(($now - moved) | mins)"]
        | if length > 0 then "[issues] можно перехватить (без движения > \($m) мин): \(join("; ")) — take <N>, продолжать её ветку"
          else empty end'
    else
      say "[issues] PR или ветки не прочитаны — кандидаты на перехват не показаны."
    fi
    free="$(printf '%s' "$all" | jq '[.[] | select((.assignees | length) == 0)] | length')"
    say "[issues] свободных: $free — bash scripts/issues.sh free"
    ;;
  labels)
    while IFS='|' read -r name color description; do
      [ -n "$name" ] || continue
      gh_ label create "$name" --color "$color" --description "$description" --force >/dev/null
      say "метка $name"
    done <<EOF
blocker|b60205|Риск показа или сдачи — закрыть раньше своей задачи
contract|5319e7|Меняет контракт: отдельный PR со списком потребителей, reviewer обязателен
P0|d93f0b|Без этого нет демо
P1|fbca04|Даёт баллы по критерию
P2|c2e0c6|Если останется время (ниже cut line)
area:infra|0e8a16|Окружение, CI, хуки, make, шаблон
$(printf '%s' "${1:-}" | tr ',' '\n' | sed -n 's/^\(area:[a-z0-9-]*\)$/\1|1d76db|Область сборки/p')
EOF
    ;;
  bootstrap)
    bash "$0" labels
    have="$(gh_ issue list --state all --limit 300 --json title --jq '.[].title')"
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    if ! printf '%s\n' "$have" | grep -qx 'Окружение и пресет'; then
      cat > "$tmp" <<'EOF'
**Цель:** окружение, которое не зависит от архитектуры: `make setup PRESET=<имя>`, `.env`, загрузка данных.
**Критерий условий:** — (инфраструктура)
**Приёмка:** `make setup PRESET=<имя> && make check` — зелёный на чистом клоне.
**Зависит от:** нет
**Сложность:** низкая · **Модель:** sonnet
EOF
      gh_ issue create --title 'Окружение и пресет' --label 'area:infra,P0' --body-file "$tmp"
    fi
    if ! printf '%s\n' "$have" | grep -qx 'Kickoff: выход планировщика → репозиторий'; then
      cat > "$tmp" <<'EOF'
**Цель:** `/hackathon-kickoff` переносит выход планировщика в CLAUDE.md, тесты, eval, eval-final, timeline и план задач.
**Критерий условий:** все критерии брифа видны в CLAUDE.md с весами.
**Приёмка:** `make check` зелёный; `make eval` печатает baseline; план задач подтверждён человеком.
**Зависит от:** «Окружение и пресет»
**Сложность:** средняя · **Модель:** opus
EOF
      gh_ issue create --title 'Kickoff: выход планировщика → репозиторий' --label 'area:infra,P0' --body-file "$tmp"
    fi
    ;;
  *)
    err "неизвестная команда: $cmd"; bash "$0" help >&2; exit 2
    ;;
esac
