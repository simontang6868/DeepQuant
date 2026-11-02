"""SP-5 行为测试：验证「闸门本身」而不是数字。

覆盖：
1. ST 月末展开**无前视**（改将来事件不得影响过去状态）；
2. 措辞审计能抓住未标注的旧结论；
3. 措辞审计会正确**放过**单纯对账（−17.82% vs −17.91%），不制造假阳性。

运行：`$PY -m pytest scripts/sp5/tests -q`
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # scripts/sp5

import st_sz_build  # noqa: E402
import wording_audit  # noqa: E402


def _mk_chg(rows):
    df = pd.DataFrame(rows, columns=["code", "date", "name_before", "name_after"])
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["code", "date"], kind="mergesort").reset_index(drop=True)


MONTHS = [
    pd.Timestamp("2004-12-31"),
    pd.Timestamp("2010-04-30"),
    pd.Timestamp("2010-06-30"),
    pd.Timestamp("2012-05-31"),
    pd.Timestamp("2012-06-30"),
]
ROWS = [
    ("000001", "2005-01-10", "深发展A", "G深发展A"),
    ("000001", "2010-05-20", "G深发展A", "ST深发"),
    ("000001", "2012-06-01", "ST深发", "深发展A"),
]


def test_build_monthly_state_transitions():
    out = st_sz_build.build_monthly(_mk_chg(ROWS), MONTHS)
    got = out[out["code"] == "000001"].sort_values("date")["name"].tolist()
    assert got == ["深发展A", "G深发展A", "ST深发", "ST深发", "深发展A"]
    assert out["is_st"].tolist() == [False, False, True, True, False]


def test_build_monthly_no_lookahead():
    """把「将来」的事件改成别的名字，过去各月的状态必须逐字不变。"""
    base = st_sz_build.build_monthly(_mk_chg(ROWS), MONTHS)
    tampered_rows = ROWS[:2] + [("000001", "2012-06-01", "ST深发", "*ST深发")]
    tamp = st_sz_build.build_monthly(_mk_chg(tampered_rows), MONTHS)

    early_mask = base["date"] <= pd.Timestamp("2012-05-31")
    pd.testing.assert_frame_equal(
        base.loc[early_mask].reset_index(drop=True),
        tamp.loc[early_mask].reset_index(drop=True),
    )
    # 被改的那一期确实变了 —— 否则说明测试没测到东西
    last_b = base[base["date"] == pd.Timestamp("2012-06-30")]["name"].iloc[0]
    last_t = tamp[tamp["date"] == pd.Timestamp("2012-06-30")]["name"].iloc[0]
    assert last_b != last_t


def test_wording_catches_unannotated_claim(tmp_path: Path):
    (tmp_path / "a.md").write_text(
        "# 报告\n\n25% 仓位：MDD −17.82%，两条目标同时达成。\n", encoding="utf-8"
    )
    rows, n = wording_audit.audit(tmp_path, exempt=set())
    assert n == 1
    assert rows, "未标注的旧结论必须被命中"
    assert all(not r["annotated"] for r in rows)


def test_wording_passes_annotated_claim(tmp_path: Path):
    (tmp_path / "a.md").write_text(
        "# 报告\n\n25% 仓位：MDD −17.82%，两条目标同时达成。\n\n"
        "> ⚠️ 已被 §10.1 撤回：日频为 −22.43%。\n",
        encoding="utf-8",
    )
    rows, _ = wording_audit.audit(tmp_path, exempt=set())
    assert rows and all(r["annotated"] for r in rows)


def test_wording_no_false_positive_on_reconciliation(tmp_path: Path):
    """只做「月频 vs 日频」对账，不算在传播旧结论。"""
    (tmp_path / "a.md").write_text(
        "# 审计\n\n| 项 | 原值 | 复算 |\n|---|---|---|\n"
        "| 25% MDD（月频） | −17.82% | −17.91% |\n", encoding="utf-8"
    )
    rows, _ = wording_audit.audit(tmp_path, exempt=set())
    assert rows == [], f"不应命中对账行，实际命中：{[r['text'] for r in rows]}"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("收益预期 7~11%", True),          # 情景假设缺标签
        ("收益预期 7~11%（研究情景假设）", False),  # 已标
        ("股票仓位 20% 仓位日频 MDD −14.05%", True),
    ],
)
def test_scenario_label_rule(line: str, expected: bool):
    hit = any(test(line) for _, test, _ in wording_audit.RULES)
    assert hit is expected
