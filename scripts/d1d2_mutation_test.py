"""ECR-D1D2-MIGRATION-1 真变异（5 类，逐条给「原实现 PASS → 变异体红」证据）。

⚠️ 只做字符串替换 + 调用 test_d1d2_migration 里的纯断言函数，
   不原地改产品源码、不启动 pytest ⇒ 可与其它回归并行，也不会污染工作树。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

from test_d1d2_migration import (  # noqa: E402
    NEW_HOST, OLD_HOST, OLD_KEY, KEY_NAME, NOT_DUE_CASE,
    TARGET_SCRIPTS, assert_targets, assert_not_due_fix, assert_timeout_constants,
)

SCRIPTS = ROOT / "scripts"
SHELL = ROOT / "deploy" / "bin" / "daily_chain.sh"
MAIL = ROOT / "deploy" / "bin" / "notify_mail.py"


def red(assert_fn, mutated: str, label: str) -> bool:
    """原实现必须 PASS；变异体必须抛 AssertionError（= 闸门变红）。"""
    try:
        assert_fn(mutated)
    except AssertionError:
        return True
    print(f"    ✗ 未检出：{label}")
    return False


def main() -> int:
    detected = 0
    total = 0
    print("=== ECR-D1D2-MIGRATION-1 真变异 ===")

    # M1：4 个脚本的 HOST 改回旧机 ⇒ assert_targets 的 OLD_HOST/NEW_HOST 断言变红
    print("[M1] HOST 改回旧机")
    for name in TARGET_SCRIPTS:
        src = (SCRIPTS / name).read_text(encoding="utf-8")
        assert_targets(src)                       # 原实现 PASS
        total += 1
        if red(assert_targets, src.replace(NEW_HOST, OLD_HOST), f"M1/{name}"):
            detected += 1
    print(f"  M1 检出 {detected}/{total}")

    # M2：密钥改回旧部署密钥 ⇒ OLD_KEY 断言变红
    print("[M2] 密钥改回旧部署密钥")
    m2_base = total
    for name in TARGET_SCRIPTS:
        src = (SCRIPTS / name).read_text(encoding="utf-8")
        assert_targets(src)
        total += 1
        if red(assert_targets, src.replace(KEY_NAME, OLD_KEY), f"M2/{name}"):
            detected += 1
    print(f"  M2 检出 {detected - m2_base}/{total - m2_base}")

    # M3：删掉 NOT_DUE 归一化 ⇒ 11-11 那种「非决策日」又会被判关键失败
    print("[M3] 删除 NOT_DUE 归一化")
    shell = SHELL.read_text(encoding="utf-8")
    assert_not_due_fix(shell)
    total += 1
    if red(assert_not_due_fix, shell.replace(NOT_DUE_CASE, ""), "M3/daily_chain.sh"):
        detected += 1

    # M4 / M5：超时系数退回旧值 ⇒ 26MB 周包重新撞线
    mail = MAIL.read_text(encoding="utf-8")
    assert_timeout_constants(mail)
    for label, old, new in (
        ("M4/SEC_PER_MB", "SEC_PER_MB = 30.0", "SEC_PER_MB = 8.0"),
        ("M5/MIN_TIMEOUT", "MIN_TIMEOUT = 300.0", "MIN_TIMEOUT = 180.0"),
    ):
        print(f"[{label[:2]}] {label}")
        total += 1
        if red(assert_timeout_constants, mail.replace(old, new), label):
            detected += 1

    print(f"=== 合计 {detected}/{total} 检出 ===")
    if detected != total:
        print("❌ 存在未检出的变异，验收不通过")
        return 1
    print("✅ 全部变异真改行为且被闸门检出；产品源码未被改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
