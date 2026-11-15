#!/usr/bin/env python3
"""R8 §1 / D3：``g1_progress.py`` 的**真变异测试**。

纪律（项目铁律）：
  - 每条变异必须**真改行为**：原实现拒绝/正确 ⇒ 变异体放行/错误，才叫检出。
  - 期望值来自**独立算术或手写常量**，⭐ 禁止调用被测函数推导期望值。
  - 变异期间不并发跑 pytest / 不提交；本脚本只写临时副本，**不改原源码**。
  - 每条变异都打印「原实现 → 变异体」的可核对证据。

用法： python scripts/g1_progress_mutation_test.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "g1_progress.py"
FWD_REAL = ROOT / "research" / "stock_pick_2026-10-25" / "forward"
REPO_REAL = ROOT
sys.path.insert(0, str(ROOT / "scripts"))

ORIGINAL = SRC.read_text(encoding="utf-8")

OLD_MACHINE = ["20261103", "20261104", "20261105", "20261106",
               "20261109", "20261110", "20261111"]
D3_DAYS = ["20261112", "20261113"]


def state(chain, hb, *, sr_ok=True, errors=None, old=()):
    return {
        "host": "/opt/quant",
        "chain_days": sorted(set(chain) | set(old)),
        "hb_days": sorted(set(hb) | set(old)),
        "heartbeat": {"ts": "2026-11-13T17:14:24", "ok": True,
                      "mode": "NOT_DUE", "stages": []},
        "stage_results": {"ts": "2026-11-13T17:14:24", "ok": sr_ok,
                          "stage_note": "", "stages": []},
        "errors": list(errors or []),
    }


def load(text: str, tag: str, tmp: Path):
    p = tmp / f"g1_mut_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(f"g1_mut_{tag}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.FWD = FWD_REAL
    mod.REPO = REPO_REAL
    return mod


def run_main(mod, args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(args)
    return rc, buf.getvalue()


def m1_start_moved_back(mod, tmp):
    """起点被事后改成 11-03 ⇒ 旧机 7 天进入严格口径（真改行为）。"""
    good, _ = mod.build_days(state(D3_DAYS, D3_DAYS, old=OLD_MACHINE),
                             mod.official_days(), mod.START)
    return any(d in good for d in OLD_MACHINE), f"变异后全绿日含旧机：{sorted(good)[:4]}…"


def m2_start_guard_removed(mod, tmp):
    f = tmp / "m2.json"
    f.write_text(json.dumps(state(D3_DAYS, D3_DAYS)), encoding="utf-8")
    rc, _ = run_main(mod, ["--state-file", str(f), "--today", "20261115",
                           "--start", "20261103"])
    return rc != 2, f"--start 20261103 未被拦，rc={rc}（原实现 rc=2）"


def m3_conditional_day_becomes_unconditional(mod, tmp):
    """红日仍输出 11-25 ⇒ 判定日从条件式变成硬编码（真改行为）。"""
    f = tmp / "m3.json"
    f.write_text(json.dumps(state(D3_DAYS, ["20261112"])), encoding="utf-8")
    rc, out = run_main(mod, ["--state-file", str(f), "--today", "20261115"])
    return "2026-11-25 收盘后判" in out, f"红日仍报 11-25：{'11-25' in out}（原实现不报）"


def m4_red_day_not_reset(mod, tmp):
    """跨红累加：11-09 漏跑后原实现 5 天，变异体应 >5（独立期望 5）。"""
    days = ["20261103", "20261104", "20261105", "20261106", "20261109",
            "20261110", "20261111", "20261112", "20261113", "20261116"]
    good = set(days) - {"20261109"}
    k, _, _ = mod.count_streak(days, good, "20261116", start="20261103")
    return k > 5, f"变异后连续数 {k}（原实现/独立算术 = 5）"


def m5_artifact_check_disabled(mod, tmp):
    """stage_results.ok=false 时变异体仍把该日算绿。"""
    good, _ = mod.build_days(state(D3_DAYS, D3_DAYS, sr_ok=False),
                             mod.official_days(), mod.START)
    return "20261113" in good, f"产物 ok=false 仍全绿：{'20261113' in good}（原实现判红）"


def m6_old_machine_merged(mod, tmp):
    good, _ = mod.build_days(state(D3_DAYS, D3_DAYS, old=OLD_MACHINE),
                             mod.official_days(), mod.START)
    return any(d in good for d in OLD_MACHINE), f"旧机数据被并入：{sorted(good)[:4]}…"


def m7_errors_not_failing_closed(mod, tmp):
    f = tmp / "m7.json"
    f.write_text(json.dumps(state(D3_DAYS, D3_DAYS,
                                  errors=["stage_results.json:FileNotFoundError"])),
                 encoding="utf-8")
    rc, _ = run_main(mod, ["--state-file", str(f), "--today", "20261115"])
    return rc != 2, f"产物缺口未失败关闭，rc={rc}（原实现 rc=2）"


MUTATIONS = [
    ("M1_START_MOVED_BACK", 'START = "20261112"', 'START = "20261103"', m1_start_moved_back),
    ("M2_START_GUARD_OFF", "if args.start is not None and args.start < START:",
     "if False:", m2_start_guard_removed),
    ("M3_DAY_UNCONDITIONAL", "elif broken is None and not legacy:",
     "elif not legacy:", m3_conditional_day_becomes_unconditional),
    ("M4_NO_RED_RESET", "            return len(streak), streak, d",
     "            continue", m4_red_day_not_reset),
    ("M5_ARTIFACT_OFF", "        good.discard(d_hb)\n        good.discard(d_sr)",
     "        pass\n        pass", m5_artifact_check_disabled),
    ("M6_MERGE_OLD", "d >= start and d in chain and d in hb",
     "d in chain and d in hb", m6_old_machine_merged),
    ("M7_NO_FAIL_CLOSED", '    if state.get("errors"):', "    if False:",
     m7_errors_not_failing_closed),
]


def main() -> int:
    import g1_progress as baseline
    baseline.FWD = FWD_REAL
    # 基线自检：原实现必须全部「不检出」（即保持正确行为）
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = load(ORIGINAL, "baseline", tmp)
        for name, _old, _new, check in MUTATIONS:
            ok, _ = check(base, tmp)
            if ok:
                print(f"[MUT] FAIL 基线 {name} 就被判为变异体 ⇒ 检查器写错了")
                return 1
    print(f"[MUT] 基线自检 OK：{len(MUTATIONS)} 条变异在原实现上均不触发\n")

    detected = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, (name, old, new, check) in enumerate(MUTATIONS, 1):
            cnt = ORIGINAL.count(old)
            if cnt != 1:
                print(f"[MUT] FAIL {name}：锚点出现 {cnt} 次（必须恰好 1 次）")
                return 1
            variant = ORIGINAL.replace(old, new, 1)
            if variant == ORIGINAL:
                print(f"[MUT] FAIL {name}：假变异（源码没变）")
                return 1
            mod = load(variant, f"m{i}", tmp)
            ok, evidence = check(mod, tmp)
            print(f"  {name:26s} {'DETECTED' if ok else 'MISSED  '} | {evidence}")
            detected += int(ok)

    total = len(MUTATIONS)
    print(f"\n[MUT] 真变异检出 {detected}/{total}")
    if detected != total:
        print("[MUT] FAIL 有变异未被检出 ⇒ 闸门或检查器写错，不得报绿")
        return 1
    print("[MUT] PASS 全部变异真改行为且被检出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
