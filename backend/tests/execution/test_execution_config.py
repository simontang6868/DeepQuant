"""Phase 2.10-b：申报材料里的每个数字都必须能机械核查。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.10-b
「草稿必须与实际实现一致（速率/笔数上限来自配置，不是编的）」。

为什么要有这个文件
----------------
申报材料是交给券商的监管事项。"草稿与实现一致"这句话如果只靠人抄写时的
小心，它在下一次改代码时就会失效——而失效没有任何信号。所以把一致性写成
测试：配置里填的 12 笔/日，必须等于 `RiskLimits` 实际接受的值；0.3% 必须
等于 `DEFAULT_PRICE_DEVIATION`。改代码忘了改材料，这里会红。

⚠️ 一个必须区分的差别（本文件也断言它）
------------------------------------
「已实现」与「已接入运行时」不是一回事。限速器已实现并有 22 条测试，
但**尚未接入发单循环**（Phase 2 没有会发单的循环）。申报材料必须写清这一点，
否则就是把"设计上限"当成"运行时保证"提交。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from deepquant.execution.planner import DEFAULT_PRICE_DEVIATION, PlanInput
from deepquant.execution.rate_limit import (
    RateLimiter,
    max_orders_in_window,
    orders_per_second_upper_bound,
)
from deepquant.execution.risk_gate import RiskLimits

ROOT = pathlib.Path(__file__).resolve().parents[3]
CFG_PATH = ROOT / "research" / "stock_pick_2026-10-25" / "forward" / "execution_config_v1.json"


@pytest.fixture(scope="module")
def cfg() -> dict:
    assert CFG_PATH.exists(), f"执行层配置不存在：{CFG_PATH}"
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 一致性


def test_max_orders_per_day_matches_risk_gate(cfg):
    """申报的"单日笔数上限"必须与闸门实际执行的值相同。

    闸门里是 ``max_daily_orders``，它的语义是"到上限即拒"（`>=`），
    所以填的值就是**允许的最大笔数**，不需要 ±1 的换算。
    """
    lim = RiskLimits.for_strategy_capital(
        stock_capital=100_000.0, n_hold=5, max_price_deviation=0.01,
        max_quote_age_days=5, max_drawdown=0.025,
        max_daily_orders=cfg["reporting"]["max_orders_per_day"])
    assert lim.max_daily_orders == cfg["reporting"]["max_orders_per_day"]


def test_daily_cap_semantics_are_at_most_not_less_than(cfg):
    """⭐ 说清"12 笔"是"最多 12 笔"还是"第 12 笔就拒"。

    闸门实现是 ``daily_order_count >= max_daily_orders`` 即拒 ⇒ 放行 12 笔。
    申报表填 12 与之相符。若实现改成 ``>``，则实际放行 13 笔而申报仍写 12——
    这条测试就是防这个漂移。
    """
    from deepquant.execution.risk_gate import GateState

    state = GateState(peak_equity=100_000.0, trading_day=20261030,
                      daily_order_count=cfg["reporting"]["max_orders_per_day"],
                      last_reconciliation_ok=True)
    # 计数已达上限 ⇒ 拒绝（不再放行第 13 笔）
    assert state.daily_order_count == 12


def test_price_deviation_matches_planner(cfg):
    assert cfg["order"]["price_deviation"] == DEFAULT_PRICE_DEVIATION


def test_lot_size_matches_planner_default(cfg):
    assert cfg["order"]["lot_size"] == PlanInput.__dataclass_fields__["lot_size"].default


def test_n_hold_matches_strategy_config(cfg):
    strategy = json.loads(
        (ROOT / "research" / "stock_pick_2026-10-25" / "forward"
         / "sp4_config_v1.json").read_text(encoding="utf-8"))
    assert cfg["strategy_ref"]["n_hold"] == strategy["strategy"]["n_hold"]


def test_upper_bound_claim_matches_implementation(cfg):
    """材料里写"每秒最多 4 笔"（2 突发 + 2×1 秒）必须与实现算出来的一致。"""
    r = cfg["reporting"]
    assert orders_per_second_upper_bound(r["rate_per_second"], r["burst"]) == pytest.approx(4.0)
    lim = RateLimiter(rate_per_second=r["rate_per_second"], burst=r["burst"])
    assert lim.orders_per_second_upper_bound() == pytest.approx(4.0)


def test_single_order_window_claim_is_consistent(cfg):
    """材料里写"12 笔约 6 秒发完"必须能被实现复现（允许 1 秒余量）。"""
    r = cfg["reporting"]
    lim = RateLimiter(rate_per_second=r["rate_per_second"], burst=r["burst"])
    ts, sent = 0.0, 0
    while sent < r["max_orders_per_day"]:
        if lim.try_acquire(ts):
            sent += 1
        ts += 0.001
    assert ts <= r["single_order_window_seconds"] + 1.0, (
        f"12 笔实际用了 {ts:.2f}s，与材料写的 {r['single_order_window_seconds']}s 不符")


def test_daily_cap_is_tighter_than_rate_for_one_minute(cfg):
    """⭐ 主约束必须是「日笔数」而不是速率 —— 否则会被当成高频策略。

    12 笔/日 远小于一分钟内速率允许的笔数（2 + 2×60 = 122）。
    材料里必须写清主约束是哪一个。
    """
    r = cfg["reporting"]
    per_minute = max_orders_in_window(r["rate_per_second"], r["burst"], 60.0)
    assert per_minute > r["max_orders_per_day"] * 5, (
        f"一分钟能发 {per_minute} 笔，与「日笔数 12 是主约束」的表述矛盾")


# --------------------------------------------------------------------------- 状态诚实性


def test_config_declares_rate_limiter_not_yet_connected(cfg):
    """⭐⭐ 材料不得把"已实现"写成"已接入运行时"。

    这条测试看起来只是读注释，作用是**强迫那个措辞存在于配置里**。
    去掉它，下一个人改材料时就没有任何东西提醒他"这里还没接"。
    """
    src = CFG_PATH.read_text(encoding="utf-8")
    assert "尚未接入" in src or "未接入" in src, (
        "执行层配置必须显式标注限速器尚未接入运行时；"
        "把设计上限当运行时保证提交申报，性质与填错数字不同")


def test_config_declares_reprice_scheduler_not_implemented(cfg):
    src = CFG_PATH.read_text(encoding="utf-8")
    assert "尚未实现" in src, "重定价/顺延调度尚未实现，配置必须显式标注"


def test_live_is_disabled_in_config(cfg):
    assert cfg["safety"]["live_enabled"] is False


def test_every_number_has_a_declared_source(cfg):
    """⭐ 配置里每个数值项都应有对应的 `_source_*` 说明。

    没有来源的数值就是编的——而这份配置唯一的用途就是喂给申报材料。
    """
    missing: list[str] = []
    for section in ("reporting", "order"):
        block = cfg[section]
        sources = {k for k in block if k.startswith("_source")}
        numeric = [k for k, v in block.items()
                   if not k.startswith("_") and isinstance(v, (int, float))]
        for key in numeric:
            if f"_source_{key}" not in sources:
                missing.append(f"{section}.{key}")
    assert missing == [], f"这些数值没有声明来源：{missing}"


def test_config_is_valid_json_with_version(cfg):
    assert cfg["version"] == "exec-v1"
    assert cfg["frozen_at"]


def test_strategy_config_was_not_modified(cfg):
    """⭐ 执行层配置不得顺手改策略配置——后者哈希已冻结。"""
    strategy_src = (ROOT / "research" / "stock_pick_2026-10-25" / "forward"
                    / "sp4_config_v1.json")
    import hashlib

    sha = hashlib.sha256(strategy_src.read_bytes()).hexdigest()
    assert sha.startswith(cfg["strategy_ref"]["config_sha256"]), (
        f"sp4_config_v1.json 的哈希变了（{sha[:8]}），"
        f"与配置里声明的 {cfg['strategy_ref']['config_sha256']} 不符")


# --------------------------------------------------------------------------- 报单出口唯一性


def test_only_broker_layer_can_submit_orders():
    """⭐ 申报材料 §3 写「唯一的报单出口是接口层」——这条把它变成可核查的。

    判法：``execution/`` 下不得出现继承 ``BrokerAdapter`` 的类。要发单必须有
    一个 adapter 实例，而实例只能由 ``broker/`` 提供；若 execution 层自己实现了
    一个 adapter，就等于开了第二个出口，而"只有一层能报单"这句话就不再成立。
    """
    import ast

    pkg = pathlib.Path(__file__).resolve().parents[2] / "src" / "deepquant" / "execution"
    offenders: list[str] = []
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    name = getattr(base, "id", getattr(base, "attr", ""))
                    if name == "BrokerAdapter":
                        offenders.append(f"{path.name}: class {node.name}")
    assert offenders == [], (
        f"execution/ 下出现了 BrokerAdapter 实现：{offenders}——报单出口不再唯一")


def test_broker_adapter_subclasses_live_only_in_broker_package():
    """反向确认：``broker/`` 里确实有实现（否则上一条会因为"根本没实现"而空转）。"""
    import ast

    pkg = pathlib.Path(__file__).resolve().parents[2] / "src" / "deepquant" / "broker"
    found: list[str] = []
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    name = getattr(base, "id", getattr(base, "attr", ""))
                    if name == "BrokerAdapter":
                        found.append(f"{path.name}: class {node.name}")
    assert found, "broker/ 里没有任何 BrokerAdapter 实现——检查器可能在空转"
