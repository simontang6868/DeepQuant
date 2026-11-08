"""R6-5：11-30 时间闸门、navs 失败关闭、常数 waste 敏感性口径。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "lotapp_decision_brief.py"
spec = importlib.util.spec_from_file_location("brief", SRC)
assert spec and spec.loader
b = importlib.util.module_from_spec(spec)
sys.modules["brief"] = b
spec.loader.exec_module(b)


def synth(n=8, length=90):
    rng = np.random.default_rng(11)
    return [np.concatenate([[1.0], np.cumprod(1 + rng.normal(0.003, 0.05, length))])
            for _ in range(n)]


def test_prereg_sha_is_intact():
    assert len(b.check_prereg()) == 12


def test_publish_before_1130_is_refused(tmp_path):
    assert b.publish("20261129", tmp_path / "navs.json", tmp_path / "out.md") == b.EXIT_GATE
    assert not (tmp_path / "out.md").exists()


def test_missing_navs_fails_closed(tmp_path):
    assert b.publish("20261130", tmp_path / "no.json", tmp_path / "out.md") == b.EXIT_FAIL
    assert not (tmp_path / "out.md").exists()


def test_bad_navs_structure_fails_closed(tmp_path):
    for payload in ({}, {"navs": []}, {"navs": [[1.0, 1.1, 1.2]]},
                     {"navs": [[1.0] * 100, [1.0, float("nan")] * 50]}):
        p = tmp_path / "navs.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(b.BriefError):
            b.load_navs(p)


def test_paired_bootstrap_weight_monotonicity():
    navs = synth()
    low = b.paired_bootstrap(navs, b.WASTE["current"]["expected"])
    high = b.paired_bootstrap(navs, b.WASTE["append"]["expected"])
    assert high["w_eff"] > low["w_eff"]
    assert high["q50"] <= low["q50"] + 1e-9
    assert high["n_rep"] == len(navs) * b.REPS
    assert high["assumption"] == "EXPLORATORY_CONSTANT_WASTE"
    assert 0.0 <= low["p_mdd_gt_20pct"] <= 1.0


def test_render_contains_mandatory_disclaimers(tmp_path):
    navs = synth()
    cur = b.paired_bootstrap(navs, b.WASTE["current"]["expected"])
    app = b.paired_bootstrap(navs, b.WASTE["append"]["expected"])
    rec = {"prereg_sha": "abc", "baseline_p": 0.047,
           "rows": {"waste": (0.12, 0.017), "ann": (0.075, 0.0802),
                    "max_weight": (1.0, 1.176),
                    "p": (cur["p_mdd_gt_20pct"], app["p_mdd_gt_20pct"])},
           "q5": (cur["q5"], app["q5"]),
           "w_eff": (cur["w_eff"], app["w_eff"]), "n_rep": cur["n_rep"]}
    out = tmp_path / "brief.md"
    assert b.render(rec, "deadbeef", out) == b.EXIT_OK
    t = out.read_text(encoding="utf-8")
    for key in ("8.02% 仅超 8% 门槛 0.02pp", "样本内估算", "S4=0",
                "EXPLORATORY_CONSTANT_WASTE", "未上线", "不含上线建议",
                "17.6%"):
        assert key in t, key


def test_selftest_runs():
    assert b.selftest() == b.EXIT_OK
