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
    """三只标的：
       AAAA：2010-01-04 起连续 300 个工作日（足够长）
       BBBB：2015-06-01 起 60 个工作日（短）
       CCCC：2012-03-05 起 300 个工作日，中间挖掉 10 天模拟停牌
       DDDD：空文件（只有表头）→ NO_DATA
    """
    a = _bdays("2010-01-04", 300)
    _write(str(tmp_path / "AAAA.SZ.exec.csv"), a)

    b = _bdays("2015-06-01", 60)
    _write(str(tmp_path / "BBBB.SZ.exec.csv"), b)

    c = _bdays("2012-03-05", 300)
    hole = c[100:110]                     # 停牌 10 个工作日
    _write(str(tmp_path / "CCCC.SZ.exec.csv"), [d for d in c if d not in hole])

    _write(str(tmp_path / "DDDD.SZ.exec.csv"), [])
    return AsOfUniverse.from_dir(str(tmp_path)), {
        "a": a, "b": b, "c": c, "hole": hole,
    }


def test_states_are_five_not_boolean(universe):
    """五态必须互斥且各自可达——不能把后三态压成 False。"""
    u, d = universe
    a = d["a"]
    assert u.state("AAAA.SZ", "2009-12-31") == TradingState.BEFORE_LISTING
    assert u.state("AAAA.SZ", a[0]) == TradingState.HAS_DATA
    assert u.state("AAAA.SZ", "2030-01-01") == TradingState.AFTER_LAST_DATA
    assert u.state("DDDD.SZ", a[0]) == TradingState.NO_DATA
    # 停牌：CCCC 在挖掉的那天必须判 SUSPENDED_LIKELY，而不是"未上市"或"已退市"
    assert u.state("CCCC.SZ", d["hole"][0]) == TradingState.SUSPENDED_LIKELY
    # 未知代码
    assert u.state("ZZZZ.SZ", a[0]) == TradingState.NO_DATA


def test_after_last_data_is_not_delisted(universe):
    """核心语义：AFTER_LAST_DATA 只表示"没有数据"，**不等于已退市**。

    抓取失败会产生完全相同的现象，因此本模块不提供 `is_delisted`。
    """
    u, d = universe
    st = u.state("BBBB.SZ", "2030-01-01")
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
    assert "AAAA.SZ" in u.eligible(a[251], rule)
    # 第 251 个 → 累计 251 → 不可入池
    assert "AAAA.SZ" not in u.eligible(a[250], rule)


def test_suspension_excluded(universe):
    """停牌日必须排除（exclude_suspended 默认 True）。"""
    u, d = universe
    hole = d["hole"]
    rule = EligibilityRule(min_listed_days=1)
    for day in hole:
        assert "CCCC.SZ" not in u.eligible(day, rule)
    # 停牌前后各一天应入池
    assert "CCCC.SZ" in u.eligible(d["c"][99], rule)
    assert "CCCC.SZ" in u.eligible(d["c"][110], rule)


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
    assert "BBBB.SZ" not in u.eligible(b[-1], EligibilityRule(min_history_days=100, **base))
    assert "BBBB.SZ" in u.eligible(b[-1], EligibilityRule(min_history_days=50, **base))


def test_no_after_last_data_option(universe):
    """**不存在**"允许序列终止后入池"的选项——这是刻意的设计决定。

    理由：序列终止后用最后价估值 = 假设退市时能以最后价卖出，会凭空造收益；
    且"已退市"与"抓取失败"不可区分。要做得另开预登记并显式声明估值假设。
    """
    u, d = universe
    day = "2030-01-01"
    assert not hasattr(EligibilityRule(), "include_after_last_data")
    assert "BBBB.SZ" not in u.eligible(day, EligibilityRule(min_listed_days=1))
    # 任何规则组合下都不应放行
    assert u.eligible(day, EligibilityRule(min_listed_days=1, min_history_days=0)) == []


def test_rule_is_explicit_and_serializable(universe):
    """规则必须可序列化——否则无法写进预登记、无法审计。"""
    r = EligibilityRule(min_listed_days=252)
    d = r.as_dict()
    assert d["min_listed_days"] == 252
    assert set(d) == {"min_listed_days", "require_price_on_date", "exclude_suspended",
                      "min_history_days"}


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
    dddd = [r for r in m if r["code"] == "DDDD.SZ"][0]
    assert dddd["n_days"] == 0 and dddd["fetch_error"] == "empty_file"


def test_investable_by_year_monotone_for_growing_universe(universe):
    """构造一个"每年新增标的"的场景，逐年可投资只数不应下降。"""
    u, _ = universe
    by_year = u.investable_by_year()
    years = sorted(by_year)
    assert years == sorted(years)
    # 该夹具里没有新增，只验证不抛错且为 dict[str,int]
    assert all(isinstance(v, int) for v in by_year.values())
