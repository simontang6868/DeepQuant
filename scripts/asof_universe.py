#!/usr/bin/env python
"""as-of 历史可投资股票池接口（阶段 B4）。

设计原则（来自外部审阅意见）
--------------------------
1. **不用一个空值表达多种含义**。`not in universe` 至少有五种截然不同的原因：
   还没上市、已退市、当日停牌、抓取失败、从未有过数据。混成一个 False，
   就会让"数据缺失"伪装成"当时不存在"——这正是 D-1 事故（东财空表被当成"无公司行为"）的同构错误。
   因此本模块用 `TradingState` 五态枚举。

2. **"确认不存在"与"未知"必须分开**。退市只能从"序列末日早于数据终点"推断，
   而抓取失败会产生完全相同的现象。所以 `AFTER_LAST_DATA` 只表示"没有数据"，
   不表示"已退市"；要判退市必须另有独立来源（交易所公告 / 退市名单）。
   本模块因此提供 `delist_evidence` 字段，只记录**可核实的证据**，不臆断。

3. **规则要能被测试**。所有判定都走 `TradingState` 与 `EligibilityRule`，
   单元测试覆盖边界（上市首日、退市前一日、停牌、窗口首尾），见 `scripts/tests/test_asof_universe.py`。

用法
----
    from asof_universe import AsOfUniverse

    u = AsOfUniverse.from_dir("data/exec_all")
    codes = u.eligible("2015-06-30", min_listed_days=252)
    u.state("600005.SH", "2015-06-30")      # -> TradingState.HAS_DATA
    u.describe("2015-06-30")                # -> 统计字典
"""
from __future__ import annotations

import bisect
import glob
import json
import os
from dataclasses import dataclass, field
from enum import Enum


class TradingState(str, Enum):
    """一个标的在某一天的交易状态。

    ⚠️ 五态而不是布尔：任何把后三态合并成 False 的用法都会重演 D-1 类错误。
    """
    HAS_DATA = "HAS_DATA"                 # 当日有行情
    SUSPENDED_LIKELY = "SUSPENDED_LIKELY"  # 上市区间内但当日无行情 → 疑似停牌（不可交易）
    BEFORE_LISTING = "BEFORE_LISTING"      # 首个行情日之前 → 未上市（确认不存在）
    AFTER_LAST_DATA = "AFTER_LAST_DATA"    # 末个行情日之后 → 退市或数据终止（**不可区分**）
    NO_DATA = "NO_DATA"                   # 全区间无任何行情


class UniverseError(ValueError):
    pass


@dataclass(frozen=True)
class EligibilityRule:
    """入池规则的显式表达。任何一项都不能隐含在代码分支里。"""
    min_listed_days: int = 252        # 上市满 N 个有效交易日
    require_price_on_date: bool = True  # 当日必须有行情（=可定价）
    exclude_suspended: bool = True      # 排除停牌（当日无行情即视为不可交易）
    min_history_days: int = 0         # 最低历史长度（0=不限）

    # ⚠️ 刻意**不提供** include_after_last_data 选项（原设计有，已删除）。
    #    原因：允许"序列已终止"的标的入池，当日无价就只能用最后价估值，
    #    等于假设"退市时能以最后价卖出"——这会凭空造出退市股的收益，
    #    是幸存者偏差的镜像陷阱。而且"已退市"与"抓取失败"本来不可区分。
    #    如确需做这类敏感性分析，必须另开预登记并显式声明估值假设。

    def as_dict(self) -> dict:
        return {
            "min_listed_days": self.min_listed_days,
            "require_price_on_date": self.require_price_on_date,
            "exclude_suspended": self.exclude_suspended,
            "min_history_days": self.min_history_days,
        }


@dataclass
class SymbolSpan:
    """一只标的的行情跨度（只存必要信息，便于审计与内存控制）。"""
    code: str
    first: str
    last: str
    n_days: int
    dates: list[str] = field(repr=False, default_factory=list)
    source: str = "exec"      # exec = 真不复权；来源标记用于口径核对
    fetch_error: str | None = None   # 抓取失败原因（**与"不存在"严格区分**）

    def has_date(self, day: str) -> bool:
        i = bisect.bisect_left(self.dates, day)
        return i < len(self.dates) and self.dates[i] == day

    def n_before_or_on(self, day: str) -> int:
        return bisect.bisect_right(self.dates, day)


