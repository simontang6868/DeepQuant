# -*- coding: utf-8 -*-
"""Phase 1.3 硬化的行为测试。

对应 `docs/PHASE1_ACCEPTANCE_CRITERIA_2026-11-02.md` §3.1 的 1.3-a ~ 1.3-i。
每条测试都打在一个**会写文件的行为**上，不依赖跑全量截面（用桩替换 build_snapshot）。

为什么不跑真实快照：一次 build_snapshot 要遍历 5,444 个 CSV，单测会变成几分钟。
桩只替代「读数据」这一步；写盘、覆盖保护、模式判定、心跳、自检全部走真实代码。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SP5 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SP5))
sys.path.insert(0, str(SP5.parent))

import p13_forward as pf  # noqa: E402
import p13b_forward_track as pb  # noqa: E402


# ---------------------------------------------------------------- 桩
def _stub_snapshot(me: str, close_hi: float | None = None) -> pd.DataFrame:
    """30 只、amt20 递增。liqpct<0.2 ⇒ 只有最小的 5 只进池，正好凑满 top5。"""
    rows = []
    for k in range(30):
        code = f"{300000 + k:06d}"
        rows.append({
            "date": me, "code": code, "close": 10.0 + k * 0.1,
            "f2": 1.0, "vol20": 0.02, "f3": 1.0 + k,
            "amt20": 1e7 * (1 + k), "stale_days": 0,
            "next_td": None,          # 数据未到次日 ⇒ 成交挂起（1.3-e）
        })
    s = pd.DataFrame(rows)
    base = pd.Series([True] * len(s))
    s.attrs["base_filter"] = base
    s["ok_buy"] = base
    return s


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 FWD/LEDGER/HB/CORRECTIONS/KF 全部指到 tmp，避免污染真实 forward/。"""
    fwd = tmp_path / "forward"
    ledger = fwd / "ledger"
    cfg = fwd / "sp4_config_v1.json"
    fwd.mkdir(parents=True)
    cfg.write_text('{"version":"sp4-v1"}', encoding="utf-8")
    # 真实的 forward/ 里同时存在 .sha256；verify 会校验它，fixture 必须还原这个前提
    (fwd / "sp4_config_v1.sha256").write_text(
        hashlib.sha256(cfg.read_bytes()).hexdigest() + "  sp4_config_v1.json\n", encoding="utf-8")

    monkeypatch.setattr(pf, "FWD", fwd)
    monkeypatch.setattr(pf, "LEDGER", ledger)
    monkeypatch.setattr(pf, "HB", fwd / "heartbeat.json")
    monkeypatch.setattr(pf, "CORRECTIONS", fwd / "corrections.jsonl")
    monkeypatch.setattr(pf, "KF", cfg)
    monkeypatch.setattr(pf, "build_snapshot", _stub_snapshot)
    monkeypatch.setattr(pf, "data_last_date", lambda: "20260930")
    return fwd


def _run(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["p13_forward.py", *argv])
    return pf.main()


