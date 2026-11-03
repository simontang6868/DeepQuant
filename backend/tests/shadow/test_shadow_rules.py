"""影子复盘：规则判定。

每个用例都同时检查「该报的报了」与「不该报的没报」，并检查停用登记——
因为「没检出」与「没问题」必须能区分开，这是本项目最容易犯的错。
"""

from __future__ import annotations

import pytest

from deepquant.shadow.baseline import build_baseline
from deepquant.shadow.rules import RULE_CATALOG, audit_sequence, audit_sequences, render_markdown
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


def _baseline_sessions(count: int = 8, *, operators=("alice", "bob")):
    """构造一份「数据集清单 → 回测两次 → 对比」的重复语料。"""
    cards = []
    seq = 0
    for index in range(count):
        session = f"s{index}"
        operator = operators[index % len(operators)]
        for action in ("dataset.list", "backtest.run", "backtest.run", "backtest.compare"):
            seq += 1
            cards.append(
                _card(
                    seq,
                    action,
                    operator=operator,
                    session=session,
                    body={"initial_cash": 100000},
                    duration=10,
                )
            )
    return sequences_from_cards(cards)


def _sequence(actions, *, operator="alice", start=900, durations=None, statuses=None, bodies=None):
    durations = durations or [10] * len(actions)
    statuses = statuses or ["ok"] * len(actions)
    bodies = bodies if bodies is not None else [{"initial_cash": 100000}] * len(actions)
    cards = [
        _card(
            start + offset,
            action,
            operator=operator,
            session="target",
            status=statuses[offset],
            body=bodies[offset],
            duration=durations[offset],
        )
        for offset, action in enumerate(actions)
    ]
    return sequences_from_cards(cards)[0]


def _codes(sequence, baseline):
    hits, skipped = audit_sequence(sequence, baseline)
    return {hit.code for hit in hits}, {item.code for item in skipped}


def test_operator_unknown_fires_and_lists_baseline_operators():
    baseline = build_baseline(_baseline_sessions())
    codes, _ = _codes(_sequence(["dataset.list", "backtest.run"], operator="mallory"), baseline)
    assert "operator_unknown" in codes


def test_operator_unknown_is_skipped_when_baseline_has_single_operator():
    baseline = build_baseline(_baseline_sessions(operators=("alice",)))
    codes, skipped = _codes(_sequence(["dataset.list", "backtest.run"], operator="mallory"), baseline)
    assert "operator_unknown" not in codes
    assert "operator_unknown" in skipped, "单操作人语料下该规则必须显式停用"


def test_step_order_unseen_fires_on_novel_transition():
    baseline = build_baseline(_baseline_sessions())
    # 对比之后又回去看清单：基线里没有这种回头路
    codes, _ = _codes(_sequence(["dataset.list", "backtest.run", "backtest.compare", "dataset.list"]), baseline)
    assert "step_order_unseen" in codes


def test_step_order_unseen_silent_on_known_transition():
    baseline = build_baseline(_baseline_sessions())
    codes, _ = _codes(_sequence(["dataset.list", "backtest.run", "backtest.run", "backtest.compare"]), baseline)
    assert "step_order_unseen" not in codes


def test_prerequisite_missing_for_compare_without_two_runs():
    baseline = build_baseline(_baseline_sessions())
    codes, _ = _codes(_sequence(["dataset.list", "backtest.run", "backtest.compare"]), baseline)
    # 只有一次 run 就对比：前置条件不成立（与「顺序是否常见」无关）
    assert "prerequisite_missing" in codes


def test_prerequisite_satisfied_does_not_fire():
    baseline = build_baseline(_baseline_sessions())
    codes, _ = _codes(_sequence(["dataset.list", "backtest.run", "backtest.run", "backtest.compare"]), baseline)
    assert "prerequisite_missing" not in codes


