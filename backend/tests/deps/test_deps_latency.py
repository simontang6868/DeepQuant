"""延迟档案测试：nearest-rank 分位数、按日落盘、限流、失败样本计入。

这套数字将来要直接喂给执行成本模型（计划 4.4 与闸门 L4 的判定基准），
所以口径必须钉死：p99 是「真实观测到的值」而不是插值出来的值。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquant.deps.latency import LatencyArchive, percentile


# --------------------------------------------------------------------------- #
# 分位数口径
# --------------------------------------------------------------------------- #
def test_percentile_nearest_rank() -> None:
    values = list(range(1, 11))  # 1..10
    assert percentile(values, 50) == 5
    assert percentile(values, 90) == 9
    assert percentile(values, 99) == 10
    assert percentile(values, 100) == 10


def test_percentile_small_sample_p99_is_max() -> None:
    """n < 100 时 p99 必然等于最大值：如实反映"样本不够"。"""
    assert percentile([3.0, 1.0, 2.0], 99) == 3.0


def test_percentile_empty_is_none() -> None:
    """空样本返回 None 而不是 0——0 会被读成"极快"。"""
    assert percentile([], 50) is None


@pytest.mark.parametrize("p", [0, -1, 101])
def test_percentile_out_of_range_raises(p: float) -> None:
    with pytest.raises(ValueError):
        percentile([1.0], p)


# --------------------------------------------------------------------------- #
# 采样与落盘
# --------------------------------------------------------------------------- #
def test_records_daily_file_and_reads_back(tmp_path: Path) -> None:
    archive = LatencyArchive(tmp_path, min_interval_s=0)
    assert archive.record(kind="market_data", channel="eastmoney", latency_ms=12.5, ok=True, occurred_at="2026-10-27T01:00:00+00:00")
    assert archive.record(kind="market_data", channel="eastmoney", latency_ms=30.0, ok=False, occurred_at="2026-10-27T01:00:01+00:00")

    assert archive.days() == ["20261027"]
    samples = archive.samples(day="20261027")
    assert [s["latency_ms"] for s in samples] == [12.5, 30.0]
    assert samples[1]["ok"] is False


def test_files_are_lf_only(tmp_path: Path) -> None:
    archive = LatencyArchive(tmp_path, min_interval_s=0)
    archive.record(kind="market_data", channel="eastmoney", latency_ms=1.0, ok=True, occurred_at="2026-10-27T01:00:00+00:00")
    raw = (tmp_path / "latency" / "latency-20261027.jsonl").read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_rate_limit_skips_but_force_overrides(tmp_path: Path) -> None:
    archive = LatencyArchive(tmp_path, min_interval_s=60.0)
    assert archive.record(kind="market_data", channel="eastmoney", latency_ms=1.0, ok=True) is True
    assert archive.record(kind="market_data", channel="eastmoney", latency_ms=2.0, ok=True) is False
    assert archive.record(kind="market_data", channel="eastmoney", latency_ms=3.0, ok=True, force=True) is True
    assert len(archive.samples()) == 2


def test_rate_limit_is_per_channel(tmp_path: Path) -> None:
    archive = LatencyArchive(tmp_path, min_interval_s=60.0)
    assert archive.record(kind="market_data", channel="eastmoney", latency_ms=1.0, ok=True) is True
    assert archive.record(kind="market_data", channel="tencent", latency_ms=2.0, ok=True) is True


def test_no_root_means_memory_only() -> None:
    archive = LatencyArchive(None)
    assert archive.record(kind="market_data", channel="x", latency_ms=1.0, ok=True) is True
    assert archive.samples() == []
    assert archive.days() == []


# --------------------------------------------------------------------------- #
# 统计与导出
# --------------------------------------------------------------------------- #
def test_percentiles_include_failed_by_default(tmp_path: Path) -> None:
    """超时也是这条通道真实的延迟表现，默认不剔除。"""
    archive = LatencyArchive(tmp_path, min_interval_s=0)
    for value, ok in ((10.0, True), (20.0, True), (5000.0, False)):
        archive.record(kind="market_data", channel="eastmoney", latency_ms=value, ok=ok, occurred_at="2026-10-27T01:00:00+00:00")

    include = archive.percentiles(channel="eastmoney")
    assert include["n"] == 3
    assert include["max"] == 5000.0

    ok_only = archive.percentiles(channel="eastmoney", ok_only=True)
    assert ok_only["n"] == 2


def test_percentiles_empty_channel_returns_none() -> None:
    report = LatencyArchive(None).percentiles(channel="nope")
    assert report["n"] == 0
    assert report["p50"] is None and report["p99"] is None


def test_export_table_by_day_and_channel(tmp_path: Path) -> None:
    archive = LatencyArchive(tmp_path, min_interval_s=0)
    for day, channel, value, ok in (
        ("2026-10-26T01:00:00+00:00", "eastmoney", 10.0, True),
        ("2026-10-26T01:00:01+00:00", "eastmoney", 20.0, False),
        ("2026-10-26T01:00:02+00:00", "tencent", 30.0, True),
        ("2026-10-27T01:00:00+00:00", "eastmoney", 40.0, True),
    ):
        archive.record(kind="market_data", channel=channel, latency_ms=value, ok=ok, occurred_at=day)

    exported = archive.export()
    assert exported["method"] == "nearest-rank"
    assert exported["days"] == ["20261026", "20261027"]

    day1 = exported["table"]["20261026"]
    assert set(day1) == {"market_data.eastmoney", "market_data.tencent"}
    assert day1["market_data.eastmoney"]["n"] == 2
    assert day1["market_data.eastmoney"]["n_failed"] == 1
    assert day1["market_data.eastmoney"]["p50"] == 10.0


def test_corrupt_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "latency" / "latency-20261027.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"kind": "market_data", "channel": "eastmoney", "latency_ms": 5.0, "ok": True,
                    "occurred_at": "2026-10-27T01:00:00+00:00"}) + "\n{broken json\n",
        encoding="utf-8",
        newline="\n",
    )
    archive = LatencyArchive(tmp_path, min_interval_s=0)
    samples = archive.samples(day="20261027")
    assert len(samples) == 1
