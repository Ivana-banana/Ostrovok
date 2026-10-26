# shellcheck shell=bash
# heavy <команда…>: тяжёлый прогон (make check, e2e) под общим замком HEAVY_LOCK (#330, #333).
# На одном ноутбуке несколько сессий: параллельные тяжёлые прогоны роняют тесты по таймауту.
# Замок уже держит предок-процесс flock (flock … bash scripts/finish.sh) — второй раз не берём: дочерний flock на тот же
# файл ждал бы родителя. HEAVY_LOCK= пуст — команда без замка.
# Нет flock (macOS, Git Bash) — запасной замок (#595): атомарный mkdir каталога "$HEAVY_LOCK.d", в нём файл owner
# "pid время". Замок с мёртвым pid, старше HEAVY_LOCK_MAX_AGE с (по умолчанию 3600) или без owner дольше
# HEAVY_LOCK_NOOWNER_SEC с (10) снимаем с сообщением в stderr, под мьютексом "$HEAVY_LOCK.d.break".
# Вложенный heavy узнаёт владельца по HEAVY_LOCK_OWNER (pid владельца, наследуется детьми) и замок второй раз не берёт.
# rm -rf с повтором: в Git Bash каталог, который в этот миг открыт другим процессом, сразу не удаляется.
# Сначала атомарный mv в своё имя: повторы удаляют только нашу копию, а не замок, который другой процесс создал
# заново между повторами (#602). mv не прошёл — одна попытка rm -rf по месту.
_heavy_rm() {
  local i gone="$1.rm.${BASHPID:-$$}"
  if mv "$1" "$gone" 2>/dev/null; then
    for i in 1 2 3 4 5 6 7 8 9 10; do
      rm -rf "$gone" 2>/dev/null
      [ -e "$gone" ] || return 0
      sleep 0.1
    done
    return 0
  fi
  rm -rf "$1" 2>/dev/null
  return 0
}

# Снять чужой замок $1 (владелец $2, время $3 — как мы их видели). Только под мьютексом "$1.break" и после повторного
# чтения owner: иначе мы бы удалили живой замок, который другой процесс взял, пока мы думали. Не взяли мьютекс — выходим,
# цикл ожидания повторит. Мьютекс, висящий дольше 10 с (breaker убит) — снимаем; время — по $SECONDS, не по кругам (#602).
_heavy_break() {
  local lock="$1" opid="$2" ots="$3" why="$4" cpid= cts= brk="$1.break"
  if ! mkdir "$brk" 2>/dev/null; then
    [ -n "${_HEAVY_BSTUCK:-}" ] || _HEAVY_BSTUCK=$SECONDS
    [ "$((SECONDS - _HEAVY_BSTUCK))" -gt 10 ] && { _heavy_rm "$brk"; _HEAVY_BSTUCK=; }
    return 0
  fi
  _HEAVY_BSTUCK=
  { read -r cpid cts <"$lock/owner"; } 2>/dev/null
  if [ "$cpid" = "$opid" ] && [ "$cts" = "$ots" ] && [ -d "$lock" ]; then
    echo "heavy: замок $lock $why — снимаю" >&2
    _heavy_rm "$lock"
  fi
  _heavy_rm "$brk"
}

# Снять свой замок $1 при выходе — только если владелец в нём всё ещё $2: замок могли отнять по возрасту.
_heavy_release() {
  local opid=
  { read -r opid _ <"$1/owner"; } 2>/dev/null
  [ "$opid" = "$2" ] && _heavy_rm "$1"
  return 0
}

_heavy_mkdir() {
  local lock="$1.d" wait="$2" max="${HEAVY_LOCK_MAX_AGE:-3600}" noowner="${HEAVY_LOCK_NOOWNER_SEC:-10}" opid ots now start missing=
  shift 2
  case "$max" in '' | *[!0-9]*) max=3600 ;; esac
  case "$noowner" in '' | *[!0-9]*) noowner=10 ;; esac
  # owner file of our own lock: a nested heavy runs the command at once. A stale variable (owner is gone) does not count.
  if [ -n "${HEAVY_LOCK_OWNER:-}" ] && { read -r opid _ <"$lock/owner"; } 2>/dev/null && [ "$opid" = "$HEAVY_LOCK_OWNER" ]     && kill -0 "$opid" 2>/dev/null; then
    "$@"
    return
  fi
  # a subshell: its EXIT trap and pid (the holder) are ours, the trap of the caller is not touched
  (
    me="${BASHPID:-$$}"
    start=$SECONDS
    until mkdir "$lock" 2>/dev/null; do
      # first: a lock that cannot be removed must not keep us here past the wait (#602)
      [ "$((SECONDS - start))" -ge "$wait" ] && exit 75
      now="$(date +%s)"
      opid= ots=
      if { read -r opid ots <"$lock/owner"; } 2>/dev/null && [ -n "$opid" ]; then
        missing=
        if ! kill -0 "$opid" 2>/dev/null; then
          _heavy_break "$lock" "$opid" "$ots" "держит мёртвый процесс $opid"
          continue
        elif [ -n "$ots" ] && [ "$((now - ots))" -gt "$max" ]; then
          _heavy_break "$lock" "$opid" "$ots" "старше $max с (владелец $opid)"
          continue
        fi
      elif [ -d "$lock" ]; then
        # no owner yet: the holder is between mkdir and its write. Over $noowner s — it died there.
        [ -n "$missing" ] || missing="$now"
        if [ "$((now - missing))" -gt "$noowner" ]; then
          _heavy_break "$lock" "" "" "без владельца"
          missing=
          continue
        fi
      fi
      sleep 0.2
    done
    echo "$me $(date +%s)" >"$lock/owner"
    trap '_heavy_release "$lock" "$me"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    export HEAVY_LOCK_OWNER="$me"
    "$@"
  )
}

# Код 75 — замок не получен за HEAVY_LOCK_WAIT с (по умолчанию 1800): команда не запускалась.
heavy() {
  local lock="${HEAVY_LOCK-/tmp/hack-heavy-tests.lock}" wait="${HEAVY_LOCK_WAIT:-1800}" p=$$
  case "$wait" in '' | *[!0-9]*) wait=1800 ;; esac
  if [ -z "$lock" ]; then "$@"; return; fi
  if ! command -v flock >/dev/null 2>&1; then _heavy_mkdir "$lock" "$wait" "$@"; return; fi
  while [ "$p" -gt 1 ] && [ -r "/proc/$p/cmdline" ]; do
    # only a flock process counts: a shell whose command text merely mentions flock and the lock is not one (review #338)
    if [ "$(cat "/proc/$p/comm" 2>/dev/null)" = flock ] && tr '\0' ' ' <"/proc/$p/cmdline" | grep -qF "$lock"; then
      "$@"
      return
    fi
    p="$(sed 's/.*) //' "/proc/$p/stat" 2>/dev/null | cut -d' ' -f2)"
    [ -n "$p" ] || break
  done
  flock -E 75 -w "$wait" "$lock" "$@"
}

# Direct run (bash scripts/heavy.sh <команда…>): the command under the lock, its code out (#379). It was a silent
# no-op with code 0 — a false green for «bash scripts/heavy.sh make check». Without a command: an error, not 0.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  [ "$#" -gt 0 ] || { echo "usage: bash scripts/heavy.sh <команда…>  (или: . scripts/heavy.sh; heavy <команда…>)" >&2; exit 2; }
  heavy "$@"
fi
