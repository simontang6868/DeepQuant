"""A9：给 `backtest/fee_presets.py` 补覆盖（改动前为全仓库唯一 0% 模块）。

本文件**只新增测试，不改任何源码**（R7）。覆盖目标：
- 模块级费率常量与 fee_source 常量；
- `FeePreset` 的冻结语义与 `apply()` 的全部行为；
- 三个预设的字段一致性；
- `get_preset()` 的两种命中路径（fee_source / description 前缀）与未命中 KeyError 分支。

红线相关（R4：占位费率 ≠ 真实券商费率）：
本模块里「佣金 / 最低佣金 / 滑点」是**券商决定或研究假设**，不可核实；
只有「印花税 / 过户费」是市场统一规则、可核实。
下面用 `test_verified_fields_*` 把这条边界固化成断言 ——
一旦将来有人把占位值或券商参考值标进 `verified_fields`，测试立刻变红。
这是回归防线，**不修改任何费率数值本身**。
"""

from __future__ import annotations

import dataclasses

import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest import fee_presets as fp


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

def _base_config() -> BacktestConfig:
    """构造一个仅含必填字段的配置，其余走默认值。"""
    return BacktestConfig(symbol="000001", initial_cash=100_000.0)


# --------------------------------------------------------------------------- #
# 模块级常量
# --------------------------------------------------------------------------- #

def test_verified_market_rates():
    """已核实的市场统一规则取值（印花税 0.05% / 过户费 0.001%）。"""
    assert fp.VERIFIED_STAMP_DUTY_RATE == 0.0005
    assert fp.VERIFIED_TRANSFER_FEE_RATE == 0.00001


def test_reference_and_slippage_values():
    """券商参考值与滑点假设取值为正且符合常量定义。"""
    assert fp.REFERENCE_COMMISSION_RATE == 0.00025
    assert fp.REFERENCE_MIN_COMMISSION == 5.0
    assert fp.RESEARCH_SLIPPAGE_BUY == 0.001
    assert fp.RESEARCH_SLIPPAGE_SELL == 0.001
    assert fp.HIGH_COST_SLIPPAGE_BUY == 0.003
    assert fp.HIGH_COST_SLIPPAGE_SELL == 0.003


def test_fee_source_constants_are_distinct_nonempty():
    """四个 fee_source 常量互不相等且非空——它们是溯源标签，不能撞车。"""
    sources = [
        fp.FEE_SOURCE_VERIFIED,
        fp.FEE_SOURCE_REFERENCE,
        fp.FEE_SOURCE_HIGH_COST,
        fp.FEE_SOURCE_SIMULATION,
    ]
    for s in sources:
        assert isinstance(s, str) and s.strip()
    assert len(set(sources)) == 4


# --------------------------------------------------------------------------- #
# FeePreset 语义
# --------------------------------------------------------------------------- #

def test_fee_preset_is_frozen_dataclass():
    """FeePreset 必须是 frozen dataclass —— 预设是共享常量，禁止就地改。"""
    assert dataclasses.is_dataclass(fp.FeePreset)
    with pytest.raises(dataclasses.FrozenInstanceError):
        fp.FEE_PRESET_REFERENCE.commission_rate = 0.5


def test_all_presets_contents_and_order():
    """ALL_PRESETS 含全部三个预设，顺序固定、无重复。"""
    assert len(fp.ALL_PRESETS) == 3
    assert fp.ALL_PRESETS == (
        fp.FEE_PRESET_SIMULATION,
        fp.FEE_PRESET_REFERENCE,
        fp.FEE_PRESET_HIGH_COST,
    )
    assert len({id(p) for p in fp.ALL_PRESETS}) == 3


def test_each_preset_has_complete_metadata():
    """每个预设的费率字段非空负、fee_source 与 description 均有效。"""
    for p in fp.ALL_PRESETS:
        assert p.commission_rate >= 0
        assert p.min_commission >= 0
        assert p.stamp_duty_rate >= 0
        assert p.transfer_fee_rate >= 0
        assert p.slippage_buy >= 0
        assert p.slippage_sell >= 0
        assert p.fee_source.strip()
        assert p.description.strip()


