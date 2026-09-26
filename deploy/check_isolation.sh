#!/usr/bin/env bash
# Проверка изоляции контейнера (ТЗ, С11). На сервере, из папки бота, когда контейнер запущен:
#   deploy/check_isolation.sh
# Запускать после установки, после перезагрузки сервера и после перезапуска tailscaled, docker или
# nftables — наша таблица привязана к nftables.service (PartOf) и уходит вместе с ним.
set -euo pipefail
cd "$(dirname "$0")/.."

BRIDGE_INTERFACE="br-sitecheck"
TIMEOUT_SECONDS=3
PROBE_CLOSED_EXIT_CODE=42
NEIGHBOUR_CONTAINER_NAME="jw-site-check-neighbour-probe"
NEIGHBOUR_IMAGE="jw_site_check_bot:latest"  # уже собран локально (docker-compose.yml): скрипт его не собирает и не тянет
NEIGHBOUR_PORT=8080  # высокий порт: контейнер не root (Dockerfile — USER 10001), 80 ему не поднять
NEIGHBOUR_READY_ATTEMPTS=10  # короткое ограниченное ожидание, пока слушатель поднимется после run -d
NEIGHBOUR_READY_INTERVAL_SECONDS=0.3
ROUTER_PORTS=(80 443 8080 53)  # веб-интерфейс роутера бывает не только на 80 — пробуем весь список
# Закрыто — только явный отказ соединения. Любая другая ошибка (сбой docker exec, трасса Python)
# не должна выдаваться за «закрыто»: голый except это делал бы.
PROBE='import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])).close()
except OSError:
    sys.exit(int(sys.argv[4]))'

declare -A group_confirmed=([router]=0 [server]=0 [tailscale]=0 [neighbour]=0)
declare -A group_label=([router]="роутер" [server]="сервер" [tailscale]="Tailscale" [neighbour]="соседний контейнер")
failures=0

fail() { printf 'Изоляция не проверена: %s\n' "$*" >&2; exit 1; }

container_running() { [ -n "$(docker compose ps --status running -q bot)" ]; }

# open — соединение установилось; closed — зонд поймал явный отказ (сравнение с PROBE_CLOSED_EXIT_CODE);
# error — что-то ещё (сбой docker exec, необработанное исключение) — это не «закрыто».
probe() {
  if docker compose exec -T bot python -c "$PROBE" "$1" "$2" "$TIMEOUT_SECONDS" "$PROBE_CLOSED_EXIT_CODE"; then
    echo open
  elif [ "$?" -eq "$PROBE_CLOSED_EXIT_CODE" ]; then
    echo closed
  else
    echo error
  fi
}

# С самого сервера: если цель закрыта и отсюда, «закрыто» из контейнера ничего не доказывает
reachable_from_host() { timeout "$TIMEOUT_SECONDS" bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

# Адрес, который скрипту не удалось определить, — это отказ проверки, а не тихий пропуск.
require_address() {
  if [ -n "$1" ]; then return 0; fi
  echo "ОШИБКА: не удалось определить адрес: $2"
  failures=$((failures + 1))
  return 1
}

expect_closed() {
  local target="$1" port="$2" label="$3" group="${4:-}"
  if ! reachable_from_host "$target" "$port"; then
    echo "пропуск: $label ($target:$port) недоступен и с самого сервера — проверка ничего не покажет"
    return
  fi
  case "$(probe "$target" "$port")" in
    closed) [ -z "$group" ] || group_confirmed[$group]=$((group_confirmed[$group] + 1)); echo "закрыто: $label" ;;
    open) failures=$((failures + 1)); echo "ОШИБКА: из контейнера доступен $label ($target:$port)" ;;
    error) failures=$((failures + 1)); echo "ОШИБКА: проверка «$label» не выполнена (сбой зонда)" ;;
  esac
}

expect_open() {
  local target="$1" port="$2" label="$3"
  if [ "$(probe "$target" "$port")" = open ]; then
    echo "открыто: $label"
  else
    failures=$((failures + 1))
    echo "ОШИБКА: из контейнера недоступен $label ($target:$port)"
  fi
}

