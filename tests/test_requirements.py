"""Guards the Step 0 requirements so later steps can rely on them."""

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"


def load(name):
    return yaml.safe_load((CONFIG / name).read_text())


def test_network_share_never_exceeds_end_to_end():
    for name, c in load("latency_budget.yaml")["classes"].items():
        if c["network_rtt_ms"] is not None:
            assert c["network_rtt_ms"] < c["e2e_ms"], name


def test_actuation_is_the_tightest_class():
    classes = load("latency_budget.yaml")["classes"]
    assert classes["L1_actuation"]["e2e_ms"] == min(c["e2e_ms"] for c in classes.values())


def test_headline_kpis_are_defined():
    cfg = load("kpis.yaml")
    defined = {k for group in cfg["kpis"].values() for k in group}
    for objective, kpis in cfg["headline"].items():
        assert set(kpis) <= defined, objective


def test_every_kpi_has_unit_and_direction():
    for group in load("kpis.yaml")["kpis"].values():
        for name, spec in group.items():
            assert spec["direction"] in {"min", "max"}, name
            assert spec["unit"], name
