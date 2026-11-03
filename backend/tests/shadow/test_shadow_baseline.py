"""影子复盘：基线构建与摘要校验。

重点验证两件事：
1. 同一语料 → 同一摘要（顺序无关、无隐藏状态）；
2. 摘要/版本不符时**拒绝**加载，而不是「尽力而为」地用一份可疑基线做质检。
"""

from __future__ import annotations

import json

import pytest

from deepquant.shadow.baseline import (
    BASELINE_VERSION,
    baseline_digest,
    build_baseline,
    load_baseline,
    save_baseline,
)
from deepquant.shadow.sequences import OperationSequence, sequences_from_cards


def _card(seq, action, *, operator="alice", session="s1", status="ok", body=None, duration=10):
    return {
        "seq": seq,
        "card_id": f"card{seq:03d}",
        "action": action,
        "operator": operator,
        "occurred_at": "2026-10-27T10:00:00+00:00",
        "session": session,
        "basis": {"request": {"body": body, "body_sha256": None}},
        "outcome": {"status": status, "duration_ms": duration},
    }


def _corpus() -> list[OperationSequence]:
    cards = []
    seq = 0
    for session in range(6):
        for action in ("dataset.list", "backtest.run"):
            seq += 1
            cards.append(_card(seq, action, session=f"s{session}", body={"cash": 100000}))
    return sequences_from_cards(cards)


def test_digest_is_order_independent_and_stable():
    sequences = _corpus()
    first = build_baseline(sequences)
    second = build_baseline(list(reversed(sequences)))
    assert baseline_digest(first) == baseline_digest(second)


def test_top_sequences_ranked_by_frequency():
    baseline = build_baseline(_corpus())
    assert baseline["top_sequences"][0]["pattern"] == ["dataset.list", "backtest.run"]
    assert baseline["top_sequences"][0]["sessions"] == 6


def test_injected_sequences_are_excluded_from_baseline():
    sequences = _corpus()
    injected = sequences[0].replace_steps(
        [step.replace(injected="param_drift") for step in sequences[0].steps]
    )
    baseline = build_baseline(list(sequences) + [injected])
    assert baseline["corpus"]["excluded_injected"] == 1
    assert baseline["corpus"]["sequences"] == len(sequences)


def test_terminal_session_rate_counts_runs_and_compares():
    baseline = build_baseline(_corpus())
    assert baseline["corpus"]["terminal_session_rate"] == pytest.approx(1.0)


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "baseline.json"
    baseline = build_baseline(_corpus())
    save_baseline(path, baseline, note="测试")
    loaded = load_baseline(path)
    assert loaded["baseline_digest"] == baseline_digest(baseline)
    # 文件必须是 LF：Windows 文本模式会把 \n 写成 \r\n，跨平台比对就会假报警
    raw = path.read_bytes()
    assert b"\r\n" not in raw


def test_load_rejects_tampered_content(tmp_path):
    path = tmp_path / "baseline.json"
    save_baseline(path, build_baseline(_corpus()))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operations"]["backtest.run"]["count"] = 999
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="摘要校验失败"):
        load_baseline(path)


def test_load_rejects_unknown_version(tmp_path):
    path = tmp_path / "baseline.json"
    save_baseline(path, build_baseline(_corpus()))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["baseline_version"] = "t6-baseline-v0"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="基线版本不符"):
        load_baseline(path)


def test_baseline_records_version_constant():
    baseline = build_baseline(_corpus())
    assert baseline["baseline_version"] == BASELINE_VERSION