# Сосед — свой временный контейнер: настоящего стороннего контейнера на сервере обычно нет, а
# случайно найденный через docker ps мог ничего не слушать на порту проверки — тогда «закрыто»
# получалось бы что при рабочей изоляции (timeout), что без неё (refused), и группа подтверждалась
# бы вне зависимости от сети. Поэтому проверка всегда поднимает контролируемого соседа сама: из уже
# собранного образа бота, без скачивания (--pull never), в сети Docker по умолчанию (не в нашей
# site_check), со своим слушателем на NEIGHBOUR_PORT вместо самого бота — с ним «открыто» станет
# достижимым результатом, если изоляция вдруг не работает.

# Слушателю нужно мгновение подняться после docker run -d — короткое ограниченное ожидание с тем же
# приёмом положительного контроля, что и у остальных групп: порт должен ответить уже с хоста
# (reachable_from_host), иначе «закрыто» из контейнера ничего не значит.
wait_for_neighbour_ready() {
  local ip="$1" attempt
  for attempt in $(seq "$NEIGHBOUR_READY_ATTEMPTS"); do
    reachable_from_host "$ip" "$NEIGHBOUR_PORT" && return 0
    sleep "$NEIGHBOUR_READY_INTERVAL_SECONDS"
  done
  return 1
}

# Обрывок от прошлого прерванного запуска убирается заранее — тем же именем, чтобы не мешал
# следующему. Trap ставится ДО docker run: контейнер уберётся, даже если сам run не завершится
# (например, скрипт прервали прямо во время него), а не через --rm — тот сработал бы только после
# docker stop. Функция — не через $(...): в подстановке команд trap достался бы только её собственной
# подоболочке и снял бы контейнер сразу же, ещё до проверки. Поэтому результат — в глобальной переменной.
temporary_neighbour_ip=""
start_temporary_neighbour() {
  docker rm -f "$NEIGHBOUR_CONTAINER_NAME" >/dev/null 2>&1 || true
  trap 'docker rm -f "$NEIGHBOUR_CONTAINER_NAME" >/dev/null 2>&1 || true' EXIT
  docker run -d --pull never --network bridge --name "$NEIGHBOUR_CONTAINER_NAME" \
    "$NEIGHBOUR_IMAGE" python -m http.server "$NEIGHBOUR_PORT" >/dev/null || return 1
  temporary_neighbour_ip="$(docker inspect "$NEIGHBOUR_CONTAINER_NAME" \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' | grep -v '^$' | head -n1)"
  wait_for_neighbour_ready "$temporary_neighbour_ip" || temporary_neighbour_ip=""
}

report_verdict() {
  local group unmet=()
  for group in "${!group_label[@]}"; do
    [ "${group_confirmed[$group]}" -gt 0 ] || unmet+=("${group_label[$group]}")
  done
  if [ "$failures" -eq 0 ] && [ "${#unmet[@]}" -eq 0 ]; then
    echo "Изоляция в порядке"
    return 0
  fi
  echo "Изоляция НЕ подтверждена: сбоев $failures, не проверено групп: ${unmet[*]:-нет}"
  return 1
}

container_running || fail "контейнер bot не запущен (docker compose up -d)"

router_ip="$(ip -4 route show default | awk '{print $3; exit}')" || true
server_ip="$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')" || true
gateway_ip="$(ip -4 -o addr show dev "$BRIDGE_INTERFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)" || true
tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"

if require_address "$router_ip" "адрес роутера"; then
  for router_port in "${ROUTER_PORTS[@]}"; do
    expect_closed "$router_ip" "$router_port" "роутер, порт $router_port" router
  done
fi
require_address "$server_ip" "адрес сервера в домашней сети" && expect_closed "$server_ip" 22 "сервер по адресу в домашней сети" server
require_address "$gateway_ip" "шлюз Docker для сервера" && expect_closed "$gateway_ip" 22 "сервер через шлюз Docker" server
require_address "$tailscale_ip" "адрес сервера в Tailscale" && expect_closed "$tailscale_ip" 22 "сервер в Tailscale" tailscale

start_temporary_neighbour || true
if [ -n "$temporary_neighbour_ip" ]; then
  expect_closed "$temporary_neighbour_ip" "$NEIGHBOUR_PORT" "соседний контейнер" neighbour
else
  failures=$((failures + 1))
  echo "ОШИБКА: не удалось поднять временный контейнер для проверки соседа"
fi
expect_closed 100.100.100.100 53 "DNS Tailscale" tailscale
expect_closed 2001:4860:4860::8888 443 "интернет по IPv6"
expect_open www.google.com 443 "интернет по IPv4"

report_verdict || exit 1
