"""R6-1 通道隔离检查器的行为测试（含「空输入不得判 PASS」）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "check_channel_isolation.py"
spec = importlib.util.spec_from_file_location("cci", SRC)
assert spec and spec.loader
cci = importlib.util.module_from_spec(spec)
sys.modules["cci"] = cci
spec.loader.exec_module(cci)


def clean_meta(**kw):
    m = {
        "present": True,
        "files": [],
        "env_names": ["DQ_GM_TOKEN", "DQ_GM_ACCOUNT_ID"],
        "env_present": True,
        "live_enabled": None,
        "broker_modules": sorted(cci.ALLOWED_BROKER_MODULES),
        "adapter_has_order": False,
        "paper_auth_channel": None,
    }
    m.update(kw)
    return m


def fails(rows, name):
    return any(n == name and s == cci.BAD for n, s, _ in rows)


def test_clean_environment_passes():
    rows = cci.evaluate(clean_meta())
    assert not [r for r in rows if r[1] == cci.BAD]


def test_main_exit_codes(monkeypatch):
    monkeypatch.setattr(cci, "collect_local", lambda root=None: clean_meta())
    assert cci.main([]) == 0
    monkeypatch.setattr(cci, "collect_local",
                        lambda root=None: clean_meta(files=["live_authorization.json"]))
    assert cci.main([]) == 1


@pytest.mark.parametrize("fname", [
    "forward/live_authorization.json",
    "backend/qmt_adapter.py",
    "deploy/bin/mini_qmt_bridge.py",
    "etc/live.env",
])
def test_forbidden_file_is_fail(fname):
    assert fails(cci.evaluate(clean_meta(files=[fname])), "真实通道授权/适配器文件")


@pytest.mark.parametrize("var", [
    "DQ_LIVE_TOKEN", "QMT_ACCOUNT", "BROKER_PASSWORD", "GM_LIVE_KEY",
])
def test_forbidden_env_var_is_fail(var):
    assert fails(cci.evaluate(clean_meta(env_names=[var])), "真实通道 env 变量")


def test_dq_gm_vars_are_allowed():
    assert not fails(cci.evaluate(clean_meta()), "真实通道 env 变量")


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_live_enabled_true_is_fail(val):
    assert fails(cci.evaluate(clean_meta(live_enabled=val)), "LIVE_ENABLED")


def test_live_enabled_false_is_ok():
    assert not fails(cci.evaluate(clean_meta(live_enabled="false")), "LIVE_ENABLED")


def test_unknown_broker_module_is_fail():
    assert fails(cci.evaluate(clean_meta(broker_modules=["base.py", "qmt_live.py"])),
                 "broker 模块白名单")


def test_missing_broker_dir_is_fail():
    assert fails(cci.evaluate(clean_meta(broker_modules=None)), "broker 模块白名单")


def test_order_call_in_adapter_is_fail():
    assert fails(cci.evaluate(clean_meta(adapter_src="def f():\n    return order_volume(1)\n")),
                 "只读适配器无 order_* 调用")


def test_adapter_source_missing_is_fail():
    assert fails(cci.evaluate(clean_meta(adapter_src=None)),
                 "只读适配器无 order_* 调用")


def test_remote_order_count_flag_is_fail():
    assert fails(cci.evaluate(clean_meta(adapter_has_order=True)),
                 "只读适配器无 order_* 调用")


def test_collection_failure_is_fail_closed():
    rows = cci.evaluate({"present": False, "error": "ssh_rc=255"})
    assert len(rows) == 1 and rows[0][1] == cci.BAD
    assert "失败关闭" in rows[0][2]


def test_local_missing_env_is_info_not_pass():
    """本地没有 env 文件 ⇒ 必须标 INFO「未检」，不能写成 PASS。"""
    rows = cci.evaluate(clean_meta(env_present=False, env_names=[], live_enabled=None))
    for name in ("真实通道 env 变量", "LIVE_ENABLED"):
        assert any(n == name and s == cci.INFO for n, s, _ in rows)


def test_paper_auth_with_live_channel_is_fail():
    assert fails(cci.evaluate(clean_meta(paper_auth_channel="live")),
                 "paper_authorization.json")


def test_paper_auth_gm_paper_is_info():
    rows = cci.evaluate(clean_meta(paper_auth_channel="gm-paper"))
    assert any(n == "paper_authorization.json" and s == cci.INFO for n, s, _ in rows)


def test_remote_empty_env_is_fail_closed(monkeypatch):
    """远端 env 变量名为空 ⇒ 采集判失败，不能当成「没有真实通道变量」。"""
    class R:
        returncode = 0
        stdout = ("---FILES---\n---ENV---\n---LIVE---\n---BROKER---\nbase.py\n"
                  "---ORDER---\n0\n---AUTH---\nNONE\n---END---\n")

    monkeypatch.setattr(cci.subprocess, "run", lambda *a, **k: R())
    meta = cci.collect_remote()
    assert meta["present"] is False
    assert fails(cci.evaluate(meta), "远端/本地采集")
