"""ECR-P0-SNAPSHOT-1 / R7-3/4：日更链阶段 rc 收紧的验收闸门（AC-5）。

判定真源是 ``scripts/stage_rc_gate.py`` 的纯函数；本文件同时核对
``daily_chain.sh`` 确实接上了闸门、且每个关键阶段都有 record_stage 采集。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import stage_rc_gate as srg  # noqa: E402

SHELL = ROOT / "deploy" / "bin" / "daily_chain.sh"


# ── AC-5 任一关键阶段 rc≠0 ⇒ ok=false 且告警 ────────────────────────────
def test_ac5_all_green_is_ok():
    r = srg.evaluate("update=0:关键;is_due=0:关键;universe_snap=0:关键;"
                     "paper_snapshot=0:关键;shadow_reconcile=0:关键;backup=0:关键")
    assert r["ok"] is True and r["alert"] is False
    assert r["failed_key_stages"] == [] and r["n_stages"] == 6


def test_ac5_key_stage_failure_marks_chain_failed():
    r = srg.evaluate("update=0:关键;paper_snapshot=4:关键;backup=0:关键")
    assert r["ok"] is False and r["alert"] is True
    assert r["failed_key_stages"] == ["paper_snapshot"]


def test_ac5_every_key_stage_is_enforced():
    """删掉 KEY_STAGES 任一项，这一条就会变红（AC-5 的删除实现证据）。"""
    for name in srg.KEY_STAGES:
        r = srg.evaluate(f"{name}=1:关键")
        assert r["ok"] is False, name
        assert name in r["failed_key_stages"], name


def test_ac5_ignored_stage_does_not_fail_but_is_listed():
    r = srg.evaluate("update=0:关键;heartbeat_ping=1:忽略;mem_peak=1:忽略")
    assert r["ok"] is True and r["alert"] is False
    assert set(r["ignored_nonzero"]) == {"heartbeat_ping", "mem_peak"}


def test_ac5_unknown_stage_fails_closed():
    r = srg.evaluate("update=0:关键;mystery=0:关键")
    assert r["ok"] is False and r["unknown_stages"] == ["mystery"]


def test_ac5_cli_exit_code():
    py = sys.executable
    ok = subprocess.run([py, str(ROOT / "scripts" / "stage_rc_gate.py"),
                         "--check", "paper_snapshot=0:关键"], capture_output=True)
    bad = subprocess.run([py, str(ROOT / "scripts" / "stage_rc_gate.py"),
                          "--check", "paper_snapshot=4:关键"], capture_output=True)
    assert ok.returncode == 0 and bad.returncode == 1


# ── shell 接线：删掉实现即变红 ─────────────────────────────────────────
def test_shell_wires_the_gate():
    src = SHELL.read_text(encoding="utf-8")
    assert 'stage_rc_gate.py" --check "$STAGES"' in src
    assert "STAGE_RESULTS=" in src
    assert '"stages": stage_list' in src          # 阶段结果落进心跳/结果文件
    assert 'record_stage paper_snapshot "$pa_rc" 关键' in src
    assert 'record_stage shadow_reconcile "$sh_rc" 关键' in src
    assert 'record_stage backup "$brc" 关键' in src
    assert 'record_stage universe_snap 0 关键' in src


def test_shell_every_key_stage_has_a_record():
    src = SHELL.read_text(encoding="utf-8")
    for name in ("update", "is_due", "universe_snap", "paper_snapshot",
                 "shadow_reconcile", "backup"):
        assert f"record_stage {name} " in src, name


def test_shell_ignored_items_have_reasons():
    src = SHELL.read_text(encoding="utf-8")
    assert "网络或外部服务问题，不影响本机链路事实" in src
    assert "纯诊断项" in src


def test_shell_syntax_valid():
    r = subprocess.run(["bash", "-n", str(SHELL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_shell_no_longer_claims_snapshot_failure_is_nonblocking():
    """旧注释「失败只告警不阻断」必须已作废，否则文档与实现矛盾。"""
    src = SHELL.read_text(encoding="utf-8")
    assert "失败只告警并写进心跳 stage_note" not in src
    assert "不改变本次链路结论" not in src
