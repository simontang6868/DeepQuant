"""A-P1：对照组输入隔离（D1 回归守卫）。

**背景（2026-10-19 现场证据）**：对照 run `gate1_out/a027865f26ea237a` 里的
`monthly_paired.csv` 与 `monthly_paired_excluded.csv` **sha256 完全相同**（`cmp` 逐字节相同）。
根因是「先剔除再跑、再在同一份已剔除数据上重跑主实验当对照」——
两个 run 吃的是同一份输入，于是「排除某标的后结论不变」变成结构性必然，
而不是数据事实。

**D1 的核心不变量（本文件守的）**：
1. 全池输入与排除池输入的**指纹必须不同** —— 这是「两个 run 独立」的最低可验证条件；
2. 指纹是**内容哈希**，所以「输入相同」必然「指纹相同」，反之亦然 ——
   退化（control 复用 main 输入）在指纹层面当场可见；
3. 落盘产物必须把两边指纹**各自记录下来**，否则事后无法证明对照是独立的。

**补充（2026-10-19 02:00）**：`make_run_id` / `pool_fingerprint` 原先只哈希
「标的集合 + 行数 + 首末日期」，形状相同、价格不同的两份数据会撞同一个 id（缺陷 #9）。
B 线已改为 `_frame_digest`（逐值哈希）；本文件的第 2 条用例即为该缺陷的回归守卫。

**仍未覆盖**：`write_outputs` 对「传进来的两组指纹相同」不做拒绝或标注，
即没有**运行时退化守卫**。要加就得先与 B 线约定接口（抛错 or 写 `degenerate` 标记），
A 线不擅自定契约，已列为待裁决项。这不影响上述 1~3：它们能在产物层面把退化暴露出来。

规格来源：NIGHT_PROGRESS「🎯 下一阶段任务分配（2026-10-19 01:10）」A-P1 规格第三条。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from _ap1_support import judgement_stub, load_gate1_run, make_run_result

FULL_POOL = ("AAA.SH", "BBB.SZ", "CCC.SZ")


def _prices(symbols=FULL_POOL, n: int = 30) -> dict[str, pd.DataFrame]:
    """构造一份**内容可控**的假行情：每只标的收盘价不同，便于指纹区分。"""
    out = {}
    for i, sym in enumerate(symbols):
        base = 10.0 + i
        out[sym] = pd.DataFrame(
            {
                "date": pd.bdate_range("2024-01-01", periods=n),
                "close": [base + 0.01 * j for j in range(n)],
                "volume": [1_000_000.0] * n,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 1. 全池 vs 排除池：指纹必须不同
# --------------------------------------------------------------------------- #


def test_excluded_input_has_different_fingerprint():
    gate1_run = load_gate1_run()
    """排除一只标的后，输入指纹必须改变。

    这是 D1 的地基：若指纹不随输入变化，就没有任何机制能证明两个 run 独立。
    """
    full = _prices()
    excluded = gate1_run.apply_exclusion(full, ["BBB.SZ"])

    assert set(excluded) == {"AAA.SH", "CCC.SZ"}
    assert gate1_run.make_run_id(full) != gate1_run.make_run_id(excluded), (
        "全池与排除池的输入指纹相同 —— 说明指纹没有反映实际输入，D1 防线失效"
    )


def test_fingerprint_is_sensitive_to_content_not_just_symbol_set():
    gate1_run = load_gate1_run()
    """指纹必须在**内容**变化时改变，而不只是标的数量变化时改变。

    只比「标的集合 + 行数 + 日期」是常见的假防线：换掉价格但保持形状，照样应不同 ——
    否则「内容哈希」名不副实，且不同数据可能共用同一个 run_id（输出目录）。
    """
    a = _prices()
    b = _prices()
    b["AAA.SH"] = b["AAA.SH"].assign(close=lambda d: d["close"] + 1.0)

    assert set(a) == set(b), "本例刻意保持标的集合相同，只改内容"
    assert gate1_run.make_run_id(a) != gate1_run.make_run_id(b)


# --------------------------------------------------------------------------- #
# 2. 退化必须可被识别：相同输入 ⇒ 相同指纹
# --------------------------------------------------------------------------- #


def test_identical_inputs_collide_so_degeneration_is_detectable():
    gate1_run = load_gate1_run()
    """「control 复用 main 输入」在指纹层面必然碰撞 —— 这正是可检测点。

    本用例把「退化」写成可执行断言：只要两边的输入其实是同一份数据，
    `make_run_id` 就会给出同一个 id。任何「两个 run 必须不同」的检查都能据此喊停。
    """
    prices = _prices()
    main_input = prices
    control_input = gate1_run.apply_exclusion(prices, [])  # 未真正排除 = 退化形态

    assert gate1_run.make_run_id(main_input) == gate1_run.make_run_id(control_input), (
        "退化形态下两边输入应完全相同（本用例正是用来证明碰撞可被观测）"
    )
    # 反过来：真正的排除必然打破碰撞
    real_exclusion = gate1_run.apply_exclusion(prices, ["AAA.SH"])
    assert gate1_run.make_run_id(main_input) != gate1_run.make_run_id(real_exclusion)


# --------------------------------------------------------------------------- #
# 3. 指纹必须落盘（可追溯）
# --------------------------------------------------------------------------- #


def test_inputs_are_persisted_for_traceability(tmp_path):
    gate1_run = load_gate1_run()
    """`write_outputs` 必须把两组输入指纹写进 summary.json 与 manifest.json。

    没有落盘就只剩一个结论字符串；事后无法证明「对照确实是独立输入」。
    """
    main = make_run_result(gate1_run, name="main")
    control = make_run_result(gate1_run, name="main")

    fp_main = gate1_run.make_run_id(_prices())
    fp_control = gate1_run.make_run_id(gate1_run.apply_exclusion(_prices(), ["BBB.SZ"]))
    assert fp_main != fp_control

    inputs = {"main": {"fingerprint": fp_main}, "control": {"fingerprint": fp_control}}
    gate1_run.write_outputs(
        tmp_path,
        [main, control],
        judgement_stub(),
        elapsed=0.0,
        excluded_symbols=("BBB.SZ",),
        control=control,
        inputs=inputs,
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert summary["inputs"]["main"]["fingerprint"] == fp_main
    assert summary["inputs"]["control"]["fingerprint"] == fp_control
    assert summary["inputs"]["main"]["fingerprint"] != summary["inputs"]["control"]["fingerprint"]
    assert manifest["inputs"] == summary["inputs"], "manifest 与 summary 的输入指纹必须一致"

    # 对照的排除标的也要可追溯，否则「排除了什么」只能靠猜
    assert summary["excluded_symbols"] == ["BBB.SZ"]
    assert summary["control_excluded"]["excluded_symbols"] == ["BBB.SZ"]
