# -*- coding: utf-8 -*-
"""Phase 1.1：策略参数单一配置源的一致性测试。

任务书原文：「策略参数只在一处定义，回测、前瞻、实盘都从它读取。
新增一致性测试：三处解析出的参数逐字段相等，不等则测试失败。」

本文件做三件独立的事，**不合并成一个断言**：

  A. **解析层本身正确**（值对、哈希对、坏配置会报错）；
  B. **不存在参数漂移**：AST 扫描全部脚本，硬编码值必须等于配置值；
     不等于则必须登记为「有意覆盖」并写明理由；
  C. **不存在第二个真源**：`sp5_out/` 下不得出现配置副本，
     且 `p13_forward` 的常量必须来自解析层而非字面量。

为什么用 AST 而不是 grep：
  grep 分不清 `K = 5`（硬编码）与 `K = _P.n_hold`（正确取法），
  也分不清注释里提到 5 与真的赋值 5。AST 只看语法结构。
"""

from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path

import pytest

SP5 = Path(__file__).resolve().parents[1]          # scripts/sp5
ROOT = Path(__file__).resolve().parents[3]         # research/stock_pick_2026-10-25
sys.path.insert(0, str(SP5))

import strategy_params as sp  # noqa: E402


#: 参与「漂移检查」的参数名。只放**确实属于策略规则**的量。
#: `CAPITAL` 不在内：它是运行资金（D1 决策），不是策略规则。
PARAM_NAMES = frozenset({
    "K", "THRESH", "LIQ_MAX", "AMT_MIN",
    "CLOSE_HI_CFG", "CLOSE_HI_MAIN", "STAMP", "SPREAD",
})

#: 硬编码站点基线。**任何新增/删除都会让测试失败**——这是有意的：
#: 强制作者在「改用 import」和「显式登记」之间做选择，而不是悄悄多一份。
#: 键 = 相对 ROOT 的 posix 路径，值 = 该文件里出现的参数名集合。
EXPECTED_SITES: dict[str, set[str]] = {
    "scripts/audit/audit_sp4_controls.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/audit/audit_sp4_cost_f3.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/audit/audit_sp4_dump.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/audit/audit_sp4_seed.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/audit/audit_sp4_tradability.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/audit/audit_sp4_trunc_impact.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/sp2_sell_signal.py": {"K", "THRESH"},
    "scripts/sp3_entry_rule.py": {"K", "THRESH", "AMT_MIN"},
    "scripts/sp4_lowliq.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/sp5/p02c_st_effect.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/sp5/p03_tradability.py": {"K", "THRESH", "LIQ_MAX"},
    "scripts/sp5/p04_position_dd.py": {"K", "STAMP"},
    "scripts/sp5/p04b_ledger_independent.py": {"K", "STAMP", "SPREAD"},
    "scripts/sp5/p11_exec_window.py": {"K"},
    "scripts/sp5/p12_industry.py": {"K"},
    "scripts/sp5/p13b_forward_track.py": {"K", "STAMP", "SPREAD"},
    "scripts/sp5/p15_cost_alignment.py": {"K", "STAMP"},
    "scripts/test_exclusion.py": {"K"},
    "scripts/test_f2.py": {"K"},
    "scripts/test_factor.py": {"K"},
}

#: 「有意不等于配置值」的覆盖。每条都必须给出理由与本阶段的依据。
#: 不在这里登记的不一致，会被 `test_no_hardcoded_drift` 判为漂移。
INTENTIONAL_OVERRIDES: dict[tuple[str, str], str] = {
    ("scripts/sp5/p04b_ledger_independent.py", "SPREAD"):
        "独立台账重建只校验流程与分档：不叠加 0.10% 价差压力，故 SPREAD=0.0；"
        "与配置的 spread_scenario=0.001 不同是设计意图，不是漂移。",
}


# --------------------------------------------------------------------------
# AST 扫描
# --------------------------------------------------------------------------