def _all_files(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------- 1.3-d 决策日
def test_decision_day_is_last_data_day():
    """数据止于某月最后一个交易日 ⇒ 决策日就是它，不是上一个月。

    这是原实现的核心缺陷：它要求「该月之后还有数据」，于是恒取上一个月。
    """
    cal = ["20260929", "20260930", "20261029", "20261030"]
    me, why = pf.latest_closed_month(cal=cal, today="20261102")
    assert me == "20261030", why
    assert "距今" in why


def test_decision_day_ignores_current_month_partial_data():
    """数据已含当月部分交易日（11-02）⇒ 决策日仍是上一个月末 10-30。

    ⚠️ 契约已更新（2026-11-03, R2 第 2 项）：决策日来自**独立日历**，
    不再取"数据末日"。旧实现在日更链下会返回 decision=20261102 ——
    一个格式合法、但根本不是月末的决策日。本测试断言它不会发生。
    """
    cal = ["20260929", "20260930", "20261029", "20261030", "20261102"]
    d, why = pf.latest_closed_month(cal=cal, today="20261102")
    assert d == "20261030", why
    assert d != "20261102", "不得把当月某个非月末日当决策日"


def test_decision_day_refuses_when_data_does_not_cover_month_end():
    """数据止于 10-16、未覆盖月末 10-30 ⇒ 拒绝（MonthEndUnverified）。

    ⚠️ 契约已更新：这种情况以前被称作"数据过期"（gap 17 天）。但现在 gap 不再是
    判据——**数据只要没盖住月末就必须拒**，哪怕只差一天（见 month_end_calendar 组）。
    """
    cal = ["20260929", "20260930", "20261015", "20261016"]
    with pytest.raises(pf.MonthEndUnverified, match="未覆盖"):
        pf.latest_closed_month(cal=cal, today="20261102")


def test_decision_day_refuses_when_window_missed():
    """数据完整但跑得太晚（距月末 20 天）⇒ DecisionWindowMissed。

    与"数据未覆盖"分开：这种情形下**数据是最新的**，错的是调度时机。
    混成一个错误会让人去查数据源，而该改的是跑批时间。
    """
    cal = ["20261029", "20261030", "20261119"]
    with pytest.raises(pf.DecisionWindowMissed, match="窗口已过"):
        pf.latest_closed_month(cal=cal, today="20261119")


def test_stale_gate_boundary():
    """上限 7 天：7 天放行、8 天拒绝。边界必须精确，否则周末会被误杀。"""
    cal = ["20260930"]
    assert pf.latest_closed_month(cal=cal, today="20261007")[0] == "20260930"
    with pytest.raises(pf.DataStaleError):
        pf.latest_closed_month(cal=cal, today="20261008")


# ---------------------------------------------------------------- 1.3-e 成交挂起
def test_next_td_unknown_is_pending_not_judged_on_decision_day():
    """next_td 未知时必须是 PENDING_NO_CALENDAR。

    原实现回退成「用决策日自己当执行日」，会把信号日当成交日 —— 静默错判。
    """
    tgt = pd.DataFrame([{"code": "000001", "next_td": None},
                        {"code": "000002", "next_td": float("nan")}])
    fills = pf.judge_fills(tgt)
    assert list(fills["status"]) == ["PENDING_NO_CALENDAR"] * 2
    assert fills["close"].isna().all()


# ---------------------------------------------------------------- 1.3-f 来源
def test_provenance_records_code_hash_and_git_truth():
    prov = pf.code_provenance()
    real = hashlib.sha256(Path(pf.__file__).read_bytes()).hexdigest()
    assert prov["code_sha"] == real
    assert prov["code_sha_algo"] == "sha256"
    assert prov["code_in_git"] is False, "research/ 未入 git，必须如实写 False"
    assert prov["git_commit"] == "NOT_IN_GIT", "未跟踪时不得写一个看似可定位的 commit"
    assert prov["code_path"].endswith("p13_forward.py")


# ---------------------------------------------------------------- 1.3-a dry-run
def test_dry_run_writes_nothing(env, monkeypatch):
    before = _all_files(env)
    rc = _run(monkeypatch, "--asof", "20260930", "--dry-run")
    assert rc == 0
    assert _all_files(env) == before, "dry-run 不得写台账/汇总/心跳/明细"
    assert not (env / "ledger").exists()


def test_dry_run_on_stale_data_also_writes_nothing(env, monkeypatch):
    """数据过期走 abort 分支时，也不得写心跳。

    这条是自查出来的：首版在 abort 分支无条件写心跳，与「只算不写」冲突。
    """
    monkeypatch.setattr(pf, "data_last_date", lambda *a, **k: "20261016")
    before = _all_files(env)
    rc = _run(monkeypatch, "--dry-run")
    assert rc == 3
    assert _all_files(env) == before


# ---------------------------------------------------------------- 1.3-g 心跳
def test_heartbeat_written_on_success(env, monkeypatch):
    rc = _run(monkeypatch, "--asof", "20260930")
    assert rc == 0
    hb = json.loads((env / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["decision"] == "20260930"
    assert hb["ok"] is True
    assert hb["mode"] in {"LATE_RUN", "SELFTEST", "FORWARD"}
    assert "duration_s" in hb


def test_heartbeat_written_on_stale_abort(env, monkeypatch):
    monkeypatch.setattr(pf, "data_last_date", lambda *a, **k: "20261016")
    rc = _run(monkeypatch)
    assert rc == 3
    hb = json.loads((env / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["ok"] is False and hb["mode"] == "ABORT_STALE"


# ---------------------------------------------------------------- 1.3-b 不可变
def test_ledger_immutable_refuses_second_run(env, monkeypatch):
    assert _run(monkeypatch, "--asof", "20260930") == 0
    led = env / "ledger" / "20260930.json"
    first = led.read_bytes()

    rc = _run(monkeypatch, "--asof", "20260930")
    assert rc == 5, "第二条同月台账必须被拒绝"
    assert led.read_bytes() == first, "被拒绝的运行不得改动既有台账"


def test_force_backs_up_and_appends_correction(env, monkeypatch):
    """--force 覆盖：先备份旧件原字节，再写新件，并追加更正日志。

    ⚠️ 2026-11-02 修正前提：原版两次运行都用同样参数，断言「新件字节必须不同」。
    它能通过，靠的其实是前持仓 bug —— 第二次运行把第一次的台账当成「上一期」，
    于是 prev_holdings 变了、字节自然不同。bug 修掉后两次运行**幂等**，断言失效。
    现在改为构造真实差异：第一次 --selftest（SELFTEST），第二次默认（LATE_RUN）。
    断言本身一字未改，只是不再依赖缺陷。
    """
    monkeypatch.setattr(pf, "_today_str", lambda: "20261002")   # 距 09-30 两天 ⇒ 补跑窗内
    assert _run(monkeypatch, "--asof", "20260930", "--selftest") == 0
    led = env / "ledger" / "20260930.json"
    first = led.read_bytes()
    assert json.loads(first)["mode"] == "SELFTEST"

    assert _run(monkeypatch, "--asof", "20260930", "--force") == 0
    assert json.loads(led.read_bytes())["mode"] == "LATE_RUN"
    backups = list((env / "ledger").glob("20260930.json.bak.*"))
    assert len(backups) == 1, "覆盖前必须备份"
    assert backups[0].read_bytes() == first, "备份必须是旧件原字节"
    assert led.read_bytes() != first, "新件应已写入"

    lines = [json.loads(x) for x in
             (env / "corrections.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(x["event"] == "LEDGER_OVERWRITTEN_WITH_FORCE" for x in lines)


# ---------------------------------------------------------------- 1.3-c 补跑
def test_late_run_is_marked_and_counted(env, monkeypatch):
    """决策日早于今天且在周末跨度内 ⇒ LATE_RUN，标注、计入观察期、留更正行。"""
    # 冻结「今天」为 2 天后：10-02 是周五，距 09-30 仅 2 天，落在补跑容忍窗内
    monkeypatch.setattr(pf, "_today_str", lambda: "20261002")
    rc = _run(monkeypatch, "--asof", "20260930", "--late-run")
    assert rc == 0
    rec = json.loads((env / "ledger" / "20260930.json").read_text(encoding="utf-8"))
    assert rec["mode"] == "LATE_RUN"
    assert rec["late_run"] is True
    assert rec["late_calendar_days"] > 0
    assert rec["counts_toward_observation"] is True

    lines = [json.loads(x) for x in
             (env / "corrections.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(x["event"] == "LATE_RUN_RECORDED" for x in lines)


def test_late_run_flag_rejected_when_not_late(env, monkeypatch):
    """--late-run 不能贴到非补跑上（防止人为制造「补跑」标签）。"""
    monkeypatch.setattr(pf, "_today_str", lambda: "20260930")   # 决策日 == 今天
    rc = _run(monkeypatch, "--asof", "20260930", "--late-run")
    assert rc == 4


def test_long_delay_is_force_downgraded_to_selftest(env, monkeypatch):
    """距今 >3 天必须强制降级为 SELFTEST —— 既有闸门，不得因新语义丢失。

    理由：一个月前的月末信号，其下一期收益已经部分可见，
    记成 FORWARD/LATE_RUN 等于把回溯伪装成样本外。
    """
    monkeypatch.setattr(pf, "_today_str", lambda: "20261102")   # 距 09-30 共 33 天
    assert _run(monkeypatch, "--asof", "20260930") == 0
    rec = json.loads((env / "ledger" / "20260930.json").read_text(encoding="utf-8"))
    assert rec["mode"] == "SELFTEST"
    assert rec["counts_toward_observation"] is False


def test_selftest_not_counted(env, monkeypatch):
    rc = _run(monkeypatch, "--asof", "20260930", "--selftest")
    assert rc == 0
    rec = json.loads((env / "ledger" / "20260930.json").read_text(encoding="utf-8"))
    assert rec["mode"] == "SELFTEST"
    assert rec["counts_toward_observation"] is False
    obs = (env / "observations.csv").read_text(encoding="utf-8-sig")
    assert "计入观察期=False" in obs


def test_record_carries_provenance_and_data_last_date(env, monkeypatch):
    assert _run(monkeypatch, "--asof", "20260930", "--selftest") == 0
    rec = json.loads((env / "ledger" / "20260930.json").read_text(encoding="utf-8"))
    assert rec["data_last_date"] == "20260930"
    assert rec["code_sha"] == hashlib.sha256(Path(pf.__file__).read_bytes()).hexdigest()
    assert rec["git_commit"] == "NOT_IN_GIT"
    assert len(rec["ledger_sha256"]) == 16


# ---------------------------------------------------------------- 1.3-h 自检
def test_verify_is_readonly(env, monkeypatch):
    assert _run(monkeypatch, "--asof", "20260930", "--selftest") == 0
    before = _all_files(env)
    rc = _run(monkeypatch, "--verify")
    assert rc == 0
    assert _all_files(env) == before, "--verify 不得写任何文件"


def test_verify_detects_tampered_ledger(env, monkeypatch):
    assert _run(monkeypatch, "--asof", "20260930", "--selftest") == 0
    led = env / "ledger" / "20260930.json"
    rec = json.loads(led.read_text(encoding="utf-8"))
    rec["target_holds"] = ["999999"]          # 改内容但不动 ledger_sha256
    led.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert _run(monkeypatch, "--verify") == 1


def test_verify_detects_config_drift(env, monkeypatch):
    assert _run(monkeypatch, "--asof", "20260930", "--selftest") == 0
    (env / "sp4_config_v1.sha256").write_text("0" * 64 + "  sp4_config_v1.json\n", encoding="utf-8")
    assert _run(monkeypatch, "--verify") == 1


# ---------------------------------------------------------------- 1.3-i p13b
def _ledger(tmp: Path, counts: bool) -> Path:
    p = tmp / f"dec_{int(counts)}.json"
    p.write_text(json.dumps({
        "date": "20260930", "mode": "FORWARD" if counts else "SELFTEST",
        "counts_toward_observation": counts,
        "target_holds": ["000001"], "target_holds_rand5": ["000002"],
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_p13b_derives_selftest_from_ledger(tmp_path, monkeypatch):
    """台账说 SELFTEST ⇒ track 必须写 selftest=true（原实现会写成 false，已实证发生）。"""
    cal = ["20261008", "20261009"]
    monkeypatch.setattr(pb.fe, "market_calendar", lambda *a, **k: cal)
    monkeypatch.setattr(pb, "execution_day", lambda *a, **k: "20261008")
    monkeypatch.setattr(pb, "latest_data_date", lambda: "20261009")
    monkeypatch.setattr(pb, "load_st_months", lambda: {})
    monkeypatch.setattr(pb, "fill_one", lambda *a, **k: ("20261008", 10.0, "FILLED"))
    monkeypatch.setattr(pb, "track_nav", lambda *a, **k: pd.DataFrame(
        {"date": cal, "stock_value": [0.0, 0.0], "cash": [1e5, 1e5],
         "nav": [1e5, 1e5], "cost_cum": [0.0, 0.0], "dd": [0.0, 0.0]}))
    monkeypatch.setattr(pb, "NAV_DIR", tmp_path / "nav")

    rc = pb.run(str(_ledger(tmp_path, False)), None, None)
    assert rc == 0
    track = json.loads((tmp_path / "nav" / "20260930_track.json").read_text(encoding="utf-8"))
    assert track["selftest"] is True
    assert track["selftest_source"] == "ledger.counts_toward_observation"
    assert track["counts_toward_observation"] is False


def test_p13b_aborts_on_selftest_mismatch(tmp_path, monkeypatch):
    """显式 --selftest 与台账矛盾时必须报错，不得静默覆盖。"""
    rc = pb.run(str(_ledger(tmp_path, True)), None, True)
    assert rc == 6


def test_p13b_aborts_when_ledger_lacks_counts(tmp_path):
    p = tmp_path / "noledger.json"
    p.write_text(json.dumps({"date": "20260930", "target_holds": []}), encoding="utf-8")
    assert pb.run(str(p), None, None) == 6


# ---------------------------------------------------------------- 前持仓来源
def _ledger_file(d: Path, name: str, *, counts, holds):
    """写一份最小台账。**注意不要叫 `_ledger`**——本文件第 291 行已有同名函数（两参），
    重名会覆盖它，令 p13b 的两条测试因缺参数而报 TypeError。"""
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({
        "counts_toward_observation": counts, "target_holds": holds},
        ensure_ascii=False), encoding="utf-8")


def test_prev_holdings_ignores_selftest(tmp_path):
    """SELFTEST 的持仓**不是**真实底仓 —— 必须跳过。

    真实事故：2026-10-30 那条 LATE_RUN 把 20260930 的 SELFTEST 5 只当成前持仓，
    凭空产生 5 笔换手（目标与之完全不同）。这条钉住它。
    """
    d = tmp_path / "ledger"
    _ledger_file(d, "20260930.json", counts=False, holds=["603193", "301336"])
    hold, source, skipped = pf.resolve_prev_holdings(d, "20261030")
    assert hold == []
    assert source is None
    assert skipped == ["20260930.json"]


def test_prev_holdings_takes_latest_observation(tmp_path):
    """有多条观察期记录时取**最近**那条。"""
    d = tmp_path / "ledger"
    _ledger_file(d, "20260731.json", counts=True, holds=["AAA"])
    _ledger_file(d, "20260831.json", counts=True, holds=["BBB", "CCC"])
    _ledger_file(d, "20260930.json", counts=False, holds=["ZZZ"])
    hold, source, skipped = pf.resolve_prev_holdings(d, "20261030")
    assert hold == ["BBB", "CCC"]
    assert source == "20260831.json"
    assert skipped == ["20260930.json"]


def test_prev_holdings_excludes_self(tmp_path):
    """``--force`` 覆盖时必须排除本期自己，否则 prev_source 指向自身。

    这是实测抓到的一个真 bug：重生成 20261030 时，它把自己刚写的台账
    当成了「上一期」，于是 prev_holdings == target_holds、n_swaps 恒为 0。
    """
    d = tmp_path / "ledger"
    _ledger_file(d, "20261030.json", counts=True, holds=["SELF"])
    hold, source, skipped = pf.resolve_prev_holdings(d, "20261030")
    assert hold == []
    assert source is None
    assert skipped == []          # 自己是被「排除」，不是被「跳过」


def test_prev_holdings_requires_strict_boolean(tmp_path):
    """counts 写成字符串 "false" 不能被当成 True（`bool("false")` 为真）。"""
    d = tmp_path / "ledger"
    _ledger_file(d, "20260930.json", counts="false", holds=["BAD"])
    hold, source, _ = pf.resolve_prev_holdings(d, "20261030")
    assert hold == [] and source is None


def test_prev_holdings_missing_field_is_not_observation(tmp_path):
    """缺字段一律按「非观察期」处理（不能默认 True）。"""
    d = tmp_path / "ledger"
    d.mkdir(parents=True)
    (d / "20260930.json").write_text(json.dumps({"target_holds": ["X"]}), encoding="utf-8")
    hold, source, skipped = pf.resolve_prev_holdings(d, "20261030")
    assert hold == [] and source is None and skipped == ["20260930.json"]


def test_ledger_records_prev_source(env, monkeypatch):
    """端到端：台账里要能看出前持仓来自哪儿、跳过了谁。"""
    monkeypatch.setattr(pf, "_today_str", lambda: "20261002")
    _ledger_file(pf.LEDGER, "20260831.json", counts=False, holds=["603193"])
    assert _run(monkeypatch, "--asof", "20260930") == 0
    rec = json.loads((pf.LEDGER / "20260930.json").read_text(encoding="utf-8"))
    assert rec["prev_source"] is None                  # 观察期内首次建仓
    assert rec["prev_skipped_non_observation"] == ["20260831.json"]
    assert rec["prev_holdings"] == []                  # SELFTEST 的持仓不得当底仓
