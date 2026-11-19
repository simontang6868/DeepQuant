"""U16-EXEC-2 H9 闭合：protobuf Timestamp created_at 落盘解析修复。

根因：ExecRpt.created_at 是 google.protobuf.Timestamp（seconds+nanos），
paper_account_adapter._plain_value 旧版走 str(v) ⇒ "seconds: xxx\\n"，
sim_reconcile.check_exec_timestamps 用 fromisoformat 解析 ⇒ 恒 UNPARSEABLE
⇒ 验收第 9 条真实报单后永远 FAIL（判据写错，非数据时点问题）。

用 stub 模拟 Timestamp（DESCRIPTOR.full_name + seconds/nanos），跨平台不依赖
本地是否安装 protobuf；生产路径另在云端 .venv-gm 用真 pb 验证。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_account_adapter as pa  # noqa: E402
import sim_reconcile as sr  # noqa: E402


class _Desc:
    full_name = "google.protobuf.Timestamp"


class _StubTS:
    """最小复刻 protobuf Timestamp 的可读接口。"""

    DESCRIPTOR = _Desc()

    def __init__(self, secs: int, nanos: int = 0):
        self.seconds = secs
        self.nanos = nanos


def _utc_secs(y, mo, d, h, mi, s) -> int:
    return int(dt.datetime(y, mo, d, h, mi, s,
                           tzinfo=dt.timezone.utc).timestamp())


WANT = "2026-12-01T09:35:00+08:00"


def test_timestamp_converts_to_iso_beijing():
    # UTC 01:35:12 = 北京 09:35:12
    secs = _utc_secs(2026, 12, 1, 1, 35, 12)
    out = pa._plain_value(_StubTS(secs))
    assert out == "2026-12-01T09:35:12+08:00", out


def test_timestamp_nanos_rounded_to_micros():
    secs = _utc_secs(2026, 12, 1, 1, 35, 12)
    out = pa._plain_value(_StubTS(secs, 500_000_000))
    assert out.startswith("2026-12-01T09:35:12.500000"), out


def test_full_chain_on_time_passes():
    """落盘 ISO → 验收第 9 条解析：准点必须 PASS（修复前恒 FAIL）。"""
    secs = _utc_secs(2026, 12, 1, 1, 35, 12)
    iso = pa._plain_value(_StubTS(secs))
    ok, problems = sr.check_exec_timestamps(
        [{"created_at": iso}], intended=WANT, window_min=10)
    assert ok and not problems, (iso, problems)


def test_full_chain_out_of_window_fails():
    """超窗仍 FAIL——判据阈值 ±10min 未被放宽。"""
    secs = _utc_secs(2026, 12, 1, 2, 0, 0)  # 北京 10:00
    iso = pa._plain_value(_StubTS(secs))
    ok, problems = sr.check_exec_timestamps(
        [{"created_at": iso}], intended=WANT, window_min=10)
    assert not ok and any("SKEW" in p for p in problems), (iso, problems)


def test_non_timestamp_unknown_fails_closed():
    """非 Timestamp 未知对象仍转 str，不伪造合法时间。"""
    class _Unknown:
        pass
    out = pa._plain_value(_Unknown())
    assert isinstance(out, str) and "Timestamp" not in out
    ok, problems = sr.check_exec_timestamps(
        [{"created_at": out}], intended=WANT, window_min=10)
    assert not ok and any("UNPARSEABLE" in p for p in problems)


def test_legacy_iso_string_still_parses():
    """旧版已落盘的 ISO 字符串仍可解析（向后兼容，不动判据）。"""
    ok, problems = sr.check_exec_timestamps(
        [{"created_at": "2026-12-01T09:35:05+08:00"}],
        intended=WANT, window_min=10)
    assert ok and not problems