def _num(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    return None


def scan_hardcoded(root: Path) -> dict[str, dict[str, float]]:
    """返回 {相对路径: {参数名: 字面量值}}。

    只认**字面量**赋值。`K = _P.n_hold` 这种右值不是常量的，不算硬编码（这正是我们要的方向）。
    """
    out: dict[str, dict[str, float]] = {}
    for f in sorted((root / "scripts").rglob("*.py")):
        parts = f.parts
        if "tests" in parts or f.name == "strategy_params.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in PARAM_NAMES:
                    v = _num(node.value)
                    if v is not None:
                        out.setdefault(f.relative_to(root).as_posix(), {})[tgt.id] = v
                elif isinstance(tgt, ast.Tuple) and isinstance(node.value, ast.Tuple) \
                        and len(tgt.elts) == len(node.value.elts):
                    for a, b in zip(tgt.elts, node.value.elts):
                        if isinstance(a, ast.Name) and a.id in PARAM_NAMES:
                            v = _num(b)
                            if v is not None:
                                out.setdefault(f.relative_to(root).as_posix(), {})[a.id] = v
    return out


def _config_value(name: str, p: sp.StrategyParams) -> float:
    return {
        "K": float(p.n_hold),
        "THRESH": p.thresh,
        "LIQ_MAX": p.liq_max,
        "AMT_MIN": p.amt_min,
        "CLOSE_HI_CFG": p.price_hi,
        "CLOSE_HI_MAIN": float(sp.KNOWN_DIVERGENCES["price_hi"]["code_value"]),
        "STAMP": p.stamp_duty_sell,
        "SPREAD": p.spread_scenario,
    }[name]


@pytest.fixture(scope="module")
def params():
    return sp.load_params()


# --------------------------------------------------------------------------
# A. 解析层
# --------------------------------------------------------------------------

def test_parses_all_strategy_fields(params):
    """配置里的每个阈值都要能解出正确数值。"""
    assert params.version == "sp4-v1"
    assert params.n_hold == 5
    assert params.thresh == pytest.approx(0.70)
    assert params.liq_max == pytest.approx(0.20)
    assert params.amt_min == pytest.approx(5e6)
    assert params.price_lo == pytest.approx(1.0)
    assert params.price_hi == pytest.approx(50.0)
    assert params.stock_weight == pytest.approx(0.25)
    assert params.cash_yield_annual == pytest.approx(0.03)


def test_parses_cost_fields(params):
    """成本参数必须与 L3ae 的真实费率口径一致（0.025% + 0.05% + 0.001%）。"""
    assert params.commission_rate == pytest.approx(0.00025)
    assert params.min_commission == pytest.approx(5.0)
    assert params.stamp_duty_sell == pytest.approx(0.0005)
    assert params.transfer_fee == pytest.approx(1e-5)
    assert params.spread_scenario == pytest.approx(0.001)


def test_config_hash_matches_declared_sidecar(params):
    """配置哈希必须与 `forward/sp4_config_v1.sha256` 声明的一致。"""
    assert params.config_sha256 == sp.config_sha_declared(ROOT)


def test_missing_field_raises(tmp_path):
    """缺字段必须报错，不能静默用默认值。"""
    cfg = tmp_path / "sp4_config_v1.json"
    cfg.write_text(json.dumps({"version": "sp4-v1", "strategy": {}, "cost": {}}),
                   encoding="utf-8")
    with pytest.raises(sp.ConfigError, match="缺少必需字段"):
        sp.load_params(cfg)


def test_unparseable_threshold_raises(tmp_path):
    """自然语言字段解不出数 ⇒ 报错。

    这条钉的是最危险的失败模式：配置从 `f3pct <= 0.70` 改成 `f3pct <= 0.6 至 0.65`，
    正则失配时若静默回退到 0.70，参数漂移将完全不可见。
    """
    good = json.loads((ROOT / sp.DEFAULT_CONFIG).read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["strategy"]["f3_filter_entry"] = "f3pct <= 百分之七十"
    cfg = tmp_path / "sp4_config_v1.json"
    cfg.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sp.ConfigError, match="无法解析出数值"):
        sp.load_params(cfg)


def test_version_mismatch_raises(tmp_path):
    """配置版本变了必须报错——字段语义可能已不同。"""
    good = json.loads((ROOT / sp.DEFAULT_CONFIG).read_text(encoding="utf-8"))
    good["version"] = "sp4-v2"
    cfg = tmp_path / "sp4_config_v1.json"
    cfg.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sp.ConfigError, match="版本"):
        sp.load_params(cfg)


def test_missing_config_raises(tmp_path):
    with pytest.raises(sp.ConfigError, match="配置不存在"):
        sp.load_params(tmp_path / "nope.json")


def test_known_divergence_is_real(params):
    """已知偏差表必须与实际不符之处**严格对应**。

    若哪天 1.2 的 amendment 把配置改成 200，这条会失败，提醒把偏差从表里删掉
    ——登记表不能变成永久豁免。
    """
    div = sp.KNOWN_DIVERGENCES["price_hi"]
    assert div["config_value"] == pytest.approx(params.price_hi)
    assert div["code_value"] != params.price_hi, "配置与代码已一致，应从 KNOWN_DIVERGENCES 移除"
    assert div["issue"] == "CONFIG_PRICE_FILTER_MISMATCH"
    assert div["handling"].startswith("Phase 1.2")