class AsOfUniverse:
    """某一天的候选股票集合。**只允许用当日及之前的信息**。"""

    def __init__(self, spans: dict[str, SymbolSpan], calendar: list[str] | None = None):
        self.spans = spans
        self._calendar = calendar
        self.meta: dict = {}

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_dir(cls, exec_dir: str, end_hint: str | None = None,
                 fetch_report: str | None = None) -> AsOfUniverse:
        """从 EXEC 目录（真不复权）构建。**不接受 ADJ 目录**——交易状态必须用真实成交数据判。"""
        spans: dict[str, SymbolSpan] = {}
        for p in sorted(glob.glob(os.path.join(exec_dir, "*.exec.csv"))):
            code = os.path.basename(p).replace(".exec.csv", "")
            dates = _read_dates(p)
            if not dates:
                spans[code] = SymbolSpan(code=code, first="", last="", n_days=0, dates=[],
                                         fetch_error="empty_file")
                continue
            spans[code] = SymbolSpan(code=code, first=dates[0], last=dates[-1],
                                     n_days=len(dates), dates=dates)
        if not spans:
            raise UniverseError(f"{exec_dir} 下没有任何 *.exec.csv")
        u = cls(spans)
        u.meta = {
            "source_dir": exec_dir,
            "n_symbols": len(spans),
            "end_hint": end_hint,
            "n_empty": sum(1 for s in spans.values() if s.n_days == 0),
        }
        # 抓取失败清单：从抓取报告里读，**这是"未知"的唯一来源**
        if fetch_report and os.path.exists(fetch_report):
            try:
                rep = json.load(open(fetch_report, encoding="utf-8"))
                failed = {r["code"]: str(r.get("error") or r.get("flags"))
                          for r in (rep.get("results") or rep.get("flagged") or [])
                          if not r.get("ok", True)}
                for code, err in failed.items():
                    if code in spans and spans[code].n_days == 0:
                        spans[code].fetch_error = err
                u.meta["n_fetch_failed"] = len(failed)
            except Exception as exc:  # noqa: BLE001
                u.meta["fetch_report_error"] = f"{type(exc).__name__}: {exc}"
        return u

    # ------------------------------------------------------------ 查询
    @property
    def codes(self) -> list[str]:
        return sorted(self.spans)

    def calendar(self) -> list[str]:
        """全市场交易日历 = 各标的日期的并集（升序）。缓存在实例上。"""
        if self._calendar is None:
            s: set[str] = set()
            for sp in self.spans.values():
                s.update(sp.dates)
            self._calendar = sorted(s)
        return self._calendar

    def state(self, code: str, day: str) -> TradingState:
        sp = self.spans.get(code)
        if sp is None or sp.n_days == 0:
            return TradingState.NO_DATA
        if day < sp.first:
            return TradingState.BEFORE_LISTING
        if day > sp.last:
            return TradingState.AFTER_LAST_DATA
        return TradingState.HAS_DATA if sp.has_date(day) else TradingState.SUSPENDED_LIKELY

    def eligible(self, day: str, rule: EligibilityRule | None = None) -> list[str]:
        """返回当日**可投资**的标的。规则全部显式，不做任何隐式放宽。"""
        rule = rule or EligibilityRule()
        out: list[str] = []
        for code, sp in self.spans.items():
            if sp.n_days == 0:
                continue
            if sp.n_days < rule.min_history_days:
                continue
            if day < sp.first:
                continue
            if day > sp.last:          # 序列已终止：一律不入池（见 EligibilityRule 注释）
                continue
            if rule.require_price_on_date and not sp.has_date(day):
                continue
            if rule.exclude_suspended and not sp.has_date(day):
                continue
            if sp.n_before_or_on(day) < rule.min_listed_days:
                continue
            out.append(code)
        return sorted(out)

    def describe(self, day: str, rule: EligibilityRule | None = None) -> dict:
        """当日池的诊断：**必须能看出为什么少了标的**。"""
        rule = rule or EligibilityRule()
        cnt = {s.value: 0 for s in TradingState}
        for code in self.spans:
            cnt[self.state(code, day).value] += 1
        elig = self.eligible(day, rule)
        return {
            "date": day,
            "state_counts": cnt,
            "n_eligible": len(elig),
            "n_total": len(self.spans),
            "rule": rule.as_dict(),
        }

    # ------------------------------------------------------------ 审计
    def audit_listing_consistency(self, tolerance_days: int = 10) -> dict:
        """上市/退市信息的自洽性审计（阶段 B3）。

        检查三类可疑：
          (a) 首个行情日 **晚于** 申报上市日（若提供 meta）——数据缺失
          (b) 末个行情日 **早于** 数据终点很多——疑似退市或抓取截断（**两者不可区分**）
          (c) 序列内有超过 N 个自然日的空洞——长期停牌或抓取漏段
        """
        end = max((sp.last for sp in self.spans.values() if sp.last), default="")
        late_start, early_end, long_gap = [], [], []
        for code, sp in self.spans.items():
            if sp.n_days == 0:
                continue
            if sp.last < _shift(end, -30):
                early_end.append({"code": code, "last": sp.last})
            gaps = _gaps(sp.dates)
            if gaps and gaps[0][2] > 120:
                long_gap.append({"code": code, "from": gaps[0][0], "to": gaps[0][1],
                                 "days": gaps[0][2]})
        return {
            "end_of_data": end,
            "n_symbols": len(self.spans),
            "n_ended_early": len(early_end),
            "n_long_gap": len(long_gap),
            "ended_early_sample": early_end[:10],
            "long_gap_sample": long_gap[:10],
            "note": ("ended_early 含两类不可区分的情况：真实退市 与 抓取截断。"
                     "要判定必须另有独立退市名单。本模块不臆断。"),
        }

    def manifest(self) -> list[dict]:
        """机器可读清单（供版本冻结）。"""
        return [{"code": sp.code, "first": sp.first, "last": sp.last,
                 "n_days": sp.n_days, "fetch_error": sp.fetch_error}
                for sp in (self.spans[c] for c in self.codes)]

    def investable_by_year(self) -> dict[str, int]:
        """每年最后一个交易日的可投资只数（描述性，用默认规则）。"""
        out: dict[str, int] = {}
        cal = self.calendar()
        by_year: dict[str, str] = {}
        for d in cal:
            by_year[d[:4]] = d
        for y, d in sorted(by_year.items()):
            out[y] = len(self.eligible(d))
        return out


