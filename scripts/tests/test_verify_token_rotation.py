"""R6-6 token 轮换验收：不接触真凭证，全程用注入的假 runner。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "verify_token_rotation.py"
spec = importlib.util.spec_from_file_location("vtr", SRC)
assert spec and spec.loader
v = importlib.util.module_from_spec(spec)
sys.modules["vtr"] = v
spec.loader.exec_module(v)

OLD, NEW = "OLD_TOKEN_VALUE_x", "NEW_TOKEN_VALUE_y"   # 假值，仅测试用


@pytest.fixture
def env(tmp_path):
    e = tmp_path / "env"
    e.write_text(f"DQ_GM_TOKEN={OLD}\nDQ_GM_ACCOUNT_ID=acc\n", encoding="utf-8")
    return e, tmp_path / "old_token.sealed"


def runner_for(new_ok=True, old_ok=False):
    calls = []

    def runner(cmd, *, env=None, **_kw):
        calls.append((cmd, dict(env or {})))
        tok = (env or {}).get(v.ENV_TOKEN)
        rc = 0 if (tok == NEW and new_ok) or (tok == OLD and old_ok) else 2
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")
    return runner, calls


def test_prepare_seals_without_printing_value(env, capsys):
    e, sealed = env
    assert v.prepare(e, sealed) == v.EXIT_OK
    assert sealed.read_text(encoding="utf-8").strip() == OLD
    out = capsys.readouterr().out
    assert OLD not in out and "指纹" in out


def test_prepare_refuses_overwrite(env):
    e, sealed = env
    v.prepare(e, sealed)
    assert v.prepare(e, sealed) == v.EXIT_FAIL
    assert sealed.read_text(encoding="utf-8").strip() == OLD


def test_check_old_fails_new_succeeds_passes_and_deletes(env, capsys, monkeypatch):
    # 云端由 EnvironmentFile 注入账户 ID；本地测试显式模拟，验证 probe 不把它丢掉
    monkeypatch.setenv("DQ_GM_ACCOUNT_ID", "acc_from_env")
    e, sealed = env
    v.prepare(e, sealed)
    e.write_text(f"DQ_GM_TOKEN={NEW}\nDQ_GM_ACCOUNT_ID=acc\n", encoding="utf-8")
    runner, calls = runner_for()
    assert v.check(e, sealed, Path("/fake/python"), runner=runner, today="20261109") == v.EXIT_OK
    assert not sealed.exists()
    assert len(calls) == 2
    # 两次探针唯一差异必须是 token；账户 ID 不许被 pop 掉
    assert calls[0][1]["DQ_GM_ACCOUNT_ID"] == calls[1][1]["DQ_GM_ACCOUNT_ID"]
    assert calls[0][1][v.ENV_TOKEN] == OLD and calls[1][1][v.ENV_TOKEN] == NEW
    out = capsys.readouterr().out
    assert OLD not in out and NEW not in out


@pytest.mark.parametrize("new_ok,old_ok,expect", [
    (True, True, False),     # 旧 token 仍有效 ⇒ 轮换没生效
    (False, False, False),   # 新 token 也失败
    (False, True, False),
])
def test_check_failure_modes(env, new_ok, old_ok, expect):
    e, sealed = env
    v.prepare(e, sealed)
    e.write_text(f"DQ_GM_TOKEN={NEW}\n", encoding="utf-8")
    runner, _ = runner_for(new_ok=new_ok, old_ok=old_ok)
    assert (v.check(e, sealed, Path("/fake/python"), runner=runner,
                    today="20261109") == v.EXIT_OK) is expect
    assert sealed.exists()          # 未通过必须保留封存文件


def test_check_not_rotated_is_fail(env, capsys):
    e, sealed = env
    v.prepare(e, sealed)
    runner, _ = runner_for()
    assert v.check(e, sealed, Path("/fake/python"), runner=runner,
                   today="20261109") == v.EXIT_FAIL
    assert "未轮换" in capsys.readouterr().out


def test_check_missing_seal_is_fail(env):
    e, sealed = env
    runner, _ = runner_for()
    assert v.check(e, sealed, Path("/fake/python"), runner=runner,
                   today="20261109") == v.EXIT_FAIL


def test_probe_day_is_trading_day():
    assert v.probe_day("20261109") == "20261109"
    assert v.probe_day("20261108") == "20261106"    # 周日回退到最近交易日


def test_audit_logs_no_hits(env, capsys):
    e, sealed = env
    v.prepare(e, sealed)
    e.write_text(f"DQ_GM_TOKEN={NEW}\n", encoding="utf-8")
    runner = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="clean log", stderr="")
    assert v.audit_logs(e, sealed, runner=runner) == v.EXIT_OK
    assert OLD not in capsys.readouterr().out and NEW not in capsys.readouterr().out


def test_audit_logs_detects_leak_without_printing_value(env, capsys):
    e, sealed = env
    runner = lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=f"something {OLD} leaked", stderr="")
    assert v.audit_logs(e, sealed, runner=runner) == v.EXIT_FAIL
    out = capsys.readouterr().out
    assert OLD not in out and "命中" in out


def test_read_env_token_only_reports_var_name(env, tmp_path):
    p = tmp_path / "empty"
    p.write_text("DQ_GM_ACCOUNT_ID=acc\n", encoding="utf-8")
    with pytest.raises(v.RotationError, match="只报变量名"):
        v.read_env_token(p)
