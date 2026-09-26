import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
NETWORK = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["networks"]["site_check"]
PRIVATE_RANGES = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
                  "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4")


def read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("script", ["firewall.sh", "check_isolation.sh"])
def test_scripts_are_valid_bash(script):
    assert subprocess.run(["bash", "-n", str(DEPLOY / script)]).returncode == 0


def test_rules_guard_exactly_the_container_subnet_and_bridge():
    rules = read("jw-site-check-guard.nft")
    assert f"ip saddr {NETWORK['ipam']['config'][0]['subnet']} ip daddr @not_internet" in rules
    assert f'iifname "{NETWORK["driver_opts"]["com.docker.network.bridge.name"]}"' in rules


def test_rules_close_every_private_range_and_never_flush_other_tables():
    rules = read("jw-site-check-guard.nft")
    assert all(network in rules for network in PRIVATE_RANGES)
    assert "flush ruleset" not in rules
    assert "hook prerouting priority raw" in rules


def test_service_runs_after_global_rules_and_before_docker():
    unit = read("jw-site-check-guard.service")
    assert re.search(r"^After=.*nftables\.service", unit, re.MULTILINE)  # чужой flush ruleset не сотрёт таблицу
    assert re.search(r"^Before=.*docker\.service", unit, re.MULTILINE)
    assert "RequiredBy=docker.service" in unit


def test_install_applies_rules_without_restarting_the_service():
    script = read("firewall.sh")
    assert 'nft -f "$RULES_TARGET"' in script
    assert "restart" not in script  # перезапуск службы по RequiredBy перезапустил бы и Docker


def test_service_stops_with_nftables_to_stay_fail_closed():
    unit = read("jw-site-check-guard.service")
    # Debian: ExecStop общей nftables.service — flush ruleset. Без PartOf наша таблица пропадёт,
    # а служба останется «active (exited)» — контейнер продолжит работать без изоляции.
    assert re.search(r"^PartOf=.*nftables\.service", unit, re.MULTILINE)
    assert re.search(r"^After=.*nftables\.service", unit, re.MULTILINE)


def test_check_isolation_verifies_the_container_is_running_before_any_probe():
    script = read("check_isolation.sh")
    running_check = script.index("container_running || fail")
    first_call = script.index('expect_closed "$router_ip"')  # первый настоящий вызов, не объявление функции
    assert running_check < first_call


def test_check_isolation_probe_treats_only_os_error_as_closed():
    script = read("check_isolation.sh")
    # Голый except (или любой другой) проглотил бы сбой docker exec / трассу Python как «закрыто».
    assert "except OSError:" in script
    assert re.search(r"^\s*except:\s*$", script, re.MULTILINE) is None
    assert "PROBE_CLOSED_EXIT_CODE" in script


def test_check_isolation_distinguishes_closed_from_probe_errors():
    script = read("check_isolation.sh")
    assert re.search(r"\bclosed\)", script)
    assert re.search(r"\berror\)", script)


def test_check_isolation_reads_docker_gateway_from_the_bridge_not_ipam():
    script = read("check_isolation.sh")
    # IPAM.Config[0].Gateway пуст, когда compose задаёт только subnet — проверка молча пропускалась.
    assert "docker network inspect" not in script
    assert 'ip -4 -o addr show dev "$BRIDGE_INTERFACE"' in script


def test_check_isolation_treats_an_undetermined_address_as_a_failure_not_a_skip():
    script = read("check_isolation.sh")
    assert "require_address" in script
    assert 'require_address "$tailscale_ip"' in script
    assert 'require_address "$gateway_ip"' in script