def test_high_cost_preset_is_strictly_costlier_than_reference():
    """高成本情景的佣金与滑点必须严格高于参考情景（否则情景失去意义）。"""
    assert fp.FEE_PRESET_HIGH_COST.commission_rate > fp.FEE_PRESET_REFERENCE.commission_rate
    assert fp.FEE_PRESET_HIGH_COST.slippage_buy > fp.FEE_PRESET_REFERENCE.slippage_buy
    assert fp.FEE_PRESET_HIGH_COST.slippage_sell > fp.FEE_PRESET_REFERENCE.slippage_sell


def test_simulation_preset_differs_from_reference_on_transfer_fee():
    """占位预设的过户费为 0（非真实），与参考预设不同。"""
    assert fp.FEE_PRESET_SIMULATION.transfer_fee_rate == 0.0
    assert fp.FEE_PRESET_REFERENCE.transfer_fee_rate == fp.VERIFIED_TRANSFER_FEE_RATE


# --------------------------------------------------------------------------- #
# R4 回归防线：verified_fields 的诚实性
# --------------------------------------------------------------------------- #

def test_verified_fields_simulation_claims_nothing():
    """占位预设不得声称任何字段已核实（R4）。"""
    assert fp.FEE_PRESET_SIMULATION.verified_fields == ()
    assert fp.FEE_PRESET_SIMULATION.fee_source == fp.FEE_SOURCE_SIMULATION


def test_verified_fields_reference_and_high_cost():
    """参考 / 高成本预设只把印花税与过户费标为已核实。"""
    expected = ("stamp_duty_rate", "transfer_fee_rate")
    assert fp.FEE_PRESET_REFERENCE.verified_fields == expected
    assert fp.FEE_PRESET_HIGH_COST.verified_fields == expected


@pytest.mark.parametrize("preset", list(fp.ALL_PRESETS))
def test_verified_fields_never_include_broker_or_assumption_fields(preset):
    """R4 核心防线：佣金 / 最低佣金 / 滑点永远不得出现在 verified_fields。

    佣金与最低佣金由券商决定，滑点是研究假设，三者都不可核实。
    """
    broker_or_assumption = {
        "commission_rate",
        "min_commission",
        "slippage_buy",
        "slippage_sell",
    }
    assert not (broker_or_assumption & set(preset.verified_fields))


# --------------------------------------------------------------------------- #
# FeePreset.apply()
# --------------------------------------------------------------------------- #

def test_apply_returns_new_config_of_same_type():
    """apply 返回新实例（frozen 语义），类型与入参一致。"""
    cfg = _base_config()
    out = fp.FEE_PRESET_REFERENCE.apply(cfg)
    assert out is not cfg
    assert type(out) is BacktestConfig


def test_apply_sets_all_fee_fields_from_preset():
    """apply 把预设的 6 个费率 + fee_source 全部写进新 config。"""
    cfg = _base_config()
    preset = fp.FEE_PRESET_HIGH_COST
    out = preset.apply(cfg)
    assert out.commission_rate == preset.commission_rate
    assert out.min_commission == preset.min_commission
    assert out.stamp_duty_rate == preset.stamp_duty_rate
    assert out.transfer_fee_rate == preset.transfer_fee_rate
    assert out.slippage_buy == preset.slippage_buy
    assert out.slippage_sell == preset.slippage_sell
    assert out.fee_source == preset.fee_source


def test_apply_preserves_non_fee_fields():
    """apply 只改费率，不触碰标的 / 资金 / 区间 / 执行价 / 信号延迟 / 市场规则。"""
    cfg = BacktestConfig(
        symbol="600519",
        initial_cash=250_000.0,
        start_date=None,
        end_date=None,
    )
    out = fp.FEE_PRESET_REFERENCE.apply(cfg)
    assert out.symbol == cfg.symbol
    assert out.initial_cash == cfg.initial_cash
    assert out.start_date == cfg.start_date
    assert out.end_date == cfg.end_date
    assert out.exec_price_rule == cfg.exec_price_rule
    assert out.signal_lag == cfg.signal_lag
    assert out.market_rules is cfg.market_rules