# ---------------------------------------------------------------- 内部工具
def _read_dates(path: str) -> list[str]:
    """只读 date 列。数据量大（5,472 文件），必须轻量。"""
    import csv
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if not header:
            return []
        try:
            di = [h.strip().lower() for h in header].index("date")
        except ValueError:
            di = 0
        for row in rd:
            if len(row) > di and row[di]:
                out.append(row[di][:10])
    out.sort()
    return out


def _shift(day: str, delta: int) -> str:
    import datetime as dt
    d = dt.date.fromisoformat(day) + dt.timedelta(days=delta)
    return d.isoformat()


def _gaps(dates: list[str]) -> list[tuple[str, str, int]]:
    """返回相邻日期间隔 > 30 自然日的空洞，按间隔降序。"""
    import datetime as dt
    if len(dates) < 2:
        return []
    out = []
    for a, b in zip(dates, dates[1:]):
        try:
            da = dt.date.fromisoformat(a)
            db = dt.date.fromisoformat(b)
        except ValueError:
            continue
        gap = (db - da).days
        if gap > 30:
            out.append((a, b, gap))
    out.sort(key=lambda x: -x[2])
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="as-of 股票池接口自检")
    ap.add_argument("--exec-dir", default="data/exec_all")
    ap.add_argument("--date", default="2026-09-30")
    ap.add_argument("--report", default="reports/ASOF_UNIVERSE_SELFCHECK.json")
    args = ap.parse_args(argv)

    u = AsOfUniverse.from_dir(args.exec_dir)
    print(f"标的 {len(u.codes)} 只 | 交易日历 {len(u.calendar())} 天")
    print(json.dumps(u.describe(args.date), ensure_ascii=False, indent=1))
    print("逐年可投资只数：", json.dumps(u.investable_by_year(), ensure_ascii=False))
    aud = u.audit_listing_consistency()
    print("上市/退市自洽性：", json.dumps({k: v for k, v in aud.items()
                                            if not isinstance(v, list)}, ensure_ascii=False))
    if os.path.dirname(args.report):
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump({"meta": u.meta, "describe": u.describe(args.date),
               "investable_by_year": u.investable_by_year(), "audit": aud},
              open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