def test_check_isolation_always_uses_a_controlled_temporary_neighbour():
    """Настоящий сосед из `docker ps` мог ничего не слушать на :80 — тогда «закрыто» получалось что при
    рабочей изоляции (timeout), что без неё (refused), и группа подтверждалась независимо от сети. Скрипт
    больше не ищет случайный контейнер — всегда поднимает свой, заведомо слушающий порт, и проверяет его
    обычным expect_closed (не отдельным дублем case)."""
    script = read("check_isolation.sh")
    assert "find_neighbour_container_ip" not in script
    assert "docker ps -q" not in script
    assert re.search(r"^NEIGHBOUR_CONTAINER_NAME=\S+", script, re.MULTILINE)
    assert re.search(r'docker run .*--name "\$NEIGHBOUR_CONTAINER_NAME"', script)
    assert "--network bridge" in script  # сеть Docker по умолчанию, не наша
    assert "--pull never" in script  # уже собранный локальный образ, без скачивания
    assert re.search(r'expect_closed "\$temporary_neighbour_ip" "\$NEIGHBOUR_PORT" "соседний контейнер" neighbour',
                      script)
    # Раньше был отдельный case-блок для временного соседа — дубль expect_closed. Его быть не должно.
    assert "expect_temporary_neighbour_closed" not in script


def test_check_isolation_temporary_neighbour_actually_listens():
    """С одной sleep-заглушкой «открыто» никогда не наступает: и без изоляции (refused), и с ней
    (timeout) зонд видит OSError → «закрыто». Нужен настоящий слушатель, чтобы «открыто» было
    достижимо, если изоляция вдруг не работает."""
    script = read("check_isolation.sh")
    assert "sleep infinity" not in script
    assert re.search(r"http\.server|socketserver|create_server", script)
    assert not re.search(r"^NEIGHBOUR_PORT=80\b", script, re.MULTILINE)  # не root — 80 не поднять


def test_check_isolation_waits_for_the_temporary_neighbour_to_listen():
    """Слушателю нужно мгновение подняться после docker run -d — короткое ограниченное ожидание,
    тот же приём положительного контроля (reachable_from_host), что и у остальных групп."""
    script = read("check_isolation.sh")
    assert "wait_for_neighbour_ready() {" in script
    body_start = script.index("wait_for_neighbour_ready() {")
    body_end = script.index("\n}", body_start)
    assert "reachable_from_host" in script[body_start:body_end]


def test_check_isolation_neighbour_cleans_up_stale_and_traps_before_starting():
    """Устойчивость: обрывок от прошлого убитого запуска не должен мешать следующему, а trap должен
    стоять ДО docker run — тогда контейнер уберётся, даже если сам run не завершится штатно."""
    script = read("check_isolation.sh")
    stale_cleanup_index = script.index('docker rm -f "$NEIGHBOUR_CONTAINER_NAME" >/dev/null 2>&1 || true')
    trap_index = script.index('trap \'docker rm -f "$NEIGHBOUR_CONTAINER_NAME"')
    run_index = script.index('docker run -d --pull never --network bridge')
    assert stale_cleanup_index < trap_index < run_index


def test_check_isolation_no_stale_wording_about_network_instead_of_download():
    """«без сети» рядом с --pull never сбивает с толку: сеть (bridge) как раз есть, скачивания — нет."""
    script = read("check_isolation.sh")
    assert "без сети" not in script


def test_check_isolation_verdict_requires_every_group_confirmed_and_no_failures():
    script = read("check_isolation.sh")
    assert "group_confirmed" in script
    for group in ("router", "server", "tailscale", "neighbour"):
        assert group in script
    assert "Изоляция в порядке" in script
    assert "не проверено групп" in script


def test_check_isolation_header_mentions_nftables_restart():
    header = "\n".join(read("check_isolation.sh").splitlines()[:6])
    assert "nftables" in header


def test_check_isolation_tries_several_router_ports_not_just_80():
    """Веб-интерфейс роутера бывает не только на 80 — группа «роутер» должна подтверждаться и тогда."""
    script = read("check_isolation.sh")
    assert re.search(r"^ROUTER_PORTS=\(80 443 8080 53\)", script, re.MULTILINE)
    assert 'for router_port in "${ROUTER_PORTS[@]}"' in script
    assert 'expect_closed "$router_ip" "$router_port" "роутер, порт $router_port" router' in script
