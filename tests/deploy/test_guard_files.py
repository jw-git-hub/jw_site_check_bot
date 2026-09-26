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


def test_check_isolation_checks_a_neighbour_container():
    script = read("check_isolation.sh")
    assert "find_neighbour_container_ip" in script
    assert "соседний контейнер" in script
    assert "нет запущенных контейнеров" in script  # честный отказ, если проверить нечем


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
