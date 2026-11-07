#!/usr/bin/env python
"""``scripts/paper_account_adapter.py`` 的变异测试。

项目铁律：新增检查器必须先证明「断言有牙齿」。本轮测试自身还出过一次
无限递归（install 的 lambda 调回被 patch 的函数），导致所有"期望失败"
的测试因为 RecursionError 假绿——已在该批测试里给每条失败用例补上
**原因断言**，这里再用变异体复核。

| # | 注入缺陷 | 期望 |
|---|---|---|
| M1 | total 不按分项相加 | 红 |
| M2 | 不校验交易日 | 红 |
| M3 | 不转换 SHSE. 前缀 | 红 |
| M4 | 允许同日静默覆盖 | 红 |
| M5 | 缺凭证不失败关闭 | 红 |
| M6 | 调用下单函数 | 红 |
| M7 | get_cash 返回空照样继续 | 红 |

用法：``python scripts/paper_adapter_mutation_test.py``
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[1]
TARGET = REPO / "scripts" / "paper_account_adapter.py"
TESTS = REPO / "scripts" / "tests" / "test_paper_account_adapter.py"
PY = sys.executable

MUTANTS: list[tuple[str, str, str, str]] = [
    ("M1", "total 不按分项相加（会让 Cash 校验与契约记录失真）",
     '"total": available + frozen}', '"total": 0.0}'),
    ("M2", "不校验交易日",
     "if not is_trading_day(args.date):", "if False:"),
    ("M3", "不转换 SHSE./SZSE. 代码前缀",
     '        s = s.rsplit(".", 1)[-1]', '        s = s'),
    ("M4", "允许同日静默覆盖",
     'if path.is_file() and path.read_text(encoding="utf-8") != body:',
     "if False:"),
    ("M5", "缺凭证不失败关闭",
     'missing = [k for k in (ENV_TOKEN, ENV_ACCOUNT) if not (e.get(k) or "").strip()]',
     "missing = []"),
    ("M6", "调用下单函数（最危险的越界）",
     "    cash = gm.get_cash()\n",
     '    gm.order_volume(symbol="SHSE.600000", volume=100)\n'
     "    cash = gm.get_cash()\n"),
    ("M7", "get_cash 返回空照样继续",
     "    if cash is None:", "    if False:"),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run([PY, "-m", "pytest", str(TESTS), "-q", "--no-header"],
                       cwd=str(REPO), capture_output=True, text=True)
    lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
    return r.returncode, (lines[-1] if lines else "<no output>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    selected = [m for m in MUTANTS if not args.only or m[0] in args.only]
    if not selected:
        return 1

    original = TARGET.read_bytes()
    orig_sha = hashlib.sha256(original).hexdigest()
    src = original.decode("utf-8")
    bad: list[str] = []
    try:
        for mid, desc, old, new in selected:
            n = src.count(old)
            if n != 1:
                print(f"[mut] {mid} ✗ 原文片段出现 {n} 次（要求 1 次）——定义失效")
                bad.append(mid)
                continue
            TARGET.write_text(src.replace(old, new, 1), encoding="utf-8", newline="\n")
            rc, last = run_tests()
            caught = rc != 0
            print(f"[mut] {mid} {'✓ 检出' if caught else '✗ 漏检'}｜{desc}｜{last}")
            if not caught:
                bad.append(mid)
            TARGET.write_bytes(original)
    finally:
        TARGET.write_bytes(original)
        if hashlib.sha256(TARGET.read_bytes()).hexdigest() != orig_sha:
            print("[mut] ✗✗ 源文件恢复失败——请用 git 恢复")
            return 2

    rc, last = run_tests()
    print(f"\n[mut] 恢复后复跑：{'PASS' if rc == 0 else 'FAIL'}（{last}）")
    if rc != 0:
        return 1
    print(f"\n[mut] 结果：{len(selected) - len(bad)}/{len(selected)} 个变异被检出")
    if bad:
        print(f"[mut] ✗ 未检出/失效：{', '.join(bad)}")
        return 1
    print("[mut] ✓ 全部检出：只读适配器的闸门有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