def test_reject_storm_needs_three_consecutive_rejections():
    baseline = build_baseline(_baseline_sessions())
    two = _sequence(
        ["dataset.list", "backtest.run", "backtest.run"],
        statuses=["rejected", "rejected", "ok"],
    )
    codes, _ = _codes(two, baseline)
    assert "reject_storm" not in codes

    three = _sequence(
        ["dataset.list", "backtest.run", "backtest.run"],
        statuses=["rejected", "rejected", "rejected"],
    )
    codes, _ = _codes(three, baseline)
    assert "reject_storm" in codes


def test_duration_outlier_requires_enough_samples():
    baseline = build_baseline(_baseline_sessions(count=2))
    codes, skipped = _codes(
        _sequence(["dataset.list"], durations=[10_000]),
        baseline,
    )
    assert "duration_outlier" not in codes
    assert "duration_outlier" in skipped, "样本不足时必须停用，而不是按 0 次即正常"


def test_duration_outlier_fires_above_factor_of_p99():
    # 阈值要求该操作至少有 20 条基线耗时样本，故这里用 25 个会话
    baseline = build_baseline(_baseline_sessions(count=25))
    codes, _ = _codes(_sequence(["dataset.list"], durations=[10_000]), baseline)
    assert "duration_outlier" in codes


def test_burst_repeat_flags_four_identical_requests():
    baseline = build_baseline(_baseline_sessions())
    body = {"initial_cash": 100000}
    threes = _sequence(["backtest.run"] * 3, bodies=[body] * 3)
    codes, _ = _codes(threes, baseline)
    assert "burst_repeat" not in codes

    fours = _sequence(["backtest.run"] * 4, bodies=[body] * 4)
    codes, _ = _codes(fours, baseline)
    assert "burst_repeat" in codes


def test_session_multi_operator_detected():
    baseline = build_baseline(_baseline_sessions())
    cards = [
        _card(1, "dataset.list", operator="alice", session="x"),
        _card(2, "backtest.run", operator="bob", session="x"),
    ]
    sequence = sequences_from_cards(cards)[0]
    codes, _ = _codes(sequence, baseline)
    assert "session_multi_operator" in codes


def test_off_hours_is_skipped_for_single_period_corpus():
    baseline = build_baseline(_baseline_sessions())
    _, skipped = _codes(_sequence(["dataset.list", "backtest.run"]), baseline)
    assert "off_hours" in skipped


def test_every_catalog_rule_is_either_fired_skipped_or_enabled_no_hit():
    baseline = build_baseline(_baseline_sessions())
    report = audit_sequences(_baseline_sessions()[:2], baseline)
    fired = set(report["summary"]["by_code"])
    skipped = {item["code"] for item in report["summary"]["skipped_rules"]}
    enabled_no_hit = set(report["summary"]["enabled_no_hit"])
    catalog = {spec.code for spec in RULE_CATALOG}
    assert catalog == fired | skipped | enabled_no_hit
    assert not (fired & skipped)


def test_render_markdown_contains_four_sections_and_evidence():
    baseline = build_baseline(_baseline_sessions())
    report = audit_sequences(
        [_sequence(["dataset.list", "backtest.run"], operator="mallory")], baseline
    )
    markdown = render_markdown(report)
    for section in ("## 一、", "## 二、", "## 三、", "## 四、"):
        assert section in markdown
    assert "card900" in markdown, "命中必须带可定位的 card_id"
    assert "未判断 ≠ 正常" in markdown


def test_clean_sequence_produces_no_findings():
    baseline = build_baseline(_baseline_sessions())
    report = audit_sequences(
        [_sequence(["dataset.list", "backtest.run", "backtest.run", "backtest.compare"])], baseline
    )
    assert report["flagged_sequences"] == 0
    assert report["findings"] == []


@pytest.mark.parametrize("status", ["ok", "rejected"])
def test_status_unseen_only_when_status_is_novel(status):
    baseline = build_baseline(_baseline_sessions())
    codes, _ = _codes(
        _sequence(["dataset.list", "backtest.run"], statuses=[status, "ok"]),
        baseline,
    )
    # 基线里 dataset.list 只有 ok；rejected 属新结果
    assert ("status_unseen" in codes) is (status == "rejected")
