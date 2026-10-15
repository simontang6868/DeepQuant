"""行情数据校验。

本模块只做**检测**，不修改数据。清洗动作由 data.pipeline 按文档化规则执行。

严重级别约定：
- ERROR：数据不可用，或无法安全自动修复（缺少字段、空表、日期无法解析、
  OHLC 关系矛盾、价格非正/非有限、成交量为负）。遇到即失败，等待人工处理。
- WARNING：可被确定性清洗的问题（重复日期、行情数值缺失、日期未排序）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deepquant.data.loaders.csv_loader import (
    NUMERIC_COLUMNS,
    PRICE_COLUMNS,
    REQUIRED_COLUMNS,
)

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    message: str
    count: int = 0


@dataclass
class ValidationReport:
    """一次校验的结果：行数与问题清单。"""

    n_rows: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """不存在 ERROR 级问题即视为通过（WARNING 可被清洗）。"""
        return not any(i.level == ERROR for i in self.issues)

    def get(self, code: str) -> ValidationIssue | None:
        """按问题代码取回该问题（不存在则返回 None）。"""
        for issue in self.issues:
            if issue.code == code:
                return issue
        return None

    def has_code(self, code: str) -> bool:
        return self.get(code) is not None

    def error_codes(self) -> list[str]:
        return [i.code for i in self.issues if i.level == ERROR]


def validate_daily(df: pd.DataFrame) -> ValidationReport:
    """校验单标的日线行情，返回 ValidationReport。"""
    report = ValidationReport(n_rows=int(len(df)))

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        report.issues.append(
            ValidationIssue(ERROR, "missing_columns", f"缺少必需字段: {missing_cols}")
        )
        return report

    if len(df) == 0:
        report.issues.append(ValidationIssue(ERROR, "empty", "数据为空（0 行）"))
        return report

    # --- 日期相关 ---
    nat_count = int(df["date"].isna().sum())
    if nat_count:
        report.issues.append(
            ValidationIssue(ERROR, "unparsable_date", "存在无法解析的日期", nat_count)
        )

    valid_dates = df["date"].dropna()
    if len(valid_dates) > 1 and not valid_dates.is_monotonic_increasing:
        report.issues.append(
            ValidationIssue(WARNING, "unsorted_dates", "日期未按升序排列")
        )

    dup_count = int(valid_dates.duplicated().sum())
    if dup_count:
        report.issues.append(
            ValidationIssue(WARNING, "duplicate_dates", "存在重复日期", dup_count)
        )

    # --- 数值缺失 ---
    na_count = int(df[list(NUMERIC_COLUMNS)].isna().any(axis=1).sum())
    if na_count:
        report.issues.append(
            ValidationIssue(WARNING, "missing_values", "存在缺失或无法解析的行情数值", na_count)
        )

    # --- 异常价格（仅在 open/high/low/close 都存在的行上判定）---
    price_df = df[list(PRICE_COLUMNS)].astype("float64")
    rows_complete = price_df.notna().all(axis=1)
    finite = np.isfinite(price_df.to_numpy(dtype="float64")).all(axis=1)
    nonpositive = (price_df <= 0).any(axis=1)

    invalid_price = rows_complete & (nonpositive | ~finite)
    invalid_price_count = int(invalid_price.sum())
    if invalid_price_count:
        report.issues.append(
            ValidationIssue(ERROR, "invalid_price", "价格非正或非有限", invalid_price_count)
        )

    # --- OHLC 关系矛盾 ---
    expected_high = price_df[["open", "close"]].max(axis=1)
    expected_low = price_df[["open", "close"]].min(axis=1)
    relation = rows_complete & (
        (price_df["high"] < price_df["low"])
        | (price_df["high"] < expected_high)
        | (price_df["low"] > expected_low)
    )
    relation_count = int(relation.sum())
    if relation_count:
        report.issues.append(
            ValidationIssue(ERROR, "ohlc_relation", "OHLC 关系矛盾（high/low 与 open/close 不符）", relation_count)
        )

    # --- 成交量为负 ---
    volume = df["volume"].astype("float64")
    negative_volume = int((volume.notna() & (volume < 0)).sum())
    if negative_volume:
        report.issues.append(
            ValidationIssue(ERROR, "negative_volume", "存在负成交量", negative_volume)
        )

    return report
