"""data/pipeline.py 契约与不变量测试（A16#8，本地不提 PR）。

补既有 test_pipeline.py 未覆盖的 2 行守卫（coverage 实测 63 stmts / 2 miss，缺口为
ingest_daily_csv 的**清洗后**二次校验分支 lines 129-130）：

既有 test_pipeline.py 的硬错误用例（high<low）命中的是**清洗前**校验（report_before 失效即中止，
lines 119-121）。而 _clean 只去重/丢缺失/排序，从不引入错误，故「清洗后数据仍校验失败」分支
在正常输入下不可达——它是模块对「绝不伪造数据」契约的**防御性兜底**。

本文件用 monkeypatch 把第二次 validate_daily（report_after）强制为失效报告，精确触发该分支，
并做语义断言：
- 抛出的必须是 DataValidationError，且消息前缀为「清洗后数据仍存在严重问题」（区别于清洗前守卫），
  且 detail 由 ERROR 级问题代码拼接（含 code 与 count）；
- **绝不写任何半成品文件**：raw/processed/metadata 下均不得出现该 symbol 的文件（原子性 /
  「被拒导入不污染数据仓库」契约）。
"""

from __future__ import annotations

import pytest

from deepquant.data.pipeline import DataValidationError, ingest_daily_csv
from deepquant.data.validation.checks import (
    ERROR,
    ValidationIssue,
    ValidationReport,
    validate_daily as real_validate_daily,
)

# 与 test_pipeline.py 一致的「有效但含可清洗告警」样本：
# 重复日期 + 数值缺失（均为 WARNING，report_before.is_valid 为 True）
MESSY_CSV = (
    "date,open,high,low,close,volume\n"
    "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    "2024-01-03,10.2,10.6,10.0,10.4,1200\n"
    "2024-01-03,10.2,10.6,10.0,10.5,1300\n"
    "2024-01-04,10.4,,10.1,10.6,1400\n"
    "2024-01-05,10.6,10.9,10.3,10.8,1500\n"
)


def _write(tmp_path, text, name="in.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _fake_validate_that_fails_after(monkeypatch, error_code="ohlc_relation", count=1):
    """第一次调用返回真实（应为有效）报告；第二次强制返回带 ERROR 的失效报告。"""
    calls = {"n": 0}

    def fake(df):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_validate_daily(df)  # report_before：真实、有效
        return ValidationReport(
            n_rows=int(len(df)),
            issues=[ValidationIssue(ERROR, error_code, "OHLC 关系矛盾", count)],
        )

    monkeypatch.setattr("deepquant.data.pipeline.validate_daily", fake)


# ---------- 1. 清洗后守卫：中止且不写任何半成品文件 ----------
def test_post_clean_validation_failure_aborts_and_writes_nothing(tmp_path, monkeypatch):
    """清洗后二次校验失效时，必须中止导入且数据仓库保持干净（不写半成品）。"""
    src = _write(tmp_path, MESSY_CSV)
    data_root = tmp_path / "data"
    symbol = "TEST"

    _fake_validate_that_fails_after(monkeypatch)

    with pytest.raises(DataValidationError):
        ingest_daily_csv(src, symbol=symbol, data_root=data_root)

    # 原子性契约：被拒导入绝不留下任何 symbol 文件
    assert not (data_root / "raw" / f"{symbol}.raw.csv").exists()
    assert not (data_root / "processed" / f"{symbol}.daily.parquet").exists()
    assert not (data_root / "metadata" / f"{symbol}.meta.json").exists()


# ---------- 2. 守卫消息：区分于清洗前守卫，且含 ERROR 代码+计数 ----------
def test_post_clean_guard_message_identifies_error_codes(tmp_path, monkeypatch):
    """异常必须是清洗后守卫抛出（消息前缀「清洗后」），且 detail 含 ERROR 代码与计数。"""
    src = _write(tmp_path, MESSY_CSV)
    data_root = tmp_path / "data"

    _fake_validate_that_fails_after(monkeypatch, error_code="negative_volume", count=2)

    with pytest.raises(DataValidationError) as exc:
        ingest_daily_csv(src, symbol="TEST", data_root=data_root)

    msg = str(exc.value)
    assert "清洗后" in msg           # 区别于清洗前守卫的「原始数据存在严重问题」
    assert "negative_volume" in msg  # detail 由 ERROR 级代码拼接
    assert "(2)" in msg             # 含 count
