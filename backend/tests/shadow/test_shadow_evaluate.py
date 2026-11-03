"""影子复盘：注入与命中率评估。

核心命题：评估本身不能作弊。
- 空操作（注入后什么都没变）必须被识别并重抽，不能算作「未命中」；
- 注入不得改动原序列（否则干净集会被污染，误报率变成假 0）；
- 命中率与误报率必须同批报出，且带区间。
"""

from __future__ import annotations

import pytest

from deepquant.shadow.baseline import build_baseline
from deepquant.shadow.evaluate import (
    EXPECTED_CODES,
    INJECTORS,
    collect_injected,
    evaluate,
    wilson_interval,
)
from deepquant.shadow.sequences import sequences_from_cards


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


def _corpus(count: int = 30):
    cards = []
    seq = 0
    for index in range(count):
        for action in ("dataset.list", "backtest.run", "backtest.run", "backtest.compare"):
            seq += 1
            cards.append(
                _card(
                    seq,
                    action,
                    operator=("alice", "bob")[index % 2],
                    session=f"s{index}",
                    body={"initial_cash": 100000},
                )
            )
    return sequences_from_cards(cards)


def test_injection_does_not_mutate_source_sequences():
    corpus = _corpus()
    before = [seq.pattern for seq in corpus]
    baseline = build_baseline(corpus)
    collect_injected(baseline, corpus, per_type=3, seed=1)
    after = [seq.pattern for seq in corpus]
    assert before == after


def test_noop_injections_are_counted_not_scored():
    """全部会话只含一种模式时，交换相邻两步必然是无操作：应被计数并重抽。"""
    corpus = _corpus()
    baseline = build_baseline(corpus)
    samples, noops = collect_injected(baseline, corpus, per_type=2, seed=7)
    # 交换 run/run 是无操作；具体次数取决于抽样，但至少要被记录到 dict 里
    assert set(noops) == set(INJECTORS)
    assert all(isinstance(value, int) for value in noops.values())
    for group in samples.values():
        assert len(group) <= 2


def test_evaluate_reports_hit_rate_and_clean_false_positive():
    corpus = _corpus()
    baseline = build_baseline(corpus[:20])
    result = evaluate(baseline, corpus[20:], per_type=5, seed=11)
    assert result["clean"]["total"] == len(corpus[20:])
    assert result["clean"]["flagged"] == 0, "干净留出集不应被报异常（同分布语料）"
    assert result["anomalies"]["total"] > 0
    assert 0.0 <= result["anomalies"]["hit_rate"] <= 1.0
    assert len(result["anomalies"]["hit_rate_ci"]) == 2


def test_evaluate_rejects_empty_holdout():
    baseline = build_baseline(_corpus(4))
    with pytest.raises(ValueError, match="留出集为空"):
        evaluate(baseline, [])


def test_expected_codes_cover_every_injector():
    assert set(EXPECTED_CODES) == set(INJECTORS)


def test_wilson_interval_brackets_point_estimate():
    low, high = wilson_interval(8, 10)
    assert low < 0.8 < high
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_operator_swap_is_detected_end_to_end():
    corpus = _corpus()
    baseline = build_baseline(corpus)
    result = evaluate(baseline, corpus[:5], per_type=3, seed=3)
    stats = result["anomalies"]["by_type"]["operator_swap"]
    assert stats["flagged_rate"] == 1.0
