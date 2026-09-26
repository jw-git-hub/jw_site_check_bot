#!/usr/bin/env bash
# Проверка изоляции контейнера (ТЗ, С11). На сервере, из папки бота, когда контейнер запущен:
#   deploy/check_isolation.sh
# Запускать после установки, после перезагрузки сервера и после перезапуска tailscaled или docker.
set -euo pipefail
cd "$(dirname "$0")/.."

NETWORK="jw_site_check_bot_site_check"
TIMEOUT_SECONDS=3
PROBE='import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])).close()
except OSError:
    sys.exit(1)'
failures=0

reachable() { docker compose exec -T bot python -c "$PROBE" "$1" "$2" "$TIMEOUT_SECONDS"; }
# С самого сервера: если цель закрыта и отсюда, «закрыто» из контейнера ничего не доказывает
reachable_from_host() { timeout "$TIMEOUT_SECONDS" bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

expect_closed() {
  if ! reachable_from_host "$1" "$2"; then
    echo "пропуск: $3 ($1:$2) недоступен и с самого сервера — проверка ничего не покажет"
  elif reachable "$1" "$2"; then
    echo "ОШИБКА: из контейнера доступен $3 ($1:$2)"
    failures=$((failures + 1))
  else
    echo "закрыто: $3"
  fi
}

expect_open() {
  if reachable "$1" "$2"; then
    echo "открыто: $3"
  else
    echo "ОШИБКА: из контейнера недоступен $3 ($1:$2)"
    failures=$((failures + 1))
  fi
}

router_ip="$(ip -4 route show default | awk '{print $3; exit}')"
server_ip="$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
gateway_ip="$(docker network inspect "$NETWORK" --format '{{(index .IPAM.Config 0).Gateway}}')"
tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"

expect_closed "$router_ip" 80 "роутер"
expect_closed "$server_ip" 22 "сервер по адресу в домашней сети"
expect_closed "$gateway_ip" 22 "сервер через шлюз Docker"
if [ -n "$tailscale_ip" ]; then expect_closed "$tailscale_ip" 22 "сервер в Tailscale"; fi
expect_closed 100.100.100.100 53 "DNS Tailscale"
expect_closed 2001:4860:4860::8888 443 "интернет по IPv6"
expect_open www.google.com 443 "интернет по IPv4"

[ "$failures" -eq 0 ] || { echo "Изоляция НЕ в порядке: $failures"; exit 1; }
echo "Изоляция в порядке"
