"""ECR-D1D2-MIGRATION-1 验收闸门（AC-1 ~ AC-4）。

判定写成**纯字符串断言函数**，变异脚本直接调用同一函数给出「删除实现即变红」证据；
期望值来自独立算术/常量，不调用被测脚本的判定逻辑。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

NEW_HOST = "root@1.92.141.140"
OLD_HOST = "110.40.190.225"
OLD_KEY = "dq_deploy_ed25519"
KEY_NAME = "fk_server_ed25519"
NOT_DUE_CASE = 'case "$due_line" in NOT_DUE*) due_rc=0 ;; esac'

TARGET_SCRIPTS = (
    "g1_progress.py",
    "deploy_manifest.py",
    "check_channel_isolation.py",
    "check_universe_mirror.py",
)


def assert_targets(src: str) -> None:
    """AC-1：默认目标必须是新机 + 新密钥，且源码里不得残留旧机/旧密钥字面量。"""
    assert NEW_HOST in src, "默认主机未指向新机"
    assert OLD_HOST not in src, "仍残留旧机 IP"
    assert KEY_NAME in src, "未指向新密钥"
    assert OLD_KEY not in src, "仍残留旧密钥名"
    assert "Path.home()" in src, "密钥路径未用 Path.home() 解析"


def assert_not_due_fix(src: str) -> None:
    """AC-2：is_due 用 NOT_DUE=1 表示非决策日，不得被 record_stage 判为关键失败。"""
    assert NOT_DUE_CASE in src, "缺 NOT_DUE 归一化"


def assert_timeout_constants(src: str) -> None:
    """AC-4：26MB 级周包附件的超时余量。"""
    assert "SEC_PER_MB = 30.0" in src, "SEC_PER_MB 未回灌"
    assert "MIN_TIMEOUT = 300.0" in src, "MIN_TIMEOUT 未回灌"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── AC-1 ────────────────────────────────────────────────────────────────
def test_four_scripts_default_targets():
    for name in TARGET_SCRIPTS:
        assert_targets((SCRIPTS / name).read_text(encoding="utf-8"))


def test_runtime_constants_resolve_to_new_machine():
    import g1_progress
    import deploy_manifest
    import check_channel_isolation
    import check_universe_mirror

    assert g1_progress.HOST == NEW_HOST
    assert deploy_manifest.REMOTE_HOST == NEW_HOST
    assert check_channel_isolation.REMOTE_HOST == NEW_HOST
    assert check_universe_mirror.HOST == NEW_HOST

    expected = Path.home() / ".ssh" / "fk_server_ed25519"
    assert g1_progress.KEY == expected
    assert deploy_manifest.SSH_KEY == expected
    assert check_channel_isolation.SSH_KEY == expected
    assert check_universe_mirror.KEY == expected


# ── AC-2 ────────────────────────────────────────────────────────────────
def test_not_due_normalization():
    shell = (ROOT / "deploy" / "bin" / "daily_chain.sh").read_text(encoding="utf-8")
    assert_not_due_fix(shell)
    # 归一化必须发生在 record_stage 之前，否则改了 due_rc 也没用
    assert shell.index(NOT_DUE_CASE) < shell.index("record_stage is_due")


# ── AC-3 / AC-4 ─────────────────────────────────────────────────────────
def test_large_attachment_timeout():
    mail = ROOT / "deploy" / "bin" / "notify_mail.py"
    assert_timeout_constants(mail.read_text(encoding="utf-8"))
    nm = _load(mail, "notify_mail_d1d2")

    # 独立算术：11-12 周包实测 26.45 MB；60 + 30 × 26.45 = 853.5 s
    mb = 26.45
    oracle = 60.0 + 30.0 * mb
    got = nm.timeout_for(int(mb * 1048576))
    assert got >= 853.0, "26MB 附件超时余量不足"
    assert abs(got - oracle) < 1.0, "timeout_for 偏离独立算术"
    assert nm.timeout_for(0) == 300.0, "下限应为 300s"

    # 旧常量（8 s/MB + 下限 180）只给 271.6 s，26MB 周包必然撞线
    assert max(180.0, 60.0 + 8.0 * mb) < 300.0
