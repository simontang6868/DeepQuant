# -*- coding: utf-8 -*-
"""Phase 1.5：实盘安全过滤（ST / 退市整理 / 停牌 / 板块）的行为测试。

对应验收项 `docs/PHASE1_5_ACCEPTANCE_2026-11-02.md` §4 的 1.5-a ~ 1.5-j。
本文件**不联网**：`fetch_state` 只测「失败不能当成不存在」这一条，
其余全部用合成输入 + 真实快照文件（若存在）验证。

为什么把纯函数单独钉住：
    这三个判定（板块 / 名称标签 / 无报价）是整条过滤链的唯一入口。
    它们写错时**不会报错**，只会静默多剔或少剔——正是「没检出 ≠ 没问题」
    最容易发生的地方。所以每个都用参数化用例 + 反例，而不是跑一遍看总数对不对。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SP5 = Path(__file__).resolve().parents[1]          # scripts/sp5
ROOT = Path(__file__).resolve().parents[3]         # research/stock_pick_2026-10-25
sys.path.insert(0, str(SP5))

import p17_universe_filter as u  # noqa: E402


# ---------------------------------------------------------------------------
# 1.5-a 板块判定：纯函数、覆盖全前缀、UNKNOWN 不留兜底
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,board", [
    ("600000", "SH_MAIN"), ("601288", "SH_MAIN"), ("603977", "SH_MAIN"), ("605319", "SH_MAIN"),
    ("688981", "STAR"), ("689009", "STAR"),
    ("000001", "SZ_MAIN"), ("001209", "SZ_MAIN"), ("002012", "SZ_MAIN"), ("003816", "SZ_MAIN"),
    ("300750", "GEM"), ("301329", "GEM"), ("302132", "GEM"),
    ("430047", "BSE"), ("832000", "BSE"), ("920001", "BSE"),
    ("200053", "SZ_B"), ("900901", "SH_B"),
])
def test_board_of_prefixes(code, board):
    assert u.board_of(code) == board


@pytest.mark.parametrize("bad", ["123456", "999999", "60000", "6000000", "abcdef", "", None])
def test_board_of_unknown_is_explicit(bad):
    """未知前缀必须返回 UNKNOWN，不能猜、不能当主板。

    200053 是既有池里唯一一个「按三条前缀规则都判不出」的代码（深市 B 股）。
    若这里返回 SZ_MAIN，B 股会被当成可交易——它会一路走到下单。
    """
    assert u.board_of(bad) == u.BOARD_UNKNOWN


def test_board_d4_sets_are_disjoint_choices():
    """D4 严格与宽松的差别**只有创业板**。多一项或少一项都要被发现。"""
    assert u.BOARD_D4_WITH_GEM - u.BOARD_D4_STRICT == {"GEM"}
    assert not (u.BOARD_D4_STRICT - u.BOARD_D4_WITH_GEM)


# ---------------------------------------------------------------------------
# 1.5-b / 1.5-c 名称标签
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,expect_st,expect_del", [
    ("浦发银行", False, False),
    ("平安银行", False, False),
    ("*ST美丽", True, False),
    ("ST星源", True, False),
    ("SST天海", True, False),
    ("S*ST前锋", True, False),
    ("*ST康佳A", True, False),
    ("国华退", False, True),
    ("神城A退", False, True),
    ("退市海润", False, True),
    # 同时命中：退市整理期 + 风险警示。用异或会漏掉这一类。
    ("*STXX退", True, True),
    # 反例：中文里含「退」「斯」的正常名称不得命中
    ("斯特威", False, False),
    ("", False, False),
])
def test_classify_name(name, expect_st, expect_del):
    r = u.classify_name(name)
    assert r["is_st"] is expect_st
    assert r["is_delisting"] is expect_del


def test_st_and_delisting_are_independent_not_exclusive():
    """两个标签是「与」的关系可以同时为真，不是互斥。

    这是 1.5 里最容易写反的一条：若实现成 `is_st ^ is_delisting`，
    `*STXX退` 会两个都为 False，于是**既不被 ST 剔也不被退市剔**。
    """
    r = u.classify_name("*STXX退")
    assert r["is_st"] is True and r["is_delisting"] is True


# ---------------------------------------------------------------------------
# 1.5-d 无报价判定
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ts,asof,expect", [
    ("20261102161430", "20261102", False),   # 正常收盘后
    ("20261102093001", "20261102", False),   # 刚过 09:30:00
    ("20261102093000", "20261102", True),    # 恰好 09:30:00 = 无连续竞价
    ("20261102090000", "20261102", True),    # 09:00 占位（停牌/已退市）
    ("20261030150000", "20261102", True),    # 日期不是当天
    ("", "20261102", True),                  # 空
    ("20261102", "20261102", True),          # 长度不足
])
def test_judge_no_quote(ts, asof, expect):
    assert u.judge_no_quote(ts, asof) is expect


def test_holiday_must_be_rejected_not_flagged_as_suspended(monkeypatch):
    """休市日抓取必须被拒绝。

    这是本条最危险的失败模式：休市日全市场时间戳都是 09:00，
    若闸门放行，会得出「今天 100% 股票停牌」——一个荒谬但格式正确的结论。
    所以闸门判的是「有连续竞价成交的比例」，而不是任何单只股票。
    """
    rows = [{"code": f"{600000 + i:06d}", "no_quote_today": True, "no_data": False,
             "board": "SH_MAIN", "name": "", "last_price": None, "quote_ts": "20261102090000",
             "is_st": False, "is_delisting": False}
            for i in range(100)]
    s = u.summarize(rows, "20261102")
    ok, why = u.is_trading_day(s)
    assert ok is False
    assert "不是交易日" in why or "停牌" in why


def test_trading_day_passes_when_most_quoted():
    rows = [{"code": f"{600000 + i:06d}", "no_quote_today": i >= 80, "no_data": False,
             "board": "SH_MAIN", "name": "", "last_price": None, "quote_ts": "",
             "is_st": False, "is_delisting": False}
            for i in range(100)]
    ok, _ = u.is_trading_day(u.summarize(rows, "20261102"))
    assert ok is True


def test_missing_codes_are_not_silently_tradable():
    """拉不到 ≠ 可交易。缺失代码必须标 no_data 且 no_quote_today=True。"""
    rows = u.build_rows(["600000", "600001"], {"600000": {
        "code": "600000", "name": "浦发银行", "last_price": 10.0,
        "quote_ts": "20261102150000"}}, "20261102")
    by = {r["code"]: r for r in rows}
    assert by["600001"]["no_data"] is True
    assert by["600001"]["no_quote_today"] is True
    assert by["600000"]["no_data"] is False


# ---------------------------------------------------------------------------
# 1.5-e / 1.5-f 快照落盘、自校验、重写保护
# ---------------------------------------------------------------------------
@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "STATE_DIR", tmp_path / "universe_state")
    monkeypatch.setattr(u, "CORRECTIONS", tmp_path / "corrections.jsonl")
    return tmp_path


def _fake_rows(n=6):
    return [{"code": f"{600000 + i:06d}", "name": "测试", "last_price": 10.0,
             "quote_ts": "20261102150000", "board": "SH_MAIN", "is_st": False,
             "is_delisting": False, "no_quote_today": False, "no_data": False}
            for i in range(n)]


def test_state_roundtrip_and_hash(isolated_state):
    rows = _fake_rows()
    s = u.summarize(rows, "20261102")
    p = u.write_state(rows, s, {"fetch_ts": "t", "source": "qt.gtimg.cn"})
    assert p.exists()
    st = u.load_state("20261102")
    assert st is not None
    assert st["n_total"] == 6
    assert "sha256" in st and len(st["sha256"]) == 64
    # 自校验哈希必须能复算
    assert st["sha256"] == u._sha_of_payload({k: v for k, v in st.items() if k != "sha256"})


def test_state_tamper_is_detected(isolated_state):
    rows = _fake_rows()
    u.write_state(rows, u.summarize(rows, "20261102"), {"fetch_ts": "t"})
    jp, _ = u.state_paths("20261102")
    d = json.loads(jp.read_text(encoding="utf-8"))
    d["stocks"]["600000"]["is_st"] = True          # 偷偷改一只
    jp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="自校验失败"):
        u.load_state("20261102")


def test_state_rewrite_backs_up_and_logs(isolated_state):
    """同日重抓：旧件必须留备份，且追加更正日志——不能静默替换。"""
    rows = _fake_rows()
    u.write_state(rows, u.summarize(rows, "20261102"), {"fetch_ts": "t1"})
    jp, cp = u.state_paths("20261102")
    first = jp.read_bytes()
    u.write_state(rows, u.summarize(rows, "20261102"), {"fetch_ts": "t2"})
    backups = list(u.STATE_DIR.glob("*.bak.*"))
    assert backups, "重写时必须留下 .bak.<UTC> 备份"
    assert any(b.name.startswith("20261102.json.bak.") for b in backups)
    log = u.CORRECTIONS.read_text(encoding="utf-8").strip().splitlines()
    assert log, "重写必须追加 corrections.jsonl"
    ev = json.loads(log[-1])
    assert ev["event"] == "UNIVERSE_STATE_REWRITTEN"
    assert ev["asof"] == "20261102"
    assert first != jp.read_bytes()                # 内容因 fetch_ts 不同而变
    assert cp.exists()


def test_load_state_missing_returns_none(isolated_state):
    assert u.load_state("20991231") is None


# ---------------------------------------------------------------------------
# 1.5-g / 1.5-i 账本口径：三本账互不污染，缺快照不得回退成「不过滤」
# ---------------------------------------------------------------------------
def _rows_with_flags():
    return [
        {"code": "600001", "name": "正常主板", "board": "SH_MAIN", "is_st": False,
         "is_delisting": False, "no_quote_today": False, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
        {"code": "000001", "name": "正常深主板", "board": "SZ_MAIN", "is_st": False,
         "is_delisting": False, "no_quote_today": False, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
        {"code": "300001", "name": "创业板", "board": "GEM", "is_st": False,
         "is_delisting": False, "no_quote_today": False, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
        {"code": "600002", "name": "*ST某某", "board": "SH_MAIN", "is_st": True,
         "is_delisting": False, "no_quote_today": False, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
        {"code": "600003", "name": "某某退", "board": "SH_MAIN", "is_st": False,
         "is_delisting": True, "no_quote_today": False, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
        {"code": "600004", "name": "停牌股", "board": "SH_MAIN", "is_st": False,
         "is_delisting": False, "no_quote_today": True, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
        {"code": "688001", "name": "科创", "board": "STAR", "is_st": False,
         "is_delisting": False, "no_quote_today": False, "no_data": False,
         "last_price": 10.0, "quote_ts": ""},
    ]


def test_tradable_mask_strict_only_main_boards():
    m = u.tradable_mask(_rows_with_flags(), with_gem=False)
    assert m == {"600001": True, "000001": True, "300001": False, "600002": False,
                 "600003": False, "600004": False, "688001": False}


def test_tradable_mask_with_gem_adds_only_gem():
    strict = u.tradable_mask(_rows_with_flags(), with_gem=False)
    gem = u.tradable_mask(_rows_with_flags(), with_gem=True)
    diff = {c for c in gem if gem[c] != strict[c]}
    assert diff == {"300001"}


def test_every_exclusion_reason_alone_is_enough():
    """四个排除项各自独立生效：任何一个为真都必须被剔。

    若实现成 `and` 链写错（比如写成 `or not x`），单独命中一项的股票会漏网。
    """
    rows = _rows_with_flags()
    m = u.tradable_mask(rows, with_gem=True)
    for code, reason in (("600002", "is_st"), ("600003", "is_delisting"),
                         ("600004", "no_quote_today"), ("688001", "board")):
        assert m[code] is False, f"{code} 因 {reason} 应被剔除"
    assert m["600001"] is True


# ---------------------------------------------------------------------------
# 1.5-h 主口径不变（离线可判的部分）
# ---------------------------------------------------------------------------
def test_main_ledger_fields_untouched_by_filter():
    """过滤只应该新增账本，不能改动三本账之外的任何既有字段名。

    这里只做静态契约检查（字段集合）；逐位不变的验证在
    `--dry-run --asof 20261030` 的输出里做，见报告 §5。
    """
    import p13_forward as p13
    src = Path(p13.__file__).read_text(encoding="utf-8")
    # 主口径字段书写形式必须仍然存在且未被改名
    for key in ('"target_holds": list(target_m["code"])',
                '"target_holds_rand5": list(target_r["code"])'):
        assert key in src, f"主口径字段被改动：{key}"
    # 不得出现「用过滤后的目标覆盖主口径」的写法
    assert '"target_holds": list(target_filtered' not in src


# ---------------------------------------------------------------------------
# 1.5-i 快照不可用时 D4 必须留空，不得回退成「不过滤」
# ---------------------------------------------------------------------------
def test_universe_state_missing_dir_returns_none(isolated_state, monkeypatch):
    import p13_forward as p13
    st, reason = p13._universe_state("20261030")
    assert st is None
    assert "无快照" in reason


def test_universe_state_rejects_snapshot_older_than_decision(isolated_state):
    """用旧状态判新决策必须被拒——上个月不是 ST、这个月变 ST 的票会被放进来。"""
    import p13_forward as p13
    rows = _fake_rows()
    u.write_state(rows, u.summarize(rows, "20260101"), {"fetch_ts": "t"})
    st, reason = p13._universe_state("20261030")
    assert st is None
    assert "过期" in reason or "早于" in reason


def test_universe_state_rejects_intraday_snapshot(isolated_state):
    import p13_forward as p13
    rows = _fake_rows()
    u.write_state(rows, u.summarize(rows, "20261102"), {"fetch_ts": "t", "no_quote_reliable": False})
    st, reason = p13._universe_state("20261030")
    assert st is None
    assert "盘中" in reason


def test_d4_targets_are_none_when_state_missing(isolated_state):
    """最关键的一条：快照缺失时 D4 账本必须是 None，不能等于主口径。

    若这里返回了和主口径一样的列表，读者会以为「安全过滤没有影响」——
    而真相是「根本没过滤」。
    """
    import pandas as pd
    import p13_forward as p13
    raw = pd.DataFrame({
        "code": ["600001", "000001", "300001"], "name": ["a", "b", "c"],
        "close": [10.0, 10.0, 10.0], "f3": [0.1, 0.2, 0.3],
        "amt20": [1e7, 2e7, 3e7], "vol20": [0.01] * 3, "date": ["20261030"] * 3,
        "stale_days": [0, 0, 0], "next_td": [None] * 3,
    })
    base = pd.Series([True, True, True])
    d4 = p13.build_d4_targets(raw, base, "20261030")
    assert d4["target_holds_d4"] is None
    assert d4["target_holds_d4_gem"] is None
    assert d4["universe_state"]["applied"] is False
    assert d4["universe_state"]["reason"]


# ---------------------------------------------------------------------------
# 真实快照的一致性检查（有快照才跑，不联网）
# ---------------------------------------------------------------------------
_REAL = ROOT / "forward" / "universe_state" / "20261102.json"


@pytest.mark.skipif(not _REAL.exists(), reason="尚无真实快照")
def test_real_snapshot_full_coverage():
    """真实快照的自洽性——**走 load_state，所以同时校验 sha256**。"""
    st = u.load_state("20261102")
    assert st is not None
    assert st["n_total"] > 5000
    assert st["n_unknown_board"] == 0, "仍有代码判不出板块，规则需补前缀"
    assert st["n_no_data"] == 0, "有代码没拉到行情，不能当成可交易"
    assert st["n_is_st"] > 0 and st["n_is_delisting"] > 0
    # 板块计数之和必须等于总数（防止 summarize 漏算某一类）
    assert sum(st["board_counts"].values()) == st["n_total"]
    assert st["n_quoted"] + st["n_no_quote"] == st["n_total"]
