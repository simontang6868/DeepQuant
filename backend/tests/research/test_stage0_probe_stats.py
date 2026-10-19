"""阶段 0a 幸存者偏差探针的统计层测试。

为什么必须测
------------
`scripts/stage0_survivorship_probe.py` 里有两块**新写的判断逻辑**，它们直接决定
`SURVIVORSHIP_MATERIAL` 这类结论会不会被误报：

1. **Mann-Whitney U 的自实现**（环境无 scipy）：并列秩、tie 校正、单双侧方向
   任何一处写错，都会让 p 值指向错误的一侧，而结论是「偏差把结论撑高了」还是
   「偏差不存在」完全由这个符号决定。
2. **`decide()` 的四条规则**：S1/S2 的差别只在 Δmedian 的符号，S3 的措辞又刻意
   不等同于「偏差不存在」。规则写反了，报告就会把"测不出来"说成"没有偏差"（R3）。

本文件只测这两块 + 两个纯函数（`desc` / `valid_rets`），不跑回测（回测由真数据跑）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "stage0_survivorship_probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("stage0_probe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _load()


# --------------------------------------------------------------- Mann-Whitney


def test_mwu_完全分离时_B_偏小的单侧_p_很小(probe):
    """B 组全部小于 A 组 → U_B=0，单侧 p 必须指向『B 偏小』且远小于 0.05。"""
    r = probe.mannwhitney([1, 2, 3], [4, 5, 6])
    assert r["U_B"] == 0
    assert r["p_one_sided_B_less"] < 0.05
    assert r["p_two_sided"] < 0.1
    assert r["rank_biserial_B_minus_A"] < 0


def test_mwu_方向反转时单侧_p_接近1(probe):
    """B 组全部大于 A 组 → 同一个单侧 p 必须接近 1，不能因为对称就也给小值。"""
    r = probe.mannwhitney([4, 5, 6], [1, 2, 3])
    assert r["U_B"] == 9
    assert r["p_one_sided_B_less"] > 0.95
    assert r["rank_biserial_B_minus_A"] > 0


def test_mwu_无差异时_双侧不显著(probe):
    """两组完全交错 → 双侧不显著。

    注意 3v3 时 U 只取 0..9 的**离散**值，「完全交错」对应 U=3，而期望是 4.5，
    所以单侧 p=0.256（双侧 0.513）是正确值 —— 不要误以为"无差异就该给 0.5"。
    """
    r = probe.mannwhitney([1, 3, 5], [2, 4, 6])
    assert r["U_B"] == 3
    assert r["p_two_sided"] > 0.4


def test_mwu_并列值使用平均秩(probe):
    """带并列的样本必须走平均秩，否则 U 会偏向秩次靠前的一组。"""
    r = probe.mannwhitney([1, 2, 2], [2, 3, 3])
    assert r["n_B"] == 3 and r["n_A"] == 3
    # 并列使 U 落在期望附近，不应出现「完全分离」的极端值
    assert 0 < r["U_B"] < 9


def test_mwu_全并列时方差为零返回错误而不是瞎给p值(probe):
    """全部取值相同 → 方差为 0，必须显式报错；给一个假 p 值等于编造。"""
    r = probe.mannwhitney([1, 1, 1], [1, 1, 1])
    assert r.get("error") == "zero variance"


def test_mwu_空组返回错误(probe):
    r = probe.mannwhitney([], [1, 2, 3])
    assert r.get("error") == "empty group"


def test_mwu_对称性_B偏小与A偏小互补(probe):
    """交换两组后，两侧 p 应互补（单侧），这是实现正确性的强约束。"""
    x, y = [1, 2, 8, 9], [3, 4, 5, 6, 7]
    p_xy = probe.mannwhitney(x, y)["p_one_sided_B_less"]
    p_yx = probe.mannwhitney(y, x)["p_one_sided_B_less"]
    assert abs((p_xy + p_yx) - 1.0) < 1e-9


# -------------------------------------------------------------------- decide


def _stats(nA, medA, nB, medB):
    return {
        "A_listed": {"n": nA, "median": medA},
        "B_delisted": {"n": nB, "median": medB},
    }


def test_decide_样本不足判S0(probe):
    code, _ = probe.decide(_stats(10, 0.0, 200, -0.1),
                           {"p_one_sided_B_less": 0.001, "p_two_sided": 0.002})
    assert code == "S0"


def test_decide_双侧都够但有一组小于30仍判S0(probe):
    """A 组 29 个也不算够——阈值是 30，不是 20。"""
    code, _ = probe.decide(_stats(29, 0.0, 100, -0.1),
                           {"p_one_sided_B_less": 0.001, "p_two_sided": 0.002})
    assert code == "S0"


def test_decide_B更差且显著判S1(probe):
    code, reason = probe.decide(_stats(100, 0.01, 200, -0.02),
                                {"p_one_sided_B_less": 0.001, "p_two_sided": 0.002})
    assert code == "S1"
    assert "SURVIVORSHIP_MATERIAL" in reason


def test_decide_B更好且显著判S2(probe):
    """Δmedian > 0 时必须走 S2，不能被 S1 的 p 值吞掉。"""
    code, reason = probe.decide(_stats(100, -0.02, 200, 0.01),
                                {"p_one_sided_B_less": 0.999, "p_two_sided": 0.002})
    assert code == "S2"
    assert "SURVIVORSHIP_REVERSED" in reason


def test_decide_不显著判S3且措辞不得等同于没有偏差(probe):
    code, reason = probe.decide(_stats(100, 0.0, 200, -0.001),
                                {"p_one_sided_B_less": 0.4, "p_two_sided": 0.8})
    assert code == "S3"
    assert "测不出来" in reason or "不显著" in reason


def test_decide_单侧显著但中位差为正不得判S1(probe):
    """p 小但 Δmedian 符号相反 → S1 的条件不成立（防止符号写反）。"""
    code, _ = probe.decide(_stats(100, 0.0, 200, 0.01),
                           {"p_one_sided_B_less": 0.01, "p_two_sided": 0.02})
    assert code != "S1"


def test_decide_空统计判S0而不是崩(probe):
    code, _ = probe.decide({"A_listed": {"n": 0}, "B_delisted": {"n": 0}},
                           {"error": "empty group"})
    assert code == "S0"


# ------------------------------------------------------------------ 辅助函数


def test_desc_空列表只返回n(probe):
    assert probe.desc([]) == {"n": 0}


def test_desc_统计量正确(probe):
    d = probe.desc([-0.1, 0.0, 0.1, 0.2])
    assert d["n"] == 4
    assert d["median"] == pytest.approx(0.05)
    assert d["loss_ratio"] == pytest.approx(0.25)
    assert d["pos_ratio"] == pytest.approx(0.5)


def test_valid_rets_严格按口径过滤(probe):
    """四个条件缺一不可；ret 为 None 也不能混进统计。"""
    rec = {"windows": [
        {"selection_success": True, "oos_success": True, "is_complete": True, "ret": 0.1},
        {"selection_success": False, "oos_success": True, "is_complete": True, "ret": 9.9},
        {"selection_success": True, "oos_success": False, "is_complete": True, "ret": 9.9},
        {"selection_success": True, "oos_success": True, "is_complete": False, "ret": 9.9},
        {"selection_success": True, "oos_success": True, "is_complete": True, "ret": None},
    ]}
    assert probe.valid_rets(rec) == [0.1]


def test_冻结参数与research脚本一致(probe):
    """R2/R11 的核心防线：探针的网格必须与 research.py 的 MA_GRID 逐项相同。"""
    g = probe.MA_GRID
    assert tuple(g.fast_candidates) == (3, 5, 10, 20)
    assert tuple(g.slow_candidates) == (15, 25, 40, 60)
    assert g.min_train_periods == 150
    assert g.min_oos_periods == 75
    assert g.min_trades == 2
    assert g.max_combinations == 50
    assert probe.INITIAL_CASH == 100_000.0
    assert (probe.START, probe.END) == ("2017-01-01", "2024-12-31")
