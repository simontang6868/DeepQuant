"""R8 §2/§3：每日状态邮件链头 + 备份清单纳入（静态与行为双检）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import corrections_hashchain as hc  # noqa: E402

ALERT = ROOT / "deploy" / "bin" / "heartbeat_alert.py"


def _load_alert():
    spec = importlib.util.spec_from_file_location("hb_alert_r8", ALERT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _chain(tmp_path, n=3):
    src = tmp_path / "c.jsonl"
    ch = tmp_path / "chain.jsonl"
    src.write_bytes(b"".join(('{"n": %d}\n' % i).encode() for i in range(1, n + 1)))
    hc.build(source=src, chain=ch)
    return ch


def test_mail_body_contains_chain_head(tmp_path):
    mod = _load_alert()
    mod.CHAIN = _chain(tmp_path)
    body = mod._with_chain("心跳 2026-11-15T17:14:24，mode=NOT_DUE。")
    assert "心跳 2026-11-15" in body
    assert "更正台账链头（第 3 行）：" in body
    head = hc.head(chain=mod.CHAIN)
    assert head in body and len(head) == 64
    assert "不构成更正完整性证明" in body


def test_missing_chain_is_explicitly_unavailable(tmp_path):
    mod = _load_alert()
    mod.CHAIN = tmp_path / "missing.jsonl"
    body = mod._with_chain("正常")
    assert "CHAIN_UNAVAILABLE" in body
    assert "不构成" in body and "请查" in body


def test_empty_and_malformed_chain_fail_closed(tmp_path):
    mod = _load_alert()
    ch = tmp_path / "chain.jsonl"
    ch.write_text("", encoding="utf-8")
    mod.CHAIN = ch
    assert "CHAIN_UNAVAILABLE" in mod._with_chain("x")
    ch.write_text(json.dumps({"seq": 1, "line_sha256": "0" * 64,
                              "chain_head": "NOT_A_HASH"}) + "\n", encoding="utf-8")
    assert "CHAIN_UNAVAILABLE" in mod._with_chain("x")


def test_finish_uses_with_chain():
    # 验收项对齐：finish 必须调用 _with_chain，否则「正常/告警都带链头」不成立
    src = ALERT.read_text(encoding="utf-8")
    assert "body = _with_chain(body)" in src
    assert "def finish(" in src


def test_backup_offsite_includes_chain_stage_and_archives():
    s = (ROOT / "deploy" / "bin" / "backup_offsite.sh").read_text(encoding="utf-8")
    for key in ("$FWD/corrections.jsonl", "$FWD/corrections.hashchain.jsonl",
                "$FWD/stage_results.json", '*.log.1', '*.log-20[0-9]',
                'for LOGDIR in "$REPO/var/log" "$REPO/logs"'):
        assert key in s, f"备份清单缺 {key}"


def test_weekly_offsite_includes_chain_and_stage():
    s = (ROOT / "deploy" / "bin" / "weekly_offsite.sh").read_text(encoding="utf-8")
    assert "corrections.hashchain.jsonl" in s
    assert "stage_results.json" in s
