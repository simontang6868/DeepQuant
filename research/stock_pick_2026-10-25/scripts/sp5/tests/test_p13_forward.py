# -*- coding: utf-8 -*-
"""P1-3 前瞻系统的行为测试。

每条都对应一个"如果不测就会静默出错"的点：
闸门误放行会让回溯冒充前瞻；pick 三套目标错位会让台账指向不该买的股票。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import p13_forward as fwd  # noqa: E402


# ------------------------------------------------------------------ 闸门
def test_guard_downgrades_stale_decision_date(monkeypatch, tmp_path):
    """决策日早于今天 >3 天 → 必须强制 SELFTEST。

    真实风险：拿一个月前的月末当"前瞻记录"，当时已能看见结果，
    这会把回溯伪装成样本外证据。闸门必须挡住，且不能靠操作者自觉。
    """
    monkeypatch.setattr(fwd, "LEDGER", tmp_path)
    monkeypatch.setattr(fwd, "FWD", tmp_path)
    monkeypatch.setattr(fwd, "build_snapshot", _fake_snapshot)
    monkeypatch.setattr(fwd, "judge_fills", lambda t, d: _fake_fills(t))
    monkeypatch.setattr(fwd, "sha256_file", lambda p: "deadbeef")
    # 决策日 2019-01-31（远早于今天），且不传 --selftest
    monkeypatch.setattr(sys, "argv", ["p13", "--asof", "20190131"])
    fwd.main()
    rec = _load_one(tmp_path)
    assert rec["mode"] == "SELFTEST"
    assert rec["counts_toward_observation"] is False


def test_guard_keeps_future_decision_as_forward(monkeypatch, tmp_path):
    """决策日晚于今天 → 应保留 FORWARD（真实前瞻）。"""
    monkeypatch.setattr(fwd, "LEDGER", tmp_path)
    monkeypatch.setattr(fwd, "FWD", tmp_path)
    monkeypatch.setattr(fwd, "build_snapshot", _fake_snapshot)
    monkeypatch.setattr(fwd, "judge_fills", lambda t, d: _fake_fills(t))
    monkeypatch.setattr(fwd, "sha256_file", lambda p: "deadbeef")
    monkeypatch.setattr(sys, "argv", ["p13", "--asof", "29991231"])
    fwd.main()
    rec = _load_one(tmp_path)
    assert rec["mode"] == "FORWARD"
    assert rec["counts_toward_observation"] is True


# ------------------------------------------------------------------ 选股
def test_pick_returns_three_sets_and_top5_is_amt_desc():
    s = _fake_snapshot("20260131")
    pool, top, rand = fwd.pick(s)
    assert len(top) == fwd.K and len(rand) == fwd.K
    # top5 必须是池内 amt20 最大的 5 只
    assert list(top["code"]) == list(
        pool.sort_values("amt20", ascending=False).head(fwd.K)["code"])
    assert top["amt20"].is_monotonic_decreasing
    assert set(top["code"]).issubset(set(pool["code"]))
    assert set(rand["code"]).issubset(set(pool["code"]))


def test_rand5_is_reproducible_with_same_seed():
    """rand5 必须固定种子可复现，否则台账无法回溯核对。"""
    s = _fake_snapshot("20260131")
    _, _, a = fwd.pick(s, seed=123)
    _, _, b = fwd.pick(s, seed=123)
    _, _, c = fwd.pick(s, seed=124)
    assert list(a["code"]) == list(b["code"])
    assert list(a["code"]) != list(c["code"]) or len(s) < 20


def test_pool_respects_f3_and_liquidity_cuts():
    s = _fake_snapshot("20260131")
    pool, _, _ = fwd.pick(s)
    assert (pool["f3pct"] <= fwd.THRESH).all()
    assert (pool["liqpct"] < fwd.LIQ_MAX).all()


# ------------------------------------------------------------------ 台账字段
def test_ledger_has_all_14_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(fwd, "LEDGER", tmp_path)
    monkeypatch.setattr(fwd, "FWD", tmp_path)
    monkeypatch.setattr(fwd, "build_snapshot", _fake_snapshot)
    monkeypatch.setattr(fwd, "judge_fills", lambda t, d: _fake_fills(t))
    monkeypatch.setattr(fwd, "sha256_file", lambda p: "deadbeef")
    monkeypatch.setattr(sys, "argv", ["p13", "--asof", "29991231"])
    fwd.main()
    rec = _load_one(tmp_path)
    required = ["date", "config_sha256", "signal_pool_n", "target_holds", "orders",
                "fill_status", "fill_price", "fees", "nav", "cash", "stock_weight",
                "n_positions", "unfilled_orders", "ledger_sha256"]
    missing = [k for k in required if k not in rec]
    assert not missing, f"台账缺字段 {missing}"


def test_ledger_hash_is_content_dependent(monkeypatch, tmp_path):
    monkeypatch.setattr(fwd, "LEDGER", tmp_path)
    monkeypatch.setattr(fwd, "FWD", tmp_path)
    monkeypatch.setattr(fwd, "build_snapshot", _fake_snapshot)
    monkeypatch.setattr(fwd, "judge_fills", lambda t, d: _fake_fills(t))
    monkeypatch.setattr(fwd, "sha256_file", lambda p: "deadbeef")
    monkeypatch.setattr(sys, "argv", ["p13", "--asof", "29991231"])
    fwd.main()
    rec = _load_one(tmp_path)
    import hashlib
    import json
    body = {k: v for k, v in rec.items() if k != "ledger_sha256"}
    expect = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True)
                            .encode("utf-8")).hexdigest()[:16]
    assert rec["ledger_sha256"] == expect


def test_observations_csv_is_overwritten_not_duplicated(monkeypatch, tmp_path):
    """同一决策日重跑应覆盖汇总行，不能追加成两行（否则月数被虚增）。"""
    monkeypatch.setattr(fwd, "LEDGER", tmp_path)
    monkeypatch.setattr(fwd, "FWD", tmp_path)
    monkeypatch.setattr(fwd, "build_snapshot", _fake_snapshot)
    monkeypatch.setattr(fwd, "judge_fills", lambda t, d: _fake_fills(t))
    monkeypatch.setattr(fwd, "sha256_file", lambda p: "deadbeef")
    monkeypatch.setattr(sys, "argv", ["p13", "--asof", "29991231"])
    fwd.main()
    fwd.main()
    obs = pd.read_csv(tmp_path / "observations.csv")
    assert len(obs) == 1, f"应只有 1 行，实际 {len(obs)}"


# ------------------------------------------------------------------ 桩
def _fake_snapshot(me: str, close_hi=None):
    rng = np.random.default_rng(7)
    n = 400
    codes = [f"{i:06d}" for i in range(1, n + 1)]
    close = rng.uniform(2, 120, n)
    s = pd.DataFrame({
        "date": me, "code": codes, "close": close,
        "f2": 0.02 + rng.normal(0, 0.01, n), "vol20": 0.3 + rng.normal(0, 0.05, n),
        "f3": rng.uniform(0.3, 2.0, n), "amt20": rng.uniform(6e6, 8e7, n),
        "stale_days": 0, "next_td": "20260202",
    })
    base = (s["f2"].notna() & s["vol20"].notna() & (s["amt20"] > fwd.AMT_MIN)
            & s["f3"].notna() & (s["stale_days"] <= 30))
    hi = 1e9 if close_hi is None else close_hi
    s["ok_buy"] = base & s["close"].between(1.0, hi)
    s.attrs["base_filter"] = base
    return s


def _fake_fills(t: pd.DataFrame):
    return pd.DataFrame({"code": t["code"].tolist(), "side": "buy",
                         "status": "FILLED", "close": t["close"].tolist()})


def _load_one(tmp_path):
    import json
    files = sorted(tmp_path.glob("*.json"))
    assert files, "没有生成台账"
    return json.loads(files[-1].read_text(encoding="utf-8"))
