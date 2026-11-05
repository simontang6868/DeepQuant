#!/usr/bin/env python
"""``execution/session.py`` 与 ``execution/order_loop.py`` 的变异测试。

覆盖验收 2.2-c（发单时点 / 重定价时点 / 顺延 10 个交易日）与
2.2-d（同一 client_order_id 重定价上限 2 次）。

## 为什么必须做

项目铁律：**任何一致性检查必须先做变异测试**——故意注入已知缺陷，确认测试真的抓得到。
本项目已经反复发生"检查器自己写错、通过率是假的"：
- 交叉验证只比交集，砍掉 200 行仍 PASS；
- `side.value=="buy"` 恒假（实际是 `"BUY"`），模块完全失效却自我报告为通过；
- R4-3 块自助法 `mdds > -0.20` 方向写反，仍报出数字，靠分位数矛盾才发现。

`test_session.py`（44 条）与 `test_order_loop.py`（29 条）都是新写的。全绿只能说明
它们与实现"一致"，不能说明它们**有牙齿**。本脚本负责证明后者。

## 十一个变异体

| # | 注入的缺陷 | 违反 | 期望 |
|---|---|---|---|
| M1 | `MAX_REPRICE` 2 → 3 | 2.2-d | 变红 |
| M2 | `MAX_CARRY_DAYS` 10 → 11 | 2.2-c | 变红 |
| M3 | 午休判成可发单 | 2.2-c 窗口 | 变红 |
| M4 | 重定价超限改静默 `return` | 2.2-d「超出即失败」 | 变红 |
| M5 | 顺延不校验次一交易日 | 2.2-c 交易日语义 | 变红 |
| M6 | `next_trading_day` 改自然日 +1 | 2.2-c 交易日语义 | 变红 |
| M7 | 买单允许朝下改价 | 只朝有利方向 | 变红 |
| M8 | 陈旧报价照样用于改价 | 停牌/滞后数据 | 变红 |
| M9 | 限速被拒仍发单（不看令牌） | 节流≠事故 | 变红 |
| M10 | 收盘未成交不计顺延 | 2.2-c | 变红 |
| M11 | 已发过的单再 PLACE 一次 | 重复委托 | 变红 |

## 局限（必须写明）

变异测试只能证明"**这些**缺陷会被抓到"，不能证明"所有缺陷都会被抓到"。
M4 与 M9 是最值钱的变异：它们都是"把闸门改成静默继续"——
这正是本项目历史上代价最大的错误形态。

用法：
    python scripts/session_mutation_test.py
    python scripts/session_mutation_test.py --only M7 M8

退出码：0 = 全部检出；1 = 有变异没被检出（测试有洞）；2 = 源文件恢复失败。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[1]
EXEC = REPO / "backend" / "src" / "deepquant" / "execution"
SESSION = EXEC / "session.py"
LOOP = EXEC / "order_loop.py"
T_SESSION = REPO / "backend" / "tests" / "execution" / "test_session.py"
T_LOOP = REPO / "backend" / "tests" / "execution" / "test_order_loop.py"
PY = sys.executable

#: (编号, 说明, 源文件, 测试文件, 原文片段, 替换片段)
MUTANTS: list[tuple[str, str, pathlib.Path, pathlib.Path, str, str]] = [
    # ---------------- session.py ----------------
    ("M1", "重定价上限 2→3（2.2-d 被放宽）",
     SESSION, T_SESSION, "MAX_REPRICE = 2", "MAX_REPRICE = 3"),
    ("M2", "顺延上限 10→11（2.2-c 被放宽）",
     SESSION, T_SESSION, "MAX_CARRY_DAYS = 10", "MAX_CARRY_DAYS = 11"),
    ("M3", "午休判成可发单（交易窗口错误）",
     SESSION, T_SESSION,
     "    if t < times.pm_open:\n        return Phase.LUNCH",
     "    if t < times.pm_open:\n        return Phase.PLACE_AM"),
    ("M4", "重定价超限改静默 return（账本会显示没超）",
     SESSION, T_SESSION,
     '            raise RepriceNotAllowed(\n'
     '                f"{self.client_order_id} 重定价已达上限 {MAX_REPRICE} 次（2.2-d），不得再改价")',
     "            return 0"),
    ("M5", "顺延不校验次一交易日（接受自然日）",
     SESSION, T_SESSION, "        if next_day != expect:", "        if False:"),
    ("M6", "next_trading_day 改为自然日 +1（交易日语义丢失）",
     SESSION, T_SESSION, "    return seq[i + 1]",
     '    return (dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))'
     ' + dt.timedelta(days=1)).strftime("%Y%m%d")'),
    # ---------------- order_loop.py ----------------
    ("M7", "买单让价方向反转（×0.997 而非 ×1.003，买不到反而降价）",
     LOOP, T_LOOP, "        raw = quote.last * (1.0 + deviation)",
     "        raw = quote.last * (1.0 - deviation)"),
    ("M8", "陈旧报价照样用于改价（停牌股按老价追市）",
     LOOP, T_LOOP, "    if quote.stale:", "    if False:"),
    ("M9", "限速被拒仍发单（不看令牌）",
     LOOP, T_LOOP, "            if not _take(state.limiter, now):",
     "            if False:"),
    ("M10", "收盘未成交不计顺延（不调 on_close）",
     LOOP, T_LOOP, "            outcome = tr.on_close(day, filled=False)",
     "            outcome = CloseOutcome.CARRY"),
    ("M11", "已发过的单再 PLACE 一次（重复委托）",
     LOOP, T_LOOP,
     "        # 1) 未发过 → 发单\n        if tr.placed_at is None:",
     "        # 1) 未发过 → 发单\n        if True:"),
]


def run_tests(test_file: pathlib.Path) -> tuple[int, str]:
    r = subprocess.run(
        [PY, "-m", "pytest", str(test_file), "-x", "-q"],
        cwd=str(REPO / "backend"), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO / "backend" / "src")},
    )
    lines = (r.stdout or "").strip().splitlines()
    return r.returncode, (lines[-1] if lines else "(无输出)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="session / order_loop 变异测试")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定编号，如 --only M7 M8")
    args = ap.parse_args(argv)

    for f in (SESSION, LOOP, T_SESSION, T_LOOP):
        if not f.is_file():
            print(f"[mut] ✗ 找不到 {f}")
            return 1

    originals = {SESSION: SESSION.read_bytes(), LOOP: LOOP.read_bytes()}
    orig_sha = {k: hashlib.sha256(v).hexdigest() for k, v in originals.items()}
    for k, v in orig_sha.items():
        print(f"[mut] 基线 {k.name} sha256 = {v[:16]}…")

    # 0) 原版必须全绿；否则后面全是噪音
    for tf in (T_SESSION, T_LOOP):
        rc, line = run_tests(tf)
        if rc != 0:
            print(f"[mut] ✗ 原版 {tf.name} 未通过（{line}）——先修原版")
            return 1
        print(f"[mut] 原版 {tf.name} PASS（{line}）")
    print()

    selected = [m for m in MUTANTS if not args.only or m[0] in args.only]
    if args.only:
        print(f"[mut] 只跑：{[m[0] for m in selected]}\n")

    bad: list[str] = []
    try:
        for mid, desc, src, tf, old, new in selected:
            text = originals[src].decode("utf-8")
            n = text.count(old)
            if n != 1:
                print(f"[mut] {mid} ⚠️ 锚点命中 {n} 次（需恰好 1 次），变异体本身失效")
                bad.append(f"{mid}(锚点)")
                continue
            src.write_bytes(text.replace(old, new).encode("utf-8"))
            rc, line = run_tests(tf)
            if rc != 0:
                print(f"[mut] {mid} ✓ 被检出 —— {desc}\n           {line}")
            else:
                print(f"[mut] {mid} ✗ **未被检出** —— {desc}")
                bad.append(mid)
            src.write_bytes(originals[src])
    finally:
        for k, v in originals.items():
            k.write_bytes(v)
        for k, v in orig_sha.items():
            now = hashlib.sha256(k.read_bytes()).hexdigest()
            if now != v:
                print(f"[mut] ✗✗ 恢复失败：{k.name} 已改变（{now[:16]}…）——用 git 恢复")
                return 2

    rc1, l1 = run_tests(T_SESSION)
    rc2, l2 = run_tests(T_LOOP)
    print(f"\n[mut] 恢复后复跑：session {'PASS' if rc1 == 0 else 'FAIL'}（{l1}）｜"
          f"order_loop {'PASS' if rc2 == 0 else 'FAIL'}（{l2}）")
    if rc1 != 0 or rc2 != 0:
        return 1

    print(f"\n[mut] 结果：{len(selected) - len(bad)}/{len(selected)} 个变异被检出")
    if bad:
        print(f"[mut] ✗ 未检出/失效：{', '.join(bad)}——测试有洞，先补测试")
        return 1
    print("[mut] ✓ 全部检出：这两批测试对 2.2-c / 2.2-d 是有效的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
