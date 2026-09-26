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
