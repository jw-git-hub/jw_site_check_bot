#!/usr/bin/env bash
# Выкладка сайт-чекера на сервер с Мака (ТЗ, 13.2):
#   deploy/deploy.sh          выложить HEAD — он должен точно совпадать с origin/main
#   deploy/deploy.sh <sha>    откат: выложить прошлый коммит из origin/main
# Адрес, пользователь и путь сервера — из deploy/deploy.local.env (вне git). .env скрипт не создаёт, не читает
# и не копирует: он живёт только на сервере.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
LOCAL_CONFIG="deploy/deploy.local.env"
HEALTH_WAIT_SECONDS=60  # решение C17, ТЗ 13.2 п.5: ждать healthy до 60 секунд
LOG_LINES=30
CONTAINER_UID=10001  # пользователь внутри контейнера (Dockerfile): папка data должна быть его
SSH_UNREACHABLE_EXIT_CODE=255  # ssh: не удалось соединиться (хост не найден, порт недоступен и т.п.)
ROLLBACK_READY=0        # снимок состояния сервера ещё не снят — подсказывать откат рано
PREVIOUS_SHA_ON_SERVER=""

step() { printf '\n== %s\n' "$*"; }

# Если сервер уже тронут (снимок снят, ROLLBACK_READY=1), любая остановка подсказывает, чем откатиться:
# прошлым sha, который там был, или честно — что откатывать некуда (первая выкладка).
fail() {
  printf 'Выкладка остановлена: %s\n' "$*" >&2
  if [ "$ROLLBACK_READY" -eq 1 ]; then
    if [ -n "$PREVIOUS_SHA_ON_SERVER" ]; then
      printf 'откат: deploy/deploy.sh %s\n' "$PREVIOUS_SHA_ON_SERVER" >&2
    else
      printf 'откатывать некуда\n' >&2
    fi
  fi
  exit 1
}

[ -f "$LOCAL_CONFIG" ] || fail "нет $LOCAL_CONFIG (образец — deploy/deploy.local.env.example)"
# shellcheck source=/dev/null
source "$LOCAL_CONFIG"
[ -n "${DEPLOY_HOST:-}" ] && [ -n "${DEPLOY_USER:-}" ] && [ -n "${DEPLOY_PATH:-}" ] \
  || fail "в $LOCAL_CONFIG не заполнены DEPLOY_HOST, DEPLOY_USER, DEPLOY_PATH"
SERVER="$DEPLOY_USER@$DEPLOY_HOST"
FOLDER="$(printf '%q' "$DEPLOY_PATH")"
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$SERVER" "$1"; }

# Один помощник на все удалённые шаги — не копия проверки на каждый. Обрыв связи (ssh, код 255 —
# обычно выключенный на Маке Tailscale: имя сервера в deploy.local.env резолвится только через него,
# в том числе и дома) отличается от отказа самой команды/проверки, и в обоих случаях сообщение —
# по-русски, а не сырой вывод git или docker.
remote_or_fail() {
  local output status=0
  output="$(remote "$1")" || status=$?
  if [ "$status" -eq "$SSH_UNREACHABLE_EXIT_CODE" ]; then
    fail "нет связи с сервером ($DEPLOY_HOST) — включите Tailscale на Маке"
  fi
  [ "$status" -eq 0 ] || fail "$2"
  printf '%s\n' "$output"
}

TARGET_SHA="$(git rev-parse --verify "${1:-HEAD}^{commit}" 2>/dev/null)" || fail "не найден коммит «${1:-HEAD}»"

step "Проверки на Маке"
[ -z "$(git status --porcelain)" ] || fail "есть незакоммиченные изменения"
git fetch --quiet origin main
if [ $# -eq 0 ]; then
  # Без аргумента выкладывается HEAD, и он должен точно совпадать с origin/main: --is-ancestor принял
  # бы и отставший HEAD (более старый коммит — тоже предок), выкладка отставшего прошла бы молча.
  [ "$TARGET_SHA" = "$(git rev-parse origin/main)" ] || fail "HEAD не совпадает с origin/main — сначала git push"
else
  git merge-base --is-ancestor "$TARGET_SHA" origin/main || fail "коммита $TARGET_SHA нет в origin/main — сначала git push"
fi
python3 -B scripts/check_secrets.py --rev "$TARGET_SHA" || fail "в выкладываемом коммите похожее на секрет"

step "Сервер готов? Код на нём пока не трогаю"
remote_or_fail "test -f $FOLDER/.env" "нет .env на сервере — создайте вручную (docs/эксплуатация.md)" >/dev/null
remote_or_fail "test \"\$(stat -c %u $FOLDER/data)\" = $CONTAINER_UID" \
  "папка data на сервере — не у пользователя $CONTAINER_UID (docs/эксплуатация.md)" >/dev/null
remote_or_fail "cd $FOLDER && test -z \"\$(git status --porcelain)\"" "на сервере есть правки в рабочей копии" >/dev/null

# Снимок для отката: коммит, который сейчас на сервере, и был ли там вообще контейнер (docker compose
# ps -a — включая остановленный). Если нет — это первая выкладка, откатывать в случае беды некуда.
PREVIOUS_SHA_ON_SERVER="$(remote_or_fail "cd $FOLDER && git rev-parse HEAD" "не удалось прочитать текущий коммит на сервере")"
bot_container_existed="$(remote_or_fail "cd $FOLDER && docker compose ps -a -q bot" "не удалось проверить контейнер на сервере")"
[ -n "$bot_container_existed" ] || PREVIOUS_SHA_ON_SERVER=""
ROLLBACK_READY=1

step "Код на сервере: $TARGET_SHA"
remote_or_fail "cd $FOLDER && git fetch --quiet origin && git checkout --quiet --detach $TARGET_SHA" \
  "не удалось переключить сервер на нужный коммит" >/dev/null
server_sha="$(remote_or_fail "cd $FOLDER && git rev-parse HEAD" "не удалось проверить коммит на сервере")"
[ "$server_sha" = "$TARGET_SHA" ] || fail "на сервере не тот коммит"

step "Сборка и запуск"
remote_or_fail "cd $FOLDER && docker compose build --quiet && docker compose up -d" \
  "не удалось собрать и запустить контейнер на сервере" >/dev/null

step "Здоровье"
# Настоящий предел по часам, а не число проходов цикла: docker compose ps на каждой итерации сам
# отнимает время, и цикл "for … in $(seq 60)" в сумме мог заметно превышать заявленные 60 секунд.
HEALTHY_LOOP="deadline=\$((SECONDS + $HEALTH_WAIT_SECONDS))
while [ \$SECONDS -lt \$deadline ]; do
  docker compose ps bot | grep -q '(healthy)' && exit 0
  sleep 1
done
exit 1"
remote_or_fail "cd $FOLDER && $HEALTHY_LOOP" \
  "контейнер не стал здоровым за $HEALTH_WAIT_SECONDS с — смотрите docker compose logs bot" >/dev/null
remote_or_fail "cd $FOLDER && docker compose ps && docker compose logs --tail $LOG_LINES bot" \
  "не удалось получить статус и журнал с сервера"

step "Готово: $TARGET_SHA"
