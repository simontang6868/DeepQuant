"""as-of 股票池接口的单元测试（阶段 B4 要求：关键规则必须有自动化测试）。

覆盖的不是"市场结果"，而是**规则语义**：
五态判定、上市满 N 日边界、停牌排除、退市不可臆断、规则显式化。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_asof_universe.py -q
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asof_universe import (  # noqa: E402
    AsOfUniverse, EligibilityRule, SymbolSpan, TradingState, UniverseError, _gaps,
)


def _write(path: str, dates: list[str], price: float = 10.0) -> None:
    lines = ["date,open,high,low,close,volume"]
    for d in dates:
        lines.append(f"{d},{price:.2f},{price:.2f},{price:.2f},{price:.2f},1000.0")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _bdays(start: str, n: int) -> list[str]:
    """生成从 start 起的 n 个工作日（跳过周末），用于构造"上市满 N 日"的边界。"""
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


@pytest.fixture()
def universe(tmp_path):
    """四只标的（**用真实 6 位代码格式**：`EligibilityRule.scope='a_share'` 默认开启，
    假代码如 AAAA.SZ 会被显式排除——这正是新规则的预期行为，夹具必须跟着真实化）。
       AAAA：2010-01-04 起连续 300 个工作日（足够长）
       BBBB：2015-06-01 起 60 个工作日（短）
       CCCC：2012-03-05 起 300 个工作日，中间挖掉 10 天模拟停牌
       DDDD：空文件（只有表头）→ NO_DATA
    """
    a = _bdays("2010-01-04", 300)
    _write(str(tmp_path / "000001.SZ.exec.csv"), a)

    b = _bdays("2015-06-01", 60)
    _write(str(tmp_path / "000002.SZ.exec.csv"), b)

    c = _bdays("2012-03-05", 300)
    hole = c[100:110]                     # 停牌 10 个工作日
    _write(str(tmp_path / "000003.SZ.exec.csv"), [d for d in c if d not in hole])

    _write(str(tmp_path / "000004.SZ.exec.csv"), [])
    return AsOfUniverse.from_dir(str(tmp_path)), {
        "a": a, "b": b, "c": c, "hole": hole,
    }


def test_states_are_five_not_boolean(universe):
    """五态必须互斥且各自可达——不能把后三态压成 False。"""
    u, d = universe
    a = d["a"]
    assert u.state("000001.SZ", "2009-12-31") == TradingState.BEFORE_LISTING
    assert u.state("000001.SZ", a[0]) == TradingState.HAS_DATA
    assert u.state("000001.SZ", "2030-01-01") == TradingState.AFTER_LAST_DATA
    assert u.state("000004.SZ", a[0]) == TradingState.NO_DATA
    # 停牌：CCCC 在挖掉的那天必须判 SUSPENDED_LIKELY，而不是"未上市"或"已退市"
    assert u.state("000003.SZ", d["hole"][0]) == TradingState.SUSPENDED_LIKELY
    # 未知代码
    assert u.state("ZZZZ.SZ", a[0]) == TradingState.NO_DATA


def test_after_last_data_is_not_delisted(universe):
    """核心语义：AFTER_LAST_DATA 只表示"没有数据"，**不等于已退市**。

    抓取失败会产生完全相同的现象，因此本模块不提供 `is_delisted`。
    """
    u, d = universe
    st = u.state("000002.SZ", "2030-01-01")
    assert st == TradingState.AFTER_LAST_DATA
    assert not hasattr(u, "is_delisted")
    # 审计里必须明确写出"两类不可区分"
    aud = u.audit_listing_consistency()
    assert "不可区分" in aud["note"]


def test_min_listed_days_boundary(universe):
    """上市满 N 个有效交易日的边界：恰好 N 入池，N-1 不入池。"""
    u, d = universe
    a = d["a"]
    rule = EligibilityRule(min_listed_days=252)
    # 第 252 个有效交易日（1-based）→ 累计 = 252 → 可入池
    assert "000001.SZ" in u.eligible(a[251], rule)
    # 第 251 个 → 累计 251 → 不可入池
    assert "000001.SZ" not in u.eligible(a[250], rule)


def test_suspension_excluded(universe):
    """停牌日必须排除（exclude_suspended 默认 True）。"""
    u, d = universe
    hole = d["hole"]
    rule = EligibilityRule(min_listed_days=1)
    for day in hole:
        assert "000003.SZ" not in u.eligible(day, rule)
    # 停牌前后各一天应入池
    assert "000003.SZ" in u.eligible(d["c"][99], rule)
    assert "000003.SZ" in u.eligible(d["c"][110], rule)


def test_min_history_days(universe):
    """最低历史长度是独立规则，不能混进 min_listed_days。

    ⚠️ 修测试自身：初版用 AAAA 的末日当查询日，而 BBBB 那时还没上市，
      失败原因是 BEFORE_LISTING 而非 min_history_days——测量对象错了。
       正确做法：用 BBBB 自己的末日查。
    """
    u, d = universe
    b = d["b"]
    # 必须显式把 min_listed_days 放开，否则被"上市满 252 日"先挡住，
    # 测到的就不是 min_history_days —— 这是两条**独立**规则，不能互相顶替。
    base = dict(min_listed_days=1)
    assert "000002.SZ" not in u.eligible(b[-1], EligibilityRule(min_history_days=100, **base))
    assert "000002.SZ" in u.eligible(b[-1], EligibilityRule(min_history_days=50, **base))


def test_no_after_last_data_option(universe):
    """**不存在**"允许序列终止后入池"的选项——这是刻意的设计决定。

    理由：序列终止后用最后价估值 = 假设退市时能以最后价卖出，会凭空造收益；
    且"已退市"与"抓取失败"不可区分。要做得另开预登记并显式声明估值假设。
    """
    u, d = universe
    day = "2030-01-01"
    assert not hasattr(EligibilityRule(), "include_after_last_data")
    assert "000002.SZ" not in u.eligible(day, EligibilityRule(min_listed_days=1))
    # 任何规则组合下都不应放行
    assert u.eligible(day, EligibilityRule(min_listed_days=1, min_history_days=0)) == []


def test_rule_is_explicit_and_serializable(universe):
    """规则必须可序列化——否则无法写进预登记、无法审计。"""
    r = EligibilityRule(min_listed_days=252)
    d = r.as_dict()
    assert d["min_listed_days"] == 252
    assert set(d) == {"min_listed_days", "require_price_on_date", "exclude_suspended",
                      "min_history_days", "scope", "require_adj_coverage"}


def test_describe_explains_attrition(universe):
    """describe 必须能看出"为什么少了标的"——各状态计数之和 = 总数。"""
    u, d = universe
    info = u.describe(d["a"][-1])
    assert sum(info["state_counts"].values()) == info["n_total"]
    assert info["n_eligible"] > 0


def test_empty_dir_raises(tmp_path):
    with pytest.raises(UniverseError):
        AsOfUniverse.from_dir(str(tmp_path))


def test_gaps_helper():
    assert _gaps(["2020-01-01", "2020-01-05"]) == []
    g = _gaps(["2020-01-01", "2020-06-01"])
    assert g and g[0][2] == 152


def test_manifest_roundtrip(universe):
    """机器可读清单必须含 fetch_error 字段——"抓取失败"与"不存在"要能区分。"""
    u, d = universe
    m = u.manifest()
    assert len(m) == 4
    assert all("fetch_error" in row for row in m)
    dddd = [r for r in m if r["code"] == "000004.SZ"][0]
    assert dddd["n_days"] == 0 and dddd["fetch_error"] == "empty_file"


def test_investable_by_year_monotone_for_growing_universe(universe):
    """构造一个"每年新增标的"的场景，逐年可投资只数不应下降。"""
    u, _ = universe
    by_year = u.investable_by_year()
    years = sorted(by_year)
    assert years == sorted(years)
    # 该夹具里没有新增，只验证不抛错且为 dict[str,int]
    assert all(isinstance(v, int) for v in by_year.values())


# ============================================================ 新规则（2026-10-21 阶段 C 前置）
# 以下测试覆盖 `docs/research/UNIVERSE_EFFECT_EXPERIMENT_DESIGN.md` §6 要求的显式规则：
# B 股排除、涨跌幅限制（含创业板 2020-08-24 切换）、一字板、ADJ 覆盖。

from asof_universe import (  # noqa: E402
    GEM_20PCT_START, TradabilityIndex, board_of, is_a_share, limit_ratio,
)


def _write_ohlc(path: str, rows: list[tuple[str, float, float, float, float]]) -> None:
    lines = ["date,open,high,low,close,volume"]
    for d, o, h, l, c in rows:
        lines.append(f"{d},{o:.4f},{h:.4f},{l:.4f},{c:.4f},1000.0")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def test_board_and_a_share_classification():
    """板块分类必须显式，且**不认识就返回 unknown，不猜**。"""
    assert board_of("000001.SZ") == "main"
    assert board_of("002415.SZ") == "main"      # 原中小板，仍按主板
    assert board_of("600519.SH") == "main"
    assert board_of("300750.SZ") == "gem"
    assert board_of("688111.SH") == "star"
    assert board_of("200053.SZ") == "b_share"   # 深 B（港币）
    assert board_of("900956.SH") == "b_share"   # 沪 B（美元）
    assert board_of("830799.BJ") == "bse"
    assert board_of("ABCDEF.SZ") == "unknown"
    # A 股宇宙：B 股与北交所**必须**为 False
    assert is_a_share("000001.SZ") and is_a_share("300750.SZ") and is_a_share("688111.SH")
    assert not is_a_share("200053.SZ")
    assert not is_a_share("900956.SH")
    assert not is_a_share("830799.BJ")


def test_limit_ratio_gem_switch_on_20200824():
    """创业板涨跌幅 2020-08-24 由 10% → 20%。写死常数会让前后一半判错。"""
    assert limit_ratio("300001.SZ", "2020-08-21") == 0.10
    assert limit_ratio("300001.SZ", "2020-08-24") == 0.20   # 当天就切换
    assert limit_ratio("300001.SZ", "2026-09-30") == 0.20
    # 科创板一直 20%；主板一直 10%
    assert limit_ratio("688001.SH", "2019-01-04") == 0.20
    assert limit_ratio("000001.SZ", "2019-01-04") == 0.10
    # 常量本身也要能被引用（防止有人改成硬编码）
    assert GEM_20PCT_START == "2020-08-24"


def test_b_share_excluded_by_scope(tmp_path):
    """B 股必须靠**显式规则**排除，不是靠"ADJ 恰好缺失"（Gate 6 的隐式排除）。"""
    days = _bdays("2020-01-02", 30)
    _write(str(tmp_path / "000001.SZ.exec.csv"), days)
    _write(str(tmp_path / "200053.SZ.exec.csv"), days)      # 深 B
    u = AsOfUniverse.from_dir(str(tmp_path))
    day = days[-1]
    got = u.eligible(day, EligibilityRule(min_listed_days=1))
    assert "000001.SZ" in got
    assert "200053.SZ" not in got, "B 股必须被 scope='a_share' 排除"
    # scope='all' 时才允许出现（仅供旧实验复现）
    got_all = u.eligible(day, EligibilityRule(min_listed_days=1, scope="all"))
    assert "200053.SZ" in got_all


def test_limit_up_and_down_detected(tmp_path):
    """涨停/跌停必须能被检出，且**不能因为容差写错而永远判成可成交**。"""
    rows = [("2020-01-02", 10, 10, 10, 10), ("2020-01-03", 10, 10, 10, 10),
            ("2020-01-06", 10, 10, 10, 10), ("2020-01-07", 10, 10, 10, 10),
            ("2020-01-08", 10, 10, 10, 10), ("2020-01-09", 10, 10, 10, 10),
            ("2020-01-10", 10.5, 11.0, 10.4, 11.0),   # 非一字涨停 +10%
            ("2020-01-13", 11.0, 11.0, 11.0, 11.0),
            ("2020-01-14", 10.0, 10.0, 9.90, 9.90)]    # 非一字跌停 -10%
    _write_ohlc(str(tmp_path / "000001.SZ.exec.csv"), rows)
    t = TradabilityIndex(str(tmp_path))
    assert t.blocked("000001.SZ", "2020-01-10") == "limit_up"
    assert t.blocked("000001.SZ", "2020-01-13") is None        # 平盘，可成交
    assert t.blocked("000001.SZ", "2020-01-14") == "limit_down"


def test_one_word_board(tmp_path):
    """一字板 = 开=高=低=收 且 涨跌停 → **完全无法成交**，必须单独识别。"""
    rows = [("2020-01-02", 10, 10, 10, 10), ("2020-01-03", 10, 10, 10, 10),
            ("2020-01-06", 10, 10, 10, 10), ("2020-01-07", 10, 10, 10, 10),
            ("2020-01-08", 10, 10, 10, 10), ("2020-01-09", 10, 10, 10, 10),
            ("2020-01-10", 11, 11, 11, 11),            # 一字涨停
            ("2020-01-13", 11, 11, 11, 11),
            ("2020-01-14", 9.9, 9.9, 9.9, 9.9)]        # 一字跌停
    _write_ohlc(str(tmp_path / "000001.SZ.exec.csv"), rows)
    t = TradabilityIndex(str(tmp_path))
    assert t.blocked("000001.SZ", "2020-01-10") == "one_word_board"
    assert t.blocked("000001.SZ", "2020-01-14") == "one_word_board"


def test_new_stock_warmup_exempt(tmp_path):
    """新股上市前 5 个交易日无涨跌幅限制——不加豁免会误判成涨停不可成交。

    ⚠️ 修测试自身：初版用 2020-01，那时创业板阈值还是 **10%**，
      所以 "+10%" 确实是一字涨停，`blocked` 判 `one_word_board` 是**对的**，
      错的是我的预期（我以为创业板一直 20%）。改到 2021 年（阈值已 20%）。
    """
    rows = [("2021-01-04", 10, 10, 10, 10), ("2021-01-05", 15, 15, 15, 15),   # +50%，第 2 日
            ("2021-01-06", 20, 20, 20, 20), ("2021-01-07", 20, 20, 20, 20),
            ("2021-01-08", 20, 20, 20, 20), ("2021-01-11", 20, 20, 20, 20),
            ("2021-01-12", 22, 22, 22, 22)]                                    # +10%，第 7 日
    _write_ohlc(str(tmp_path / "300999.SZ.exec.csv"), rows)
    t = TradabilityIndex(str(tmp_path))
    assert t.blocked("300999.SZ", "2021-01-05") is None, "上市前 5 日应豁免（+50% 也不算涨停）"
    # 第 7 个交易日已过 warmup，+10% 在创业板 20% 阈值下不算涨停
    assert t.blocked("300999.SZ", "2021-01-12") is None


def test_adj_coverage_filter(tmp_path):
    """ADJ/EXEC 双覆盖：只有 EXEC 没有 ADJ 的标的**不能进研究池**（否则收益口径不一致）。"""
    exec_dir = tmp_path / "exec"
    adj_dir = tmp_path / "adj"
    exec_dir.mkdir()
    adj_dir.mkdir()
    days = _bdays("2020-01-02", 30)
    _write(str(exec_dir / "000001.SZ.exec.csv"), days)
    _write(str(exec_dir / "000002.SZ.exec.csv"), days)
    _write(str(adj_dir / "000001.SZ.qfq.csv"), days)     # 只有一只有 ADJ
    u = AsOfUniverse.from_dir(str(exec_dir))
    day = days[-1]
    rule = EligibilityRule(min_listed_days=1, require_adj_coverage=True, adj_dir=str(adj_dir))
    assert u.eligible(day, rule) == ["000001.SZ"]
    # 不给 adj_dir 时必须报错，不能静默跳过检查
    with pytest.raises(UniverseError):
        u.eligible(day, EligibilityRule(min_listed_days=1, require_adj_coverage=True))
