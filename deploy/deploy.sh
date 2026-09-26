#!/usr/bin/env bash
# Выкладка сайт-чекера на сервер с Мака (ТЗ, 13.2):
#   deploy/deploy.sh          выложить HEAD — он уже должен быть в origin/main
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

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'Выкладка остановлена: %s\n' "$*" >&2; exit 1; }

[ -f "$LOCAL_CONFIG" ] || fail "нет $LOCAL_CONFIG (образец — deploy/deploy.local.env.example)"
# shellcheck source=/dev/null
source "$LOCAL_CONFIG"
[ -n "${DEPLOY_HOST:-}" ] && [ -n "${DEPLOY_USER:-}" ] && [ -n "${DEPLOY_PATH:-}" ] \
  || fail "в $LOCAL_CONFIG не заполнены DEPLOY_HOST, DEPLOY_USER, DEPLOY_PATH"
SERVER="$DEPLOY_USER@$DEPLOY_HOST"
FOLDER="$(printf '%q' "$DEPLOY_PATH")"
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$SERVER" "$1"; }

# Имя сервера в deploy.local.env — обычно имя в Tailscale (13.2): если он выключен на Маке, имя не находится,
# и ssh обрывается с кодом 255 (поправка 2, 26.09.2026) — это стоит отличать от отказа самой удалённой проверки.
check_remote() {
  local status=0
  remote "$1" || status=$?
  [ "$status" -eq 0 ] && return 0
  if [ "$status" -eq "$SSH_UNREACHABLE_EXIT_CODE" ]; then
    fail "нет связи с сервером ($DEPLOY_HOST) — если вы не дома, включите Tailscale на Маке"
  fi
  fail "$2"
}

TARGET_SHA="$(git rev-parse --verify "${1:-HEAD}^{commit}")"

step "Проверки на Маке"
[ -z "$(git status --porcelain)" ] || fail "есть незакоммиченные изменения"
git fetch --quiet origin main
git merge-base --is-ancestor "$TARGET_SHA" origin/main || fail "коммита $TARGET_SHA нет в origin/main — сначала git push"
python3 -B scripts/check_secrets.py --rev "$TARGET_SHA" || fail "в выкладываемом коммите похожее на секрет"

step "Сервер готов? Код на нём пока не трогаю"
check_remote "test -f $FOLDER/.env" "нет .env на сервере — создайте вручную (docs/эксплуатация.md)"
check_remote "test \"\$(stat -c %u $FOLDER/data)\" = $CONTAINER_UID" \
  "папка data на сервере — не у пользователя $CONTAINER_UID (docs/эксплуатация.md)"
check_remote "cd $FOLDER && test -z \"\$(git status --porcelain)\"" "на сервере есть правки в рабочей копии"

step "Код на сервере: $TARGET_SHA"
remote "cd $FOLDER && git fetch --quiet origin && git checkout --quiet --detach $TARGET_SHA"
[ "$(remote "cd $FOLDER && git rev-parse HEAD")" = "$TARGET_SHA" ] || fail "на сервере не тот коммит"

step "Сборка и запуск"
remote "cd $FOLDER && docker compose build --quiet && docker compose up -d"

step "Здоровье"
HEALTHY_LOOP="for second in \$(seq $HEALTH_WAIT_SECONDS); do
  docker compose ps bot | grep -q '(healthy)' && exit 0
  sleep 1
done
exit 1"
remote "cd $FOLDER && $HEALTHY_LOOP" \
  || fail "контейнер не стал здоровым за $HEALTH_WAIT_SECONDS с — смотрите docker compose logs bot"
remote "cd $FOLDER && docker compose ps && docker compose logs --tail $LOG_LINES bot"

step "Готово: $TARGET_SHA"
