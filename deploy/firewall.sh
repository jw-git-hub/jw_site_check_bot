#!/usr/bin/env bash
# Ставит изоляцию сети контейнера сайт-чекера (ТЗ, С10). На сервере, из папки бота: sudo deploy/firewall.sh
set -euo pipefail
cd "$(dirname "$0")"

RULES_TARGET="/etc/nftables.d/jw-site-check-guard.nft"
UNIT_TARGET="/etc/systemd/system/jw-site-check-guard.service"
UNIT_NAME="jw-site-check-guard.service"

fail() { printf 'Изоляция не поставлена: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "нужен sudo"
command -v nft >/dev/null || fail "нет nft: sudo apt install nftables"
nft -c -f jw-site-check-guard.nft || fail "ошибка в правилах"
install -D -m 0644 jw-site-check-guard.nft "$RULES_TARGET"
install -m 0644 jw-site-check-guard.service "$UNIT_TARGET"
systemctl daemon-reload
systemctl enable "$UNIT_NAME"
# Новые правила — сразу из файла: он повторяемый. Службу не перезапускаем: по RequiredBy с ней перезапустился бы Docker.
nft -f "$RULES_TARGET"
systemctl start "$UNIT_NAME"
nft list table inet jw_site_check_guard
echo "Изоляция на месте. Когда контейнер запущен — проверка: deploy/check_isolation.sh"
