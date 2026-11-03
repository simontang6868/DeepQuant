"""影子复盘：序列抽取与参数骨架。

覆盖的坑：
- 请求体在 ``basis.request.body``（不在 ``inputs``），取错位置会让骨架恒空；
- 标识类字符串必须归一，否则「正常的新 run_id」会被当成参数漂移；
- 无会话头的卡片必须按 (操作人, 日) 聚合，而不是拼成一条长序列。
"""

from __future__ import annotations

from deepquant.shadow.sequences import (
    MISSING_SESSION_PREFIX,
    digest_of,
    param_skeleton,
    sequences_from_cards,
    step_from_card,
)


def _card(seq: int, action: str, **overrides):
    card = {
        "seq": seq,
        "card_id": f"card{seq:03d}",
        "action": action,
        "operator": "alice",
        "occurred_at": "2026-10-27T10:00:00+00:00",
        "session": "s1",
        "basis": {"request": {"method": "POST", "path": "/backtests", "body": None}},
        "outcome": {"status": "ok", "status_code": 200, "duration_ms": 10},
        "inputs": {"body_sha256": None, "body_bytes": 0},
    }
    card.update(overrides)
    return card


def test_skeleton_buckets_numbers_by_magnitude_not_value():
    small = param_skeleton({"initial_cash": 100000})
    larger_same_bucket = param_skeleton({"initial_cash": 120000})
    different_bucket = param_skeleton({"initial_cash": 10_000_000})
    assert small == larger_same_bucket
    assert small != different_bucket


def test_skeleton_normalizes_run_ids_and_timestamps():
    a = param_skeleton({"run_ids": ["3f2a1b4c5d6e7f8091a2b3c4d5e6f708", "9a8b7c6d5e4f30211234567890abcdef"]})
    b = param_skeleton({"run_ids": ["11111111111111111111111111111111", "22222222222222222222222222222222"]})
    assert a == b, "不同 run_id 不应产生不同骨架"
    stamped = param_skeleton({"at": "2026-10-27T10:00:00"})
    assert stamped["body"]["at"] == "str:timestamp"


def test_step_reads_body_from_basis_request_not_inputs():
    body = {"dataset_symbol": "DQA01", "initial_cash": 100000}
    card = _card(1, "backtest.run", basis={"request": {"body": body, "body_sha256": "abc"}})
    step = step_from_card(card)
    assert step.skeleton["body"]["dataset_symbol"] == "str:DQA01"
    assert step.body_digest == "abc"


def test_step_marks_digest_only_body_instead_of_empty():
    card = _card(
        1,
        "backtest.run",
        basis={"request": {"body": None, "body_sha256": "deadbeef"}},
        inputs={"body_sha256": "deadbeef"},
    )
    step = step_from_card(card)
    assert step.skeleton == {"body": "digest_only"}
    assert step.body_digest == "deadbeef"


def test_sequences_group_by_session_and_sort_by_seq():
    cards = [
        _card(2, "backtest.run", session="s1"),
        _card(1, "dataset.list", session="s1"),
        _card(3, "strategy.list", session="s2"),
    ]
    sequences = sequences_from_cards(cards)
    first = next(seq for seq in sequences if seq.session == "s1")
    assert first.pattern == ("dataset.list", "backtest.run")


def test_sequences_without_session_fall_back_to_operator_and_day():
    cards = [
        _card(1, "dataset.list", session=None, occurred_at="2026-10-27T09:00:00+00:00"),
        _card(2, "strategy.list", session=None, occurred_at="2026-10-27T11:00:00+00:00"),
        _card(3, "dataset.list", session=None, occurred_at="2026-10-28T09:00:00+00:00"),
    ]
    sequences = sequences_from_cards(cards)
    ids = [seq.sequence_id for seq in sequences]
    assert ids == [f"{MISSING_SESSION_PREFIX}:alice:2026-10-27", f"{MISSING_SESSION_PREFIX}:alice:2026-10-28"]
    assert sequences[0].pattern == ("dataset.list", "strategy.list")


def test_digest_of_is_order_insensitive_for_dicts():
    assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})
