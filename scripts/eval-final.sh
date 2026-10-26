#!/usr/bin/env bash
# Запечатанный holdout (eval-final). Печать — eval/holdout.tar.gz и его sha256 в
# eval/holdout.sha256; кладёт их /hackathon-kickoff из 02-scenarios-holdout.md планировщика.
# Агенты holdout не читают (хук check-paths.sh); открывает его только этот скрипт.
#
#   bash scripts/eval-final.sh --verify    # печать цела? (без распаковки, можно всегда)
#   EVAL_FINAL_CONFIRM=<sha коммита> make eval-final
#
# Условия запуска (иначе отказ):
#   1. режим «финал» — заморозка объявлена (CLAUDE.md, «Режимы»);
#   2. рабочее дерево чистое: оцениваем ровно коммит;
#   3. sha256 архива совпадает с печатью;
#   4. EVAL_FINAL_CONFIRM — префикс (≥ 7) sha HEAD: человек назвал замороженный коммит;
#   5. этот коммит ещё не оценивался: повтор после правок — новый эксперимент, а не «ещё раз».
# Результат — строка в eval/final-log.md: дата, коммит, кто, печать, итог, код выхода.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2
ARCHIVE=eval/holdout.tar.gz SEAL=eval/holdout.sha256 LOG=eval/final-log.md
die() { printf '[eval-final] %s\n' "$*" >&2; exit 2; }

[ -f "$ARCHIVE" ] && [ -f "$SEAL" ] || die "нет печати ($ARCHIVE, $SEAL): holdout кладёт /hackathon-kickoff"
want="$(awk '{print $1}' "$SEAL")"
have="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
[ "$want" = "$have" ] || die "печать нарушена: sha256 $have ≠ $want из $SEAL. Holdout меняли — оценка недействительна"
if [ "${1:-}" = --verify ]; then
  opened="$(grep -c '^| 20' "$LOG" 2>/dev/null)"
  echo "[eval-final] печать цела: ${want:0:12}; holdout открывался ${opened:-0} раз"
  exit 0
fi

[ "$(bash scripts/mode.sh)" = финал ] || die "режим не «финал»: holdout открываем только на заморозке"
[ -z "$(git status --porcelain)" ] || die "дерево не чистое — закоммить или убери правки: оцениваем ровно коммит"
head="$(git rev-parse HEAD)"
c="${EVAL_FINAL_CONFIRM:-}"
[ "${#c}" -ge 7 ] && [ "${head#"$c"}" != "$head" ] \
  || die "назови замороженный коммит: EVAL_FINAL_CONFIRM=${head:0:12} make eval-final"
# Повтор: тот же код (без учёта журнала), что у уже оценённого коммита.
for c in $(sed -n 's/^| [^|]* | \([0-9a-f]\{7,40\}\) |.*/\1/p' "$LOG" 2>/dev/null); do
  git cat-file -e "$c^{commit}" 2>/dev/null || continue
  if git diff --quiet "$c" HEAD -- . ":(exclude)$LOG"; then
    die "этот код уже оценивался как $c (строка в $LOG). Повтор — только после правок: новый коммит, новая строка"
  fi
done

tmp="$(mktemp -d)"; out="$(mktemp)"; trap 'rm -rf "$tmp" "$out"' EXIT
tar -xzf "$ARCHIVE" -C "$tmp" || die "архив не распаковался"
make -s --no-print-directory stack-eval-final HOLDOUT_DIR="$tmp" 2>&1 | tee "$out"
code="${PIPESTATUS[0]}"
result="$(grep -E '^RESULT:' "$out" | tail -1 | sed 's/^RESULT: *//; s/|/\//g')"
who="$(git config user.name || echo "${USER:-?}")"
[ -f "$LOG" ] || printf '# Журнал eval-final\n\n| Дата | Коммит | Кто | Печать | Итог | Код |\n|---|---|---|---|---|---|\n' > "$LOG"
printf '| %s | %s | %s | %s | %s | %s |\n' "$(date '+%Y-%m-%d %H:%M')" "${head:0:12}" "$who" \
  "${want:0:12}" "${result:-нет строки RESULT}" "$code" >> "$LOG"
echo "[eval-final] строка в $LOG. Закоммить журнал в задаче заморозки."
exit "$code"
