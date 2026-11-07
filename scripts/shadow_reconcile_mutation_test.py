#!/usr/bin/env python
"""``scripts/shadow_daily_reconcile.py`` 的变异测试。

项目铁律：**任何一致性检查必须先做变异测试**——故意注入已知缺陷，
确认测试真的抓得到。本项目吃过两次「等价变异」（改了源码但行为没变，
于是"全检出"是假的），所以这里每个变异都要求 ``old`` 在源码里恰好出现
一次，且必须让至少一条测试变红。

| # | 注入的缺陷 | 为什么危险 | 期望 |
|---|---|---|---|
| M1 | 去掉「必须有真实仿真账户」 | ⭐ 拿自己造的快照自证 G2，最危险 | 红 |
| M2 | 不校验账户快照 asof | 用三天前的持仓对账，时点证据造假 | 红 |
| M3 | 输入缺失改为静默成功 | 失败关闭被绕过 | 红 |
| M4 | 停牌股也填 last_price | 已知口径差被误判成 UNEXPLAINED 假告警 | 红 |
| M5 | 未知跳过原因硬塞进"整手不足" | ⭐ 自己把闸门关了（模块文档原话） | 红 |
| M6 | ``orders_sent`` 写成 True | 记录声称发过单 | 红 |
| M7 | 允许同日静默覆盖 | 证据可被改写 | 红 |
| M8 | 未解释差异不返回非零 | 告警永远不响 | 红 |
| M9 | 老台账（LEGACY_NO_D4）也计入 G2 | 11-30 前的记录冒充样本外 | 红 |
| M10 | G2 门槛 20 日改成 1 日 | 闸门形同虚设 | 红 |
| M11 | 事后补跑也计入 G2 | ⭐ 把回顾包装成连续前瞻 | 红 |

局限：只证明**这些**缺陷会被抓到，不证明所有缺陷都会被抓到。

用法：``python scripts/shadow_reconcile_mutation_test.py [--only M1 M5]``
退出码：0 全部检出｜1 有变异漏检｜2 源文件恢复失败
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[1]
TARGET = REPO / "scripts" / "shadow_daily_reconcile.py"
TESTS = REPO / "scripts" / "tests" / "test_shadow_daily_reconcile.py"
PY = sys.executable

#: (编号, 说明, 原文片段, 替换片段) —— old 必须在源码里恰好出现一次
MUTANTS: list[tuple[str, str, str, str]] = [
    ("M1", "去掉真实账户检查 ⇒ 自证 G2",
     "    constitutes = (mode == REAL_PAPER and n_unexplained == 0\n"
     "                   and not is_backfill)",
     "    constitutes = (n_unexplained == 0\n"
     "                   and not is_backfill)"),
    ("M2", "不校验账户快照 asof",
     'if str(d.get("asof", "")) != day:',
     'if False:'),
    ("M3", "输入缺失改成静默成功",
     "        return rec, EXIT_INPUT\n\n    led_path, led_rec = led",
     "        return rec, EXIT_OK\n\n    led_path, led_rec = led"),
    ("M4", "停牌股也填 last_price",
     'if v["last"] > 0 and not v["no_quote"]},',
     'if v["last"] > 0},'),
    ("M5", "未知跳过原因硬塞进整手不足",
     '        if "不足一手" in why or "资金不足" in why:\n'
     '            lot_short.add(code)\n'
     '        # "无有效行情" / 未知原因：都不归类，交给 last_price / UNEXPLAINED',
     '        if "不足一手" in why or "资金不足" in why:\n'
     '            lot_short.add(code)\n'
     '        else:\n'
     '            lot_short.add(code)'),
    ("M6", "记录声称发过单",
     '        "orders_sent": False,\n        "diffs": diffs,',
     '        "orders_sent": True,\n        "diffs": diffs,'),
    ("M7", "允许同日静默覆盖",
     '        if p.read_text(encoding="utf-8") != body:\n'
     '            return p, EXIT_CONFLICT',
     '        if False:\n'
     '            return p, EXIT_CONFLICT'),
    ("M8", "未解释差异不返回非零",
     "    rc = EXIT_UNEXPLAINED if (mode == REAL_PAPER and n_unexplained) else EXIT_OK",
     "    rc = EXIT_OK"),
    ("M9", "老台账也计入 G2",
     "    if constitutes and legacy:\n        constitutes = False",
     "    if False and constitutes and legacy:\n        constitutes = False"),
    ("M10", "G2 门槛 20 日改成 1 日",
     '"need": 20, "passed": len(streak) >= 20}',
     '"need": 20, "passed": len(streak) >= 1}'),
    ("M11", "允许事后补跑计入 G2（把回顾包装成前瞻）",
     "    constitutes = (mode == REAL_PAPER and n_unexplained == 0\n"
     "                   and not is_backfill)",
     "    constitutes = (mode == REAL_PAPER and n_unexplained == 0)"),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run([PY, "-m", "pytest", str(TESTS), "-q", "--no-header"],
                       cwd=str(REPO), capture_output=True, text=True)
    lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
    return r.returncode, (lines[-1] if lines else "<no output>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="只跑指定变异（如 M1 M5）")
    args = ap.parse_args()
    selected = [m for m in MUTANTS
                if not args.only or m[0] in args.only]
    if not selected:
        print("[mut] 没有匹配的变异体")
        return 1

    original = TARGET.read_bytes()
    orig_sha = hashlib.sha256(original).hexdigest()
    src = original.decode("utf-8")

    bad: list[str] = []
    try:
        for mid, desc, old, new in selected:
            n = src.count(old)
            if n != 1:
                print(f"[mut] {mid} ✗ 原文片段出现 {n} 次（要求恰好 1 次）——"
                      "变异定义失效，不算检出")
                bad.append(mid)
                continue
            TARGET.write_text(src.replace(old, new, 1), encoding="utf-8",
                              newline="\n")
            rc, last = run_tests()
            caught = rc != 0
            print(f"[mut] {mid} {'✓ 检出' if caught else '✗ 漏检'}｜{desc}｜{last}")
            if not caught:
                bad.append(mid)
            TARGET.write_bytes(original)
    finally:
        TARGET.write_bytes(original)
        now = hashlib.sha256(TARGET.read_bytes()).hexdigest()
        if now != orig_sha:
            print("[mut] ✗✗ 源文件恢复失败——请用 git 恢复")
            return 2

    rc, last = run_tests()
    print(f"\n[mut] 恢复后复跑：{'PASS' if rc == 0 else 'FAIL'}（{last}）")
    if rc != 0:
        return 1
    print(f"\n[mut] 结果：{len(selected) - len(bad)}/{len(selected)} 个变异被检出")
    if bad:
        print(f"[mut] ✗ 未检出/失效：{', '.join(bad)}——测试有洞，先补测试")
        return 1
    print("[mut] ✓ 全部检出：对账脚本的闸门是有牙齿的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
