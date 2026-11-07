"""SHADOW 每日对账的测试。

重点是**防止自证**：没有真实仿真账户快照时，脚本必须照跑但绝不能
把「管道自检无差异」计入 G2。这一组测试大部分是在守这一条。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import shadow_daily_reconcile as sdr  # noqa: E402

DAY = "20261105"
CODES = ("600990", "603977", "002012", "600191", "600730")
PRICES = (5.41, 7.77, 9.41, 15.70, 12.30)


@pytest.fixture
def env(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "strategy": {"n_hold": 5, "stock_weight": 0.20},
        "deployment": {"initial_capital": 100000},
    }), encoding="utf-8")

    ldir = tmp_path / "ledger"
    ldir.mkdir()
    (ldir / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds": list(CODES),
    }), encoding="utf-8")

    udir = tmp_path / "universe"
    udir.mkdir()
    with (udir / f"{DAY}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "last_price", "board", "is_st", "no_quote_today"])
        for c, p in zip(CODES, PRICES):
            w.writerow([c, p, "SH_MAIN", "False", "False"])

    acct = tmp_path / "account.json"
    return {"cfg": cfg, "ldir": ldir, "udir": udir, "acct": acct,
            "tmp": tmp_path}


def real_account(day: str = DAY, **over) -> dict:
    d = {"asof": day, "source": "gm-paper-test",
         "cash": {"available": 20000.0, "frozen": 0.0, "total": 20000.0},
         "positions": []}
    d.update(over)
    return d


def build(e, day: str = DAY, **over):
    kw = dict(config_path=e["cfg"], ledger_dir=e["ldir"],
              universe_dir=e["udir"], account_path=e["acct"],
              today=day)          # 默认模拟「当天运行」
    kw.update(over)
    return sdr.build_record(day, **kw)


# ---------------------------------------------------------------- 输入与失败关闭

def test_missing_universe_snapshot_fails_closed(env):
    rec, rc = build(env, "20261106")
    assert rec["mode"] == sdr.NO_INPUTS
    assert rec["counts_toward_g2"] is False
    assert rc == sdr.EXIT_INPUT
    assert "diffs" not in rec          # 没有输入就不许产出 diff


def test_missing_ledger_fails_closed(env):
    for p in env["ldir"].glob("*.json"):
        p.unlink()
    rec, rc = build(env)
    assert rec["mode"] == sdr.NO_INPUTS and rc == sdr.EXIT_INPUT


def test_empty_targets_fails_closed(env):
    (env["ldir"] / "20261030.json").write_text(
        json.dumps({"date": "20261030", "target_holds": []}), encoding="utf-8")
    rec, rc = build(env)
    assert rec["mode"] == sdr.NO_INPUTS and rc == sdr.EXIT_INPUT


def test_latest_ledger_never_picks_future(env):
    (env["ldir"] / "20261231.json").write_text(
        json.dumps({"date": "20261231", "target_holds": ["999999"]}),
        encoding="utf-8")
    got = sdr.latest_ledger(DAY, env["ldir"])
    assert got is not None and got[0].name == "20261030.json"


def test_ledger_targets_prefers_d4(env):
    (env["ldir"] / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds": ["111111"],
        "target_holds_d4": ["600990"],
    }), encoding="utf-8")
    rec, _ = build(env)
    assert rec["targets"] == ["600990"] and rec["legacy_no_d4"] is False


# ---------------------------------------------------------------- 快照准入

@pytest.mark.parametrize("payload,reason", [
    (None, "no_account_snapshot_file"),
    (real_account(asof="20261104"), "account_snapshot_stale"),
    (real_account(source=""), "account_snapshot_missing_source"),
    (real_account(positions=None), "account_snapshot_bad_structure"),
])
def test_bad_account_snapshot_is_rejected(env, payload, reason):
    if payload is None:
        assert not env["acct"].exists()
    else:
        env["acct"].write_text(json.dumps(payload), encoding="utf-8")
    why, got = sdr.load_account_snapshot(DAY, env["acct"])
    assert got is None and reason in why


def test_stale_snapshot_never_counts_toward_g2(env):
    env["acct"].write_text(json.dumps(real_account(asof="20261104")),
                           encoding="utf-8")
    rec, rc = build(env)
    assert rec["mode"] == sdr.SELF_CHECK_ONLY
    assert rec["counts_toward_g2"] is False
    assert rc == sdr.EXIT_OK      # 退回自检，但仍要能跑完管道


def test_valid_snapshot_is_accepted(env):
    env["acct"].write_text(json.dumps(real_account()), encoding="utf-8")
    why, got = sdr.load_account_snapshot(DAY, env["acct"])
    assert why == "ok" and got["source"] == "gm-paper-test"


# ---------------------------------------------------------------- 核心不自证

def test_no_account_means_self_check_not_evidence(env):
    """⭐ 最关键：没有账户时，即使 diff 全绿也不许计入 G2。"""
    rec, rc = build(env)
    assert rec["mode"] == sdr.SELF_CHECK_ONLY
    assert rec["counts_toward_g2"] is False
    assert rec["n_orders"] > 0              # 管道确实算出了单
    assert rec["orders_sent"] is False       # 但一笔都没发
    assert rc == sdr.EXIT_OK
    assert "自检" in rec["note"]


def test_legacy_no_d4_never_counts_even_with_real_account(env):
    env["acct"].write_text(json.dumps(real_account()), encoding="utf-8")
    rec, _ = build(env)
    assert rec["mode"] == sdr.REAL_PAPER
    assert rec["legacy_no_d4"] is True
    assert rec["counts_toward_g2"] is False   # 11-30 前老台账不作证据


def test_d4_ledger_without_account_still_never_counts(env):
    """⭐ 变异 M1 抓到的洞：11-30 之后正是这个场景。

    d4 台账 + diff 全绿 + **没有仿真账户**。此时若把「必须有真实账户」
    这一条去掉，脚本会把管道自检当成证据计入 G2。之前所有测试都用
    LEGACY_NO_D4 台账，被「老台账不计入 G2」兜住了，恰好漏掉 11-30 后。
    """
    (env["ldir"] / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds_d4": list(CODES),
    }), encoding="utf-8")
    assert not env["acct"].exists()
    rec, rc = build(env)
    assert rec["legacy_no_d4"] is False
    assert rec["mode"] == sdr.SELF_CHECK_ONLY
    assert rec["n_unexplained"] == 0
    assert rec["counts_toward_g2"] is False
    assert rc == sdr.EXIT_OK


def test_real_account_with_d4_and_no_diff_counts_toward_g2(env):
    (env["ldir"] / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds_d4": list(CODES),
    }), encoding="utf-8")
    env["acct"].write_text(json.dumps(real_account()), encoding="utf-8")
    rec, rc = build(env)
    assert rec["mode"] == sdr.REAL_PAPER
    assert rec["n_unexplained"] == 0
    assert rec["counts_toward_g2"] is True
    assert rc == sdr.EXIT_OK


def test_backfill_never_counts_toward_g2(env):
    """⭐ 事后补跑不能凑连续天数：把回顾包装成连续前瞻是本项目明令禁止的。"""
    (env["ldir"] / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds_d4": list(CODES),
    }), encoding="utf-8")
    env["acct"].write_text(json.dumps(real_account()), encoding="utf-8")
    rec, rc = build(env, today="20261108")
    assert rec["is_backfill"] is True
    assert rec["mode"] == sdr.REAL_PAPER
    assert rec["n_unexplained"] == 0
    assert rec["counts_toward_g2"] is False
    assert "事后补跑" in rec["note"]
    assert rc == sdr.EXIT_OK


def test_real_account_with_unexplained_diff_returns_nonzero(env, monkeypatch):
    """⭐ 未知跳过原因绝不能被硬凑成"资金不足"，闸门必须报红。

    注意：不能拿「目标股没行情」来造这条差异——那会被正确地归到
    ``EXPLAINED_NO_QUOTE``，本就不该告警。这里用一个事前枚举之外的
    原因，才是真正需要人工核对的情形。
    """
    from deepquant.execution.planner import SkippedItem
    from deepquant.execution.shadow import ShadowPlan

    (env["ldir"] / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds_d4": ["600990"],
    }), encoding="utf-8")
    env["acct"].write_text(json.dumps(real_account()), encoding="utf-8")

    def fake_plan(record, snapshot, quotes, **kw):
        return ShadowPlan(
            decision_date=int(DAY), target_holds=("600990",),
            would_be_orders=(),
            skipped=(SkippedItem("600990", None, "柜台返回了未登记的新原因"),),
            equity=20000.0,
        )

    monkeypatch.setattr(sdr, "build_shadow_plan", fake_plan)
    rec, rc = build(env)
    assert rec["n_unexplained"] == 1
    assert rec["diffs"][0]["reason"] == "unexplained"
    assert rec["counts_toward_g2"] is False
    assert rc == sdr.EXIT_UNEXPLAINED


def test_missing_quote_is_explained_not_alarmed(env):
    """反向守住上一条：没行情是已知口径差，不该误报 UNEXPLAINED。"""
    (env["ldir"] / "20261030.json").write_text(json.dumps({
        "date": "20261030", "target_holds_d4": ["600990", "NO_SUCH_CODE"],
    }), encoding="utf-8")
    env["acct"].write_text(json.dumps(real_account()), encoding="utf-8")
    rec, rc = build(env)
    assert rec["n_unexplained"] == 0
    assert any(d["reason"] == "explained_no_quote" for d in rec["diffs"])
    assert rc == sdr.EXIT_OK


# ---------------------------------------------------------------- 差异归因纪律

def test_unknown_skip_reason_is_not_explained_away():
    """把不认识的原因硬塞进"资金不足"= 自己把闸门关了。必须留 UNEXPLAINED。"""
    from deepquant.execution.planner import SkippedItem
    uni = {"600990": {"last": 5.41, "board": "SH_MAIN",
                      "is_st": False, "no_quote": False}}
    ctx = sdr.make_ctx(uni, (SkippedItem("600990", None, "某个人类还没见过的原因"),))
    assert "600990" not in ctx.cash_short_codes
    assert "600990" not in ctx.lot_short_codes


def test_no_quote_code_has_no_last_price_so_it_explains_as_no_quote():
    uni = {"600990": {"last": 5.41, "board": "SH_MAIN",
                      "is_st": False, "no_quote": True}}
    ctx = sdr.make_ctx(uni, ())
    assert "600990" not in ctx.last_price


def test_quotes_mark_no_quote_as_stale():
    q = sdr.make_quotes({"600990": {"last": 5.41, "board": "",
                                    "is_st": False, "no_quote": True}}, DAY)
    assert q["600990"].stale is True
    assert q["600990"].ts == int(DAY)


# ---------------------------------------------------------------- 写入与统计

def test_write_is_idempotent_but_never_overwrites_different_content(tmp_path):
    p, rc = sdr.write_record({"date": DAY, "a": 1}, DAY, out_dir=tmp_path)
    assert rc == sdr.EXIT_OK and p.is_file()
    assert sdr.write_record({"date": DAY, "a": 1}, DAY,
                            out_dir=tmp_path)[1] == sdr.EXIT_OK
    assert sdr.write_record({"date": DAY, "a": 2}, DAY,
                            out_dir=tmp_path)[1] == sdr.EXIT_CONFLICT
    assert json.loads(p.read_text(encoding="utf-8"))["a"] == 1


def test_records_are_lf_only(tmp_path):
    sdr.write_record({"date": DAY, "a": 1}, DAY, out_dir=tmp_path)
    assert b"\r" not in (tmp_path / f"{DAY}.json").read_bytes()


def test_g2_summary_breaks_at_first_missing_day(tmp_path):
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps({"dates": ["20261103", "20261104",
                                         "20261105", "20261106"]}),
                   encoding="utf-8")
    for d in ("20261103", "20261104", "20261106"):
        (tmp_path / f"{d}.json").write_text(
            json.dumps({"date": d, "counts_toward_g2": True}), encoding="utf-8")
    s = sdr.g2_summary(tmp_path, cal, today="20261106")
    assert s["n_qualified_days"] == 3
    assert s["streak"] == 1                 # 11-06 有，11-05 断
    assert s["first_break"] == "20261105"
    assert s["passed"] is False


def test_g2_summary_passes_at_20(tmp_path):
    days = [f"202611{d:02d}" for d in range(1, 21)]
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps({"dates": days}), encoding="utf-8")
    for d in days:
        (tmp_path / f"{d}.json").write_text(
            json.dumps({"date": d, "counts_toward_g2": True}), encoding="utf-8")
    s = sdr.g2_summary(tmp_path, cal, today=days[-1])
    assert s["streak"] == 20 and s["passed"] is True


# ---------------------------------------------------------------- 静态：绝不发单

def test_script_never_imports_a_broker_adapter():
    src = (ROOT / "scripts" / "shadow_daily_reconcile.py").read_text(encoding="utf-8")
    banned = ["place_order(", "order_volume(", "import gmtrade",
              "import xtquant", "from deepquant.broker.adapters",
              "MockBroker(", "send_order("]
    assert not [b for b in banned if b in src]


def test_shadow_module_signature_still_has_no_adapter():
    """接进日更链不许绕过 2.9-a 的"签名里没有券商对象"。"""
    import inspect
    from deepquant.execution.shadow import build_shadow_plan
    names = set(inspect.signature(build_shadow_plan).parameters)
    assert not (names & {"broker", "adapter", "client", "gateway", "trader"})
