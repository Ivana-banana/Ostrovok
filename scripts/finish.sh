#!/usr/bin/env bash
# Финиш ветки задачи одной командой (GIT.md, «Финиш»):
#   git fetch --prune && git merge origin/main → проверки по режиму → push → PR «Closes #N»
#   → merge после вердикта reviewer (когда он требуется) → уборка worktree.
# Конфликт — стоп: разреши, закоммить, запусти снова. GitHub отказал в merge — до трёх попыток.
#
#   bash scripts/finish.sh            # из worktree задачи, всё закоммичено
#   bash scripts/finish.sh --review   # потребовать вердикт reviewer и в режиме «финал»
#
# Проверки: сборка — make check; финал — make lint (тесты запускает только человек).
# reviewer: сборка — каждый PR; финал — метка contract у задачи, diff > 300 строк или --review.
# FINISH_REVIEW=important: в сборке reviewer — только при метке contract, diff > 300 или --review.
# Вердикт — комментарий reviewer в PR со строкой «Вердикт reviewer: merge <sha>». После
# вердикта в ветке не должно быть своих новых коммитов (merge origin/main допустим).
set -euo pipefail

# Windows keeps a running script file open: run from a temp copy so the worktree can be deleted at the end.
if [ -z "${FINISH_FROM_COPY:-}" ]; then
  copy="$(mktemp "${TMPDIR:-/tmp}/finish.XXXXXX")"
  cp "$0" "$copy"
  FINISH_FROM_COPY="$copy" exec bash "$copy" "$@"
fi
trap 'rm -f "$FINISH_FROM_COPY" 2>/dev/null || true' EXIT

die() { printf '[finish] %s\n' "$*" >&2; exit "${2:-1}"; }
say() { printf '[finish] %s\n' "$*"; }
force_review=false
[ "${1:-}" = --review ] && force_review=true

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "не git-репозиторий"
git remote get-url origin >/dev/null 2>&1 \
  || die "нет remote origin: PR создать некуда. Добавьте: git remote add origin <url GitHub> (README, «Запуск»)"
command -v gh >/dev/null 2>&1 || die "нет gh: установите GitHub CLI и выполните gh auth login"
gh auth status >/dev/null 2>&1 || die "gh не авторизован: gh auth login"

branch="$(git branch --show-current)"
n="$(printf '%s' "$branch" | sed -n 's/^worktree-\([0-9][0-9]*\)-.*/\1/p')"
[ -n "$n" ] || die "ветка «$branch» не worktree-<N>-<тема>"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "есть незакоммиченные правки"
wt="$(git rev-parse --show-toplevel)"
main_copy="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
[ "$wt" != "$main_copy" ] || die "запускай из worktree задачи, не из основной копии"
cd "$wt"
git fetch -q --prune || die "git fetch не прошёл: нет сети или доступа к origin"
[ -n "$(git log -1 --no-merges --format=%s origin/main..HEAD)" ] \
  || die "в ветке нет своих коммитов относительно origin/main — PR не создаю"

verdict_ok() { # есть вердикт merge, и после него нет своих новых коммитов
  local pr="$1" sha
  sha="$(gh pr view "$pr" --json comments --jq '.comments[].body' 2>/dev/null \
    | sed -n 's/.*Вердикт reviewer: merge \([0-9a-f]\{7,40\}\).*/\1/p' | tail -1)"
  [ -n "$sha" ] || return 1
  git cat-file -e "$sha^{commit}" 2>/dev/null || return 1
  git merge-base --is-ancestor "$sha" HEAD || return 1
  [ -z "$(git rev-list --no-merges "$sha..HEAD" ^origin/main)" ]
}

for try in 1 2 3; do
  git fetch -q --prune || die "git fetch не прошёл: нет сети или доступа к origin"
  git merge -q --no-edit origin/main || die "конфликт с main: разреши, закоммить и запусти снова"
  mode="$(bash scripts/mode.sh)"
  if [ "$mode" = финал ]; then
    say "режим финал: make lint (тесты — только по просьбе человека)"
    make -s lint || die "make lint красный"
  else
    say "режим сборка: make check (под общим замком тяжёлых прогонов, #333)"
    # heavy: общий замок тяжёлых прогонов (#333); скрипт идёт из временной копии — путь от корня репо
    # shellcheck source=heavy.sh
    . "$(git rev-parse --show-toplevel)/scripts/heavy.sh"
    rc=0; heavy make -s check || rc=$?
    [ "$rc" = 75 ] && die "make check не запускался: замок ${HEAVY_LOCK-/tmp/hack-heavy-tests.lock} занят — другая сессия гоняет тяжёлое, повтори"
    [ "$rc" = 0 ] || die "make check красный — почини до PR"
  fi
  title="$(git log -1 --no-merges --format=%s origin/main..HEAD)"
  [ -n "$title" ] || die "в ветке нет своих коммитов относительно origin/main — PR не создаю"
  git push -q -u origin HEAD || die "push не прошёл"
  pr="$(gh pr list --head "$branch" --state open --json number --jq '.[0].number // empty')"
  if [ -z "$pr" ]; then
    gh pr create --base main --head "$branch" --title "$title" --body "Closes #$n" >/dev/null \
      || die "gh pr create не прошёл"
    pr="$(gh pr list --head "$branch" --state open --json number --jq '.[0].number // empty')"
  fi
  [ -n "$pr" ] || die "PR для $branch не найден"

  need=false why=""
  if [ "$mode" = сборка ] && [ "${FINISH_REVIEW:-}" != important ]; then need=true why="режим сборка: reviewer на каждый PR"; fi
  if [ "$force_review" = true ]; then need=true why="--review"; fi
  if gh issue view "$n" --json labels --jq '.labels[].name' 2>/dev/null | grep -qx contract; then
    need=true why="метка contract"
  fi
  lines="$(git diff --shortstat origin/main...HEAD | awk '{print $4 + $6}')"
  if [ "${lines:-0}" -gt 300 ]; then need=true why="diff ${lines} строк > 300"; fi
  if [ "$need" = true ] && ! verdict_ok "$pr"; then
    say "PR #$pr ждёт вердикт reviewer ($why). Запусти reviewer на $(git rev-parse --short HEAD),"
    say "затем снова bash scripts/finish.sh. Merge не делаю."
    exit 3
  fi

  if gh pr merge "$pr" --merge >/dev/null 2>&1; then
    break
  fi
  [ "$try" -lt 3 ] || die "PR #$pr не влит за 3 попытки: gh pr view $pr"
  sleep 10
done

cd "$main_copy"
if [ -z "$(git status --porcelain --untracked-files=no)" ] && [ "$(git branch --show-current)" = main ]; then
  git pull -q --ff-only || say "основная копия не обновлена: pull --ff-only не прошёл"
fi
# Windows: worktree remove can fail on a leftover file after git has already unregistered the tree.
# Delete the leftover directory only then: a registered (maybe dirty) worktree is never deleted by force.
if ! git worktree remove "$wt"; then
  git worktree list --porcelain | grep -qxF "worktree $wt" && die "worktree не убран: $wt"
  # The caller's shell may still sit in $wt: Windows keeps that empty directory until the shell leaves it.
  rm -rf "$wt" 2>/dev/null || [ -z "$(ls -A "$wt" 2>/dev/null)" ] || die "каталог worktree не удалён: $wt"
  [ ! -e "$wt" ] || say "пустой каталог $wt останется, пока терминал в нём; удалите его после выхода"
fi
git branch -q -D "$branch"
git push -q origin --delete "$branch" 2>/dev/null || true
git worktree prune
say "#$n: PR #$pr влит, worktree убран"
