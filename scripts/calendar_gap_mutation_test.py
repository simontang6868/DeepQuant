"""R6-4 日历缺口预案的变异测试（只写真改行为的变异）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5" / "trade_calendar.py"
ORIGINAL = SRC.read_text(encoding="utf-8")
sys.path.insert(0, str(SRC.parent))
import trade_calendar as base  # noqa: E402

REAL = ROOT / "research" / "stock_pick_2026-10-25" / "forward" / "trade_calendar.json"
DAYS = [d for d in json.loads(REAL.read_text(encoding="utf-8"))["dates"] if d <= "20261231"]
TRUNC = [d for d in DAYS if d <= "20261127"]


def load_variant(code: str) -> dict:
    ns = {"__name__": "tcal_variant", "__file__": str(SRC)}
    exec(compile(code, str(SRC), "exec"), ns)  # noqa: S102
    return ns


def setup(mod):
    tmp = Path(tempfile.mkdtemp(prefix="dq_cal_mut_"))
    cal, gaps = tmp / "trade_calendar.json", tmp / "calendar_gaps.json"
    mod["save"](DAYS, cal)
    return tmp, cal, gaps


def boom_of(mod):
    """必须抛**被测模块自己**的异常类：exec 出来的变异模块会重新定义
    CalendarUnavailable，用原模块的类会绕过它的 except（假失败）。"""
    def boom():
        raise mod["CalendarUnavailable"]("模拟接口挂了")
    return boom


def args(cal, gaps):
    return ["--refresh", "--path", str(cal), "--gaps-path", str(gaps), "--today", "20261201"]


def probe_bad_new_calendar_does_not_overwrite(mod):
    """fetch 返回缺当月的坏日历 ⇒ 不得 save（写前校验）。"""
    _, cal, gaps = setup(mod)
    before = cal.read_bytes()
    mod["refresh"](args(cal, gaps), fetcher=lambda: TRUNC)
    return cal.read_bytes() == before


def probe_cache_bad_fails_closed(mod):
    """fetch 失败 + 缓存被截断 ⇒ rc=1。"""
    _, cal, gaps = setup(mod)
    mod["save"](TRUNC, cal)
    rc, _ = mod["refresh"](args(cal, gaps), fetcher=boom_of(mod))
    return rc == 1


def probe_fetch_failure_is_recorded(mod):
    """fetch 失败不得在缺口台账里写成 OK。"""
    _, cal, gaps = setup(mod)
    mod["refresh"](args(cal, gaps), fetcher=boom_of(mod))
    return json.loads(gaps.read_text(encoding="utf-8"))["status"] == "FETCH_FAILED_USING_CACHE"


def probe_missing_months_recorded(mod):
    """缺 2027 必须写进缺口台账，不能只打印。"""
    _, cal, gaps = setup(mod)
    mod["refresh"](args(cal, gaps), fetcher=lambda: DAYS)
    return "202701" in json.loads(gaps.read_text(encoding="utf-8"))["missing_months"]


MUTATIONS = [
    ("M1", "写前校验被摘掉 ⇒ 坏日历会落盘",
     "assert_window_covered(days, required_ym)      # 先验证，再落盘",
     "pass                                        # 先验证，再落盘",
     probe_bad_new_calendar_does_not_overwrite),

    ("M2", "缓存不覆盖当月也判成功",
     "return 1, None",
     "return 0, None",
     probe_cache_bad_fails_closed),

    ("M3", "拉取失败被写成 OK",
     'status = "FETCH_FAILED_USING_CACHE"',
     'status = "OK"',
     probe_fetch_failure_is_recorded),

    ("M4", "缺口不再写进台账",
     '"missing_months": missing_months(days, required_ym),',
     '"missing_months": [],',
     probe_missing_months_recorded),

    ("M5", "assert_window_covered 变成空函数",
     "    ensure_covered(days, through_ym)\n\n\ndef missing_months",
     "    return None\n\n\ndef missing_months",
     probe_bad_new_calendar_does_not_overwrite),
]


def main() -> int:
    detected = 0
    for mid, desc, old, new, probe in MUTATIONS:
        if old not in ORIGINAL:
            print(f"[{mid}] ✗ 变异定义失效：替换文本未命中源码")
            return 1
        variant = load_variant(ORIGINAL.replace(old, new, 1))
        base_red = probe(vars(base))
        var_red = probe(variant)
        if not base_red:
            print(f"[{mid}] ✗ 测试用例无效：原版没守住（{desc}）")
            return 1
        if var_red:
            print(f"[{mid}] ✗ 变异未被检出：{desc}")
            return 1
        detected += 1
        print(f"[{mid}] ✓ 检出：{desc}")
    print(f"[mutation] {detected}/{len(MUTATIONS)} 检出；全部变异均真改行为")
    return 0


if __name__ == "__main__":
    sys.exit(main())
