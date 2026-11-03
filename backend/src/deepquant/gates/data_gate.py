"""数据门禁：L1a 里唯一的"一票否决"项之一。

计划的否决项写的是「数据不达标（OHLC 完整性/跨源一致性未过）」。这两项在本仓库
都有可算的定义，因此不许用"看起来没问题"糊过去：

- **OHLC 完整性**：复用既有的 ``validate_daily``（ERROR 级即不通过），另加
  ``high ≥ max(open,close)``、``low ≤ min(open,close)`` 这类结构关系与重复日期检查；
- **跨源一致性**：研究层（复权）与交易层（真不复权）对同一标的的**日收益**应当
  在非除权日一致。除权日两边必然不同，所以判定写成"不一致天数占比 ≤ 5%"，
  而不是"逐日相等"——后者会让任何一次分红都把数据判死。

未提供交易层数据时，跨源这一项记为 ``undetermined``（带理由），闸门整体降级为
``INCONCLUSIVE`` 而不是默认通过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from deepquant.data.validation.checks import ERROR, validate_daily
from deepquant.gates.spec import DataSource, Window

#: 样本内至少需要的交易日数量（约一年）。低于此值任何收益/回撤指标都不可信。
MIN_IN_SAMPLE_ROWS = 250

#: 跨源判定阈值：日期覆盖率下限、不一致（除权）天数占比上限。
MIN_DATE_COVERAGE = 0.99
MAX_MISMATCH_SHARE = 0.05


@dataclass(frozen=True)
class Check:
    code: str
    title: str
    passed: bool | None
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "passed": self.passed,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class DataVerdict:
    checks: list[Check] = field(default_factory=list)
    frame: pd.DataFrame | None = None
    rows_total: int = 0
    rows_in_sample: int = 0
    period: dict[str, str | None] = field(default_factory=dict)

    @property
    def blocking_failures(self) -> list[Check]:
        return [check for check in self.checks if check.blocking and check.passed is False]

    @property
    def undetermined(self) -> list[Check]:
        return [check for check in self.checks if check.passed is None]

    @property
    def ok(self) -> bool:
        return not self.blocking_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rows_total": self.rows_total,
            "rows_in_sample": self.rows_in_sample,
            "period": self.period,
            "checks": [check.to_dict() for check in self.checks],
            "blocking_failures": [check.code for check in self.blocking_failures],
            "undetermined": [check.code for check in self.undetermined],
        }


def load_frame(path: str | Path) -> pd.DataFrame:
    """读日线 CSV，统一列名与排序。文件缺失抛 ``FileNotFoundError``（由闸门转否决）。"""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"数据文件不存在：{target}")
    frame = pd.read_csv(target)
    if "date" not in frame.columns:
        raise ValueError(f"数据文件缺少 date 列：{target}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    return frame.sort_values("date").reset_index(drop=True)


def _ohlc_relation_check(frame: pd.DataFrame) -> Check:
    required = {"open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        return Check("ohlc_relations", "OHLC 结构关系", False, f"缺少列：{sorted(missing)}")
    high_ok = (frame["high"] >= frame[["open", "close"]].max(axis=1) - 1e-9)
    low_ok = (frame["low"] <= frame[["open", "close"]].min(axis=1) + 1e-9)
    bad = int((~(high_ok & low_ok)).sum())
    return Check(
        "ohlc_relations",
        "OHLC 结构关系（high ≥ max(open,close)，low ≤ min(open,close)）",
        bad == 0,
        f"违例行数 {bad} / {len(frame)}",
    )


def _positive_price_check(frame: pd.DataFrame) -> Check:
    prices = frame[["open", "high", "low", "close"]]
    bad = int((prices <= 0).any(axis=1).sum())
    return Check("positive_prices", "价格全为正数", bad == 0, f"非正价格行数 {bad}")


def _volume_check(frame: pd.DataFrame) -> Check:
    if "volume" not in frame.columns:
        return Check("volume_present", "成交量字段存在且为正", False, "缺少 volume 列")
    bad = int((frame["volume"] <= 0).sum())
    # 停牌日成交量为 0 是真实情况，不构成数据错误；此处只登记比例，不参与否决。
    return Check(
        "volume_present",
        "成交量字段存在（0 量视为停牌，不否决）",
        True,
        f"0 成交量行数 {bad} / {len(frame)}",
        blocking=False,
    )


def _duplicate_check(frame: pd.DataFrame) -> Check:
    dup = int(frame["date"].duplicated().sum())
    return Check("duplicate_dates", "日期不重复", dup == 0, f"重复日期行数 {dup}")


def _coverage_check(frame: pd.DataFrame, window: Window) -> tuple[Check, int]:
    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end)
    if frame["date"].isna().all():
        return Check("in_sample_coverage", "样本内交易日数量", False, "日期列全部无法解析"), 0
    in_window = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    rows = int(len(in_window))
    return (
        Check(
            "in_sample_coverage",
            "样本内交易日数量",
            rows >= MIN_IN_SAMPLE_ROWS,
            f"样本内 {rows} 行（门槛 {MIN_IN_SAMPLE_ROWS}）",
        ),
        rows,
    )


def _cross_source_check(research: pd.DataFrame, trading: pd.DataFrame | None) -> Check:
    """研究层 vs 交易层：日期覆盖 + 非除权日日收益一致率。"""
    if trading is None:
        return Check(
            "cross_source",
            "跨源一致性（研究层 vs 交易层）",
            None,
            "未提供交易层数据源：本项无法判断（不等于一致）",
        )
    left = research[["date", "close"]].rename(columns={"close": "research_close"})
    right = trading[["date", "close"]].rename(columns={"close": "trading_close"})
    merged = left.merge(right, on="date", how="inner").dropna()
    if len(merged) < 2:
        return Check("cross_source", "跨源一致性", False, f"两源重叠交易日仅 {len(merged)} 天")
    coverage = len(merged) / max(1, len(research))
    research_ret = merged["research_close"].pct_change()
    trading_ret = merged["trading_close"].pct_change()
    diff = (research_ret - trading_ret).abs()
    mismatch = int((diff > 1e-6).sum())
    share = mismatch / max(1, len(merged) - 1)
    ok = coverage >= MIN_DATE_COVERAGE and share <= MAX_MISMATCH_SHARE
    return Check(
        "cross_source",
        "跨源一致性（日期覆盖 + 非除权日收益一致）",
        bool(ok),
        (
            f"日期覆盖 {coverage:.4f}（门槛 ≥{MIN_DATE_COVERAGE}）；"
            f"收益不一致 {mismatch} 天，占比 {share:.4f}（上限 {MAX_MISMATCH_SHARE}）"
        ),
    )


def check_frame(
    frame: pd.DataFrame,
    *,
    window: Window,
    trading: pd.DataFrame | None = None,
    label: str = "内存数据",
) -> DataVerdict:
    """对已在内存里的行情做门禁（不读文件）。

    测试与「数据已在内存」的调用方走这条路径；文件路径额外的可读性检查由
    :func:`check_data` 负责。两条路径共用同一套列级检查——不允许"内存数据少查几项"。

    入参若以 ``DatetimeIndex`` 承载日期（``normalize_daily_data`` 的产物、
     Notebook 与测试里的常见形态）而**没有** ``date`` 列，这里会先把索引落成列。
    不这么做的话下面每一项列级检查都会 ``KeyError``——调用方拿到的是异常而不是否决项，
    而"门禁抛异常"与"门禁发现数据有问题"是两件完全不同的事。
    """
    if "date" not in frame.columns:
        if isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index()
            frame = frame.rename(columns={frame.columns[0]: "date"})
        else:
            verdict = DataVerdict(frame=frame, rows_total=int(len(frame)))
            verdict.checks.append(
                Check("date_column", "日期字段存在", False, "既没有 date 列，索引也不是 DatetimeIndex")
            )
            return verdict
    verdict = DataVerdict(frame=frame, rows_total=int(len(frame)))
    verdict.period = {
        "start": str(frame["date"].min().date()) if len(frame) and "date" in frame else None,
        "end": str(frame["date"].max().date()) if len(frame) and "date" in frame else None,
    }
    report = validate_daily(frame)
    errors = [issue for issue in report.issues if issue.level == ERROR]
    verdict.checks.append(
        Check(
            "pipeline_validation",
            "既有导入管线校验（ERROR 级）",
            not errors,
            "; ".join(f"{issue.code}({issue.count})" for issue in errors) or "无 ERROR 级问题",
        )
    )
    if {"open", "high", "low", "close"} <= set(frame.columns):
        verdict.checks.append(_ohlc_relation_check(frame))
        verdict.checks.append(_positive_price_check(frame))
    else:
        verdict.checks.append(
            Check("ohlc_relations", "OHLC 结构关系", False, f"缺少列：{sorted({'open','high','low','close'} - set(frame.columns))}")
        )
    verdict.checks.append(_volume_check(frame))
    verdict.checks.append(_duplicate_check(frame))
    coverage_check, rows_in_sample = _coverage_check(frame, window)
    verdict.checks.append(coverage_check)
    verdict.rows_in_sample = rows_in_sample
    verdict.checks.append(_cross_source_check(frame, trading))
    if label:
        verdict.checks.insert(0, Check("research_file", "研究层数据可读", True, label, blocking=False))
    return verdict


def check_data(spec_data: DataSource, window: Window) -> DataVerdict:
    """执行数据门禁（从契约里的路径加载）。所有异常收敛为否决项，不向上抛。"""
    try:
        research = load_frame(spec_data.research_path)
    except (FileNotFoundError, ValueError) as exc:
        verdict = DataVerdict()
        verdict.checks.append(Check("research_file", "研究层数据可读", False, str(exc)))
        return verdict

    trading: pd.DataFrame | None = None
    trading_error: str | None = None
    if spec_data.trading_path:
        try:
            trading = load_frame(spec_data.trading_path)
        except (FileNotFoundError, ValueError) as exc:
            trading_error = str(exc)

    verdict = check_frame(
        research,
        window=window,
        trading=trading,
        label=f"{spec_data.research_path}（{len(research)} 行）",
    )
    if trading_error is not None:
        verdict.checks = [check for check in verdict.checks if check.code != "cross_source"]
        verdict.checks.append(Check("trading_file", "交易层数据可读", False, trading_error))
        verdict.checks.append(
            Check("cross_source", "跨源一致性（研究层 vs 交易层）", False, "交易层读取失败，无法比对")
        )
    elif spec_data.trading_path:
        trading_rows = 0 if trading is None else len(trading)
        verdict.checks.append(
            Check("trading_file", "交易层数据可读", True, f"{spec_data.trading_path}（{trading_rows} 行）", blocking=False)
        )
    return verdict