# --------------------------------------------------------------------------
# B. 漂移检查
# --------------------------------------------------------------------------

def test_hardcoded_sites_match_baseline():
    """硬编码站点清单必须与基线一字不差（新增/删除都失败）。"""
    got = scan_hardcoded(ROOT)
    got_sets = {k: set(v) for k, v in got.items()}
    assert got_sets == EXPECTED_SITES, (
        "硬编码站点清单发生变化。\n"
        f"只在新基线里: {sorted(set(got_sets) - set(EXPECTED_SITES))}\n"
        f"只在旧基线里: {sorted(set(EXPECTED_SITES) - set(got_sets))}\n"
        f"字段有变化: {{k: (EXPECTED_SITES.get(k), v) for k, v in got_sets.items() "
        f"if k in EXPECTED_SITES and EXPECTED_SITES[k] != v}}\n"
        "新增硬编码请改为从 strategy_params 取值；若确属历史脚本不便改动，"
        "请把它加入 EXPECTED_SITES 并在注释里写明原因。"
    )


def test_no_hardcoded_drift(params):
    """每个硬编码站点的值都必须等于配置值（或有登记的有意覆盖）。"""
    got = scan_hardcoded(ROOT)
    drift: list[str] = []
    for path, names in got.items():
        for name, value in names.items():
            expected = _config_value(name, params)
            if math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-15):
                continue
            if (path, name) in INTENTIONAL_OVERRIDES:
                continue
            drift.append(f"{path}: {name} = {value!r}，配置是 {expected!r}")
    assert not drift, "参数漂移（硬编码值与配置不符且未登记）：\n" + "\n".join(drift)


def test_intentional_overrides_are_still_needed(params):
    """登记过的「有意覆盖」必须确实仍与配置不同。

    否则它会变成一条永久豁免：覆盖早已不存在，登记还在，将来真正的不一致会被它挡住。
    """
    got = scan_hardcoded(ROOT)
    stale = []
    for (path, name), reason in INTENTIONAL_OVERRIDES.items():
        assert reason.strip(), f"{path}:{name} 的登记缺少理由"
        if path not in got or name not in got[path]:
            stale.append(f"{path}:{name} 已不存在于代码中")
            continue
        if math.isclose(got[path][name], _config_value(name, params), rel_tol=1e-12):
            stale.append(f"{path}:{name} 已与配置一致，登记应删除")
    assert not stale, "过期登记：\n" + "\n".join(stale)


# --------------------------------------------------------------------------
# C. 单一真源
# --------------------------------------------------------------------------

def test_no_duplicate_config_file():
    """`sp5_out/` 下不得出现配置副本——那正是 `KF` 原先静默切换的入口。"""
    dup = ROOT / "sp5_out" / "sp4_config_v1.json"
    assert not dup.exists(), f"发现重复配置真源：{dup}"


def test_p13_forward_constants_come_from_params(params):
    """前瞻脚本的常量必须等于解析层给出的值。"""
    sys.path.insert(0, str(SP5))
    sys.path.insert(0, str(SP5.parent))
    import p13_forward as pf  # noqa: E402

    assert pf.K == params.n_hold
    assert pf.THRESH == pytest.approx(params.thresh)
    assert pf.LIQ_MAX == pytest.approx(params.liq_max)
    assert pf.AMT_MIN == pytest.approx(params.amt_min)
    assert pf.CLOSE_HI_CFG == pytest.approx(params.price_hi)
    assert pf.CLOSE_HI_MAIN == pytest.approx(sp.KNOWN_DIVERGENCES["price_hi"]["code_value"])
    assert pf.COMM_RATE == pytest.approx(params.commission_rate)
    assert pf.COMM_MIN == pytest.approx(params.min_commission)
    assert pf.TRANSFER == pytest.approx(params.transfer_fee)
    assert pf.STAMP == pytest.approx(params.stamp_duty_sell)
    assert pf.SPREAD == pytest.approx(params.spread_scenario)


def test_p13_forward_reads_the_same_file(params):
    """前瞻用的配置文件与解析层是同一个物理文件。"""
    import p13_forward as pf  # noqa: E402

    assert Path(pf.KF).resolve() == Path(params.config_path).resolve()


def test_p13b_constants_come_from_params(params):
    """回填脚本的常量同样必须等于配置值（它是「第二处」）。"""
    import p13b_forward_track as pb  # noqa: E402

    assert pb.K == params.n_hold
    assert pb.STAMP == pytest.approx(params.stamp_duty_sell)
    assert pb.SPREAD == pytest.approx(params.spread_scenario)