def test_apply_appends_exactly_one_assumption_line():
    """apply 在 assumptions 末尾追加且仅追加一条 fee_preset 溯源行。"""
    cfg = _base_config()
    before = cfg.assumptions
    out = fp.FEE_PRESET_REFERENCE.apply(cfg)
    assert len(out.assumptions) == len(before) + 1
    added = out.assumptions[-1]
    assert added.startswith("fee_preset=")
    assert fp.FEE_PRESET_REFERENCE.fee_source in added
    assert fp.FEE_PRESET_REFERENCE.description in added
    # 原有 assumptions 逐条保留在前面（不覆盖、不重排）
    assert out.assumptions[:-1] == before


def test_apply_does_not_mutate_original_config():
    """apply 是纯函数：原 config 的费率与 assumptions 均不变。"""
    cfg = _base_config()
    original_rates = (
        cfg.commission_rate,
        cfg.min_commission,
        cfg.stamp_duty_rate,
        cfg.transfer_fee_rate,
        cfg.slippage_buy,
        cfg.slippage_sell,
        cfg.fee_source,
    )
    original_assumptions = cfg.assumptions
    _ = fp.FEE_PRESET_HIGH_COST.apply(cfg)
    assert (
        cfg.commission_rate,
        cfg.min_commission,
        cfg.stamp_duty_rate,
        cfg.transfer_fee_rate,
        cfg.slippage_buy,
        cfg.slippage_sell,
        cfg.fee_source,
    ) == original_rates
    assert cfg.assumptions == original_assumptions


@pytest.mark.parametrize("preset", list(fp.ALL_PRESETS))
def test_apply_works_for_every_preset(preset):
    """三个预设都能成功 apply，且结果的 fee_source 与预设一致。"""
    out = preset.apply(_base_config())
    assert out.fee_source == preset.fee_source
    assert out.assumptions[-1].startswith("fee_preset=")


def test_apply_is_repeatable_and_idempotent_on_fee_fields():
    """对同一 config 连续 apply 两次，费率字段结果一致（assumptions 会累积）。"""
    cfg = _base_config()
    once = fp.FEE_PRESET_REFERENCE.apply(cfg)
    twice = fp.FEE_PRESET_REFERENCE.apply(once)
    assert twice.commission_rate == once.commission_rate
    assert twice.fee_source == once.fee_source
    assert len(twice.assumptions) == len(once.assumptions) + 1


# --------------------------------------------------------------------------- #
# get_preset()
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "source,expected",
    [
        (fp.FEE_SOURCE_SIMULATION, fp.FEE_PRESET_SIMULATION),
        (fp.FEE_SOURCE_REFERENCE, fp.FEE_PRESET_REFERENCE),
        (fp.FEE_SOURCE_HIGH_COST, fp.FEE_PRESET_HIGH_COST),
    ],
)
def test_get_preset_by_fee_source(source, expected):
    """按 fee_source 精确命中。"""
    assert fp.get_preset(source) is expected


def test_get_preset_by_description_prefix():
    """按 description 前缀命中（get_preset 的第二条匹配路径）。"""
    prefix = fp.FEE_PRESET_SIMULATION.description[:10]
    assert fp.get_preset(prefix) is fp.FEE_PRESET_SIMULATION


def test_get_preset_unknown_raises_keyerror_with_name():
    """未命中时抛 KeyError，且错误信息里带原查询名。"""
    missing = "这个预设根本不存在"
    with pytest.raises(KeyError) as exc:
        fp.get_preset(missing)
    assert missing in str(exc.value)


def test_get_preset_empty_string_matches_first_preset():
    """记录既有行为（不修源码，修 bug 属判断类）：空串会命中**第一个**预设。

    原因：`p.description.startswith("")` 对任何 description 恒为真，
    故循环在第一个预设就返回，不会走到 KeyError 分支。
    此处如实固化该边界行为，作为将来若决定收紧入参校验时的基线证据。
    """
    assert fp.get_preset("") is fp.FEE_PRESET_SIMULATION
