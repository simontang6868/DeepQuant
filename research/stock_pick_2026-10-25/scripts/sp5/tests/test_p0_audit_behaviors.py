# -*- coding: utf-8 -*-
"""SP-5 / §九：11 项关键行为审计（规格测试）。

设计原则
--------
本文件测的是**要求的行为是否存在于当前代码**，不是"数字对不对"。
因此部分测试**预期失败**——失败即缺陷证据，不得为了变绿而修改被测代码
（专家任务书 §二.8：发现代码/配置错误，先存原始证据、以独立审计产物记录、禁止静默修复）。

所有测试均为**只读**：不写 `forward/`、不改任何既有产物。
涉及"写入行为"的项（台账不可变、哈希校验）用**源码静态检查**判定，避免副作用。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SP5 = Path(__file__).resolve().parents[1]
BASE = SP5.parents[1]
sys.path.insert(0, str(SP5))
sys.path.insert(0, str(SP5.parent))

import fill_engine as fe                     # noqa: E402
import p04b_ledger_independent as p04b       # noqa: E402
import p13_forward as p13                    # noqa: E402
import p13b_forward_track as p13b            # noqa: E402


def _src(name: str) -> str:
    p = SP5 / name
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- T1 配置冻结校验
def test_T1_freeze_config_hash_is_enforced_not_just_recorded():
    """要求：运行时 `assert sha256(config) == .sha256`，不一致即中止。

    现状（P0-3 G1）：p13_forward 只把哈希写进台账，无任何比较 → 本测试失败。
    """
    s = _src("p13_forward.py")
    has_assert = bool(re.search(r"assert\s+.*(cfg_hash|sha256)", s))
    reads_manifest = "sp4_config_v1.sha256" in s
    assert has_assert and reads_manifest, (
        "p13_forward 未在运行时校验冻结配置哈希（仅记录）；"
        f"assert={has_assert} 读 .sha256={reads_manifest} —— 对应 P0-3 G1 FAIL"
    )


# ---------------------------------------------------------------- T2 台账不可变
def test_T2_ledger_is_immutable():
    """要求：已存在的 FORWARD 台账拒绝覆盖（需显式 --force 且留副本）。

    现状（P0-3 G4）：无条件 write_text 覆盖 → 本测试失败。
    """
    s = _src("p13_forward.py")
    i = s.find('LEDGER / f"{me}.json").write_text')
    assert i > 0, "未找到台账写入语句（测试本身失效，需更新定位）"
    pre = s[max(0, i - 600):i]
    guarded = ("exists()" in pre) and bool(re.search(r"if\s+.*:", pre))
    assert guarded, "写入台账前未检查文件是否已存在（可静默覆盖）—— 对应 P0-3 G4 FAIL"


# ---------------------------------------------------------------- T3 种子可复现
def test_T3_seed_reproducibility():
    """同一种子两次构建，目标持仓序列必须逐位一致。"""
    panel = pd.read_parquet(p13.p03.PANEL) if hasattr(p13, "p03") else None
    import p03_tradability as p03
    panel = pd.read_parquet(p03.PANEL)
    a = p03.build_decisions(panel, 5)[2]
    b = p03.build_decisions(panel, 5)[2]
    assert a == b, "同种子两次 build_decisions 结果不一致"


# ---------------------------------------------------------------- T4 资产恒等式
def test_T4_cash_hold_identity():
    """nav == stock_value + cash，逐日成立。"""
    codes = ["000001", "600000", "000002"]
    px = {c: np.full(50, 10.0) for c in codes}
    tabs = {c: (np.array(["20200102"]), np.array([True]), np.array([True])) for c in codes}
    cal = ["20200102"] * 50
    cal_pos = {d: i for i, d in enumerate(cal)}
    holds = [codes] * 3
    months = ["202001", "202002", "202003"]
    buy = ["20200102"] * 3
    nxt = ["20200102"] * 3
    df = p04b.simulate(holds, 0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal)
    assert np.allclose(df["nav"], df["stock_value"] + df["cash"], atol=1e-9)


# ---------------------------------------------------------------- T5 费用只扣一次
def test_T5_single_fee_deduction():
    """买入无印花、卖出有印花；三费之和 = 返回总额；最低佣金生效。"""
    notional = 5000.0
    cb, tb, sb = p04b.fee_parts(notional, "buy")
    cs, ts, ss = p04b.fee_parts(notional, "sell")
    assert sb == 0.0 and ss == pytest.approx(notional * p04b.STAMP)
    assert cb == pytest.approx(p04b.COMM_MIN)          # 1.25 < 5 → 取 5
    assert cb + tb + sb == pytest.approx(5.0 + notional * p04b.TRANSFER)


# ---------------------------------------------------------------- T6 缺失行情不得静默填充
def test_T6_no_silent_fill_in_backtest():
    """要求：缺失行情必须显式标注，不得 ffill。

    现状（P0-2 §3 / 审计项 10）：p04 使用 pd.Series(arr).ffill() → 本测试失败。
    """
    s = _src("p04_position_dd.py")
    m = re.search(r"pd\.Series\(arr\)\.ffill\(\)", s)
    assert m is None, "p04 仍在使用 pd.Series(arr).ffill() 静默填充缺失行情 —— 审计项 10 FAIL"


# ---------------------------------------------------------------- T7 SELFTEST 隔离
def test_T7_selftest_excluded_by_enforcement():
    """要求：存在聚合/校验层，统计时强制排除 counts_toward_observation=False 的记录。

    现状（P0-3 G11）：无任何聚合层读取该字段 → 本测试失败（UNVERIFIED 无法升级为 PASS）。
    """
    # 必须是「对 flags 的比较/过滤」，不能只出现字段名（`rec['counts_...']` 这类取值不算）
    pat = re.compile(r"counts_toward_observation[^\n]{0,30}(==|!=|\.query\(|is True|is False)")
    hits = []
    for f in sorted(SP5.glob("*.py")):
        t = f.read_text(encoding="utf-8")
        if pat.search(t):
            hits.append(f.name)
    assert hits, ("无任何代码对 counts_toward_observation 做比较/过滤（只有赋值与打印），"
                  "SELFTEST 不会被强制排除 —— 对应 P0-3 G11")


# ---------------------------------------------------------------- T8 数据截止拒收
def test_T8_data_cutoff_rejected():
    """建仓日/终点不在日历或早于建仓日 → 必须拒收（返回非 0 / 抛错）。"""
    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    assert p13b.execution_day("20260930", cal, cal_pos) in cal_pos
    # 终点早于建仓日：track_nav 的边界由 run() 拦截；此处验证纯函数层不会静默产出空序列
    with pytest.raises((ValueError, KeyError)):
        p13b.track_nav([], {}, "29991231", "20000101", fe.load_bars, cal=cal, cal_pos=cal_pos)


# ---------------------------------------------------------------- T9 失败必须留痕
def test_T9_failure_is_logged():
    """无行情代码的成交判定必须返回明确状态码，不得静默删除订单。"""
    fd, fp, st = p13b.fill_one("999999", "20260930", fe.load_bars, False,
                               fe.market_calendar(), {d: i for i, d in enumerate(fe.market_calendar())})
    assert fd is None and st == "NO_BAR" and not np.isfinite(fp)


# ---------------------------------------------------------------- T10 记录含配置与代码哈希
def test_T10_record_contains_config_and_code_hash():
    """要求：台账同时含配置哈希与**代码提交哈希**（可追溯）。

    现状（P0-1 §5.5）：只写 config_sha256，无代码哈希，且脚本未纳入 git → 本测试失败。
    """
    p = BASE / "forward" / "ledger" / "20260930.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert "config_sha256" in rec and len(rec["config_sha256"]) == 64
    assert any(k in rec for k in ("code_commit", "code_sha", "git_commit")), \
        "台账未记录代码提交哈希 —— 对应 git_provenance TRACEABILITY_GAP"


# ---------------------------------------------------------------- T11 UNVERIFIED 不得冒充 PASS
def test_T11_unverified_never_reported_as_pass():
    """审计产物必须保留 UNVERIFIED 字样，且不得把 UNVERIFIED 项写成 PASS。"""
    reports = list((BASE / "reports").glob("SP5_*.md"))
    assert reports, "找不到审计报告"
    n_unv = sum(r.read_text(encoding="utf-8").count("UNVERIFIED") for r in reports)
    assert n_unv >= 5, f"UNVERIFIED 标记过少（{n_unv}），疑似被抹平为 PASS"
    # 同一行内不得同时出现 UNVERIFIED 与 PASS（表格行级检查）
    bad = []
    for r in reports:
        for line in r.read_text(encoding="utf-8").splitlines():
            if "UNVERIFIED" not in line:
                continue
            cells = [c.strip().strip("*").strip() for c in line.split("|")]
            if any(c == "PASS" for c in cells):      # 同一行某个单元格直接写 PASS
                bad.append((r.name, line[:80]))
    assert not bad, f"存在把 UNVERIFIED 标成 PASS 的行：{bad[:3]}"
