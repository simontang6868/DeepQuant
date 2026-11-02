#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SP-4 策略参数的**唯一解析层**。

为什么需要这个模块（任务书 Phase 1.1）：

    原文：「策略参数只在一处定义，回测、前瞻、实盘都从它读取。新增一致性测试：
           三处解析出的参数逐字段相等，不等则测试失败。」

改动前的实际状况（已实测）：

    * `forward/sp4_config_v1.json` 是冻结配置，但**阈值写在自然语言里**
      （`"f3pct <= 0.70"`、`"amt20 全市场分位 < 20%"`、`"amt20 > 500 万"`），
      没有任何一处代码真的读它们；
    * `p13_forward.py` 自己硬编码 `K=5, THRESH=0.70, LIQ_MAX=0.20, AMT_MIN=5e6`；
    * `scripts/audit/audit_sp4_*.py` 另有 **6 个文件**各自写
      `K, THRESH, LIQ_MAX = 5, 0.70, 0.20`；
    * `scripts/sp2_sell_signal.py` 再写一遍。

    ⇒ 「配置」与「代码」是两套并存的真源，改一处另一处不会动。这正是本模块要消掉的东西。

设计取舍：

1. **不修改冻结配置**。`sp4_config_v1.json` 冻结于 2026-10-26，改它属 amendment。
   所以本模块负责把自然语言字段**解析**成数值，而不是要求配置改成数值。
2. **解析失败必须报错**，不允许静默回退到默认值。正则失配意味着配置格式变了，
   此时静默用 0.70 会让「参数漂移」彻底不可见。
3. **已知偏差显式登记**（见 ``KNOWN_DIVERGENCES``）。`price_hi` 配置写 50、
   回测实跑 200，是已登记的缺陷 `CONFIG_PRICE_FILTER_MISMATCH`，
   由 Phase 1.2 的 amendment 处理；本模块只暴露事实，不擅自统一。
4. **不允许本模块以外再硬编码策略参数**。`tests/test_strategy_params.py`
   会用 AST 扫描全部脚本，发现新的硬编码即刻失败。

用法：

    from strategy_params import load_params
    p = load_params()           # 默认读 forward/sp4_config_v1.json
    p.k, p.thresh, p.liq_max, p.amt_min, p.price_lo, p.price_hi
    p.as_record()               # 便于落盘与逐字段比对
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 配置版本。变更时必须同步本常量，否则 `load_params` 报错。
PARAMS_VERSION = "sp4-v1"

#: 本仓库里 SP-4 配置的唯一位置（相对 `research/stock_pick_2026-10-25`）。
DEFAULT_CONFIG = Path("forward") / "sp4_config_v1.json"

#: 已知的「配置 vs 代码」偏差。**这不是许可证，是待办清单。**
#: 每一条都必须能被指出对应的缺陷编号与处理阶段；不允许往里塞「看起来差不多」的项。
KNOWN_DIVERGENCES: dict[str, dict[str, Any]] = {
    "price_hi": {
        "config_value": 50.0,
        "code_value": 200.0,
        "issue": "CONFIG_PRICE_FILTER_MISMATCH",
        "evidence": "sp5_out/p13c_rule_diff_check.json：同一月末 50 与 200 口径 top5 重合 0/5",
        "handling": "Phase 1.2 amendment 并列报告；本阶段**不**统一",
    },
}


class ConfigError(RuntimeError):
    """配置缺失、格式不符、或解析失败。"""


@dataclass(frozen=True)
class StrategyParams:
    """解析后的策略参数。字段与配置一一对应，便于逐字段比对。"""

    version: str
    config_sha256: str
    config_path: str

    # ---- 选股 ----
    n_hold: int
    thresh: float            # f3pct <= thresh 才可买
    liq_max: float           # amt20 分位 < liq_max
    amt_min: float           # amt20 > amt_min（元）
    price_lo: float
    price_hi: float          # **配置口径**（50）；回测实际用 200，见 KNOWN_DIVERGENCES

    # ---- 组合 ----
    stock_weight: float
    cash_yield_annual: float

    # ---- 成本 ----
    commission_rate: float
    min_commission: float
    stamp_duty_sell: float
    transfer_fee: float
    spread_scenario: float

    # ---- 原始文本（供审计追溯，不参与计算）----
    raw_text_fields: dict[str, str] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        """扁平化，用于落盘与逐字段比对。"""
        return {
            "version": self.version,
            "config_sha256": self.config_sha256,
            "n_hold": self.n_hold,
            "thresh": self.thresh,
            "liq_max": self.liq_max,
            "amt_min": self.amt_min,
            "price_lo": self.price_lo,
            "price_hi": self.price_hi,
            "stock_weight": self.stock_weight,
            "cash_yield_annual": self.cash_yield_annual,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_duty_sell": self.stamp_duty_sell,
            "transfer_fee": self.transfer_fee,
            "spread_scenario": self.spread_scenario,
        }


# --------------------------------------------------------------------------
# 自然语言阈值的解析
# --------------------------------------------------------------------------

_RE_F3 = re.compile(r"f3pct\s*<=\s*([0-9]*\.?[0-9]+)")
_RE_PCT_LT = re.compile(r"<\s*([0-9]*\.?[0-9]+)\s*%")
_RE_WAN = re.compile(r">\s*([0-9]*\.?[0-9]+)\s*万")


def _require(text: str, pattern: re.Pattern[str], field_name: str, expect: str) -> float:
    """从自然语言里取一个数。**取不到就报错**，绝不回退默认值。

    静默回退是这类解析器最危险的失败模式：配置改成 `f3pct <= 0.6` 而正则失配时，
    程序会继续用旧值 0.70，且没有任何提示。
    """
    m = pattern.search(text or "")
    if not m:
        raise ConfigError(
            f"字段 {field_name} 无法解析出数值：期望形如 {expect}，实际是 {text!r}。"
            "配置格式变了就必须同步更新解析器，不能让它静默用回退值。"
        )
    return float(m.group(1))


def _need(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise ConfigError(f"{where} 缺少必需字段 {key!r}")
    return d[key]


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------

def load_params(config_path: Path | str | None = None,
                base: Path | None = None) -> StrategyParams:
    """读取并解析配置。所有策略参数**必须**经此处取得。

    ``base`` 用于测试时指定根目录；默认按本文件位置向上两级推断
    （`scripts/sp5/strategy_params.py` → `research/stock_pick_2026-10-25`）。
    """
    if base is None:
        base = Path(__file__).resolve().parents[2]
    path = Path(config_path) if config_path is not None else Path(base) / DEFAULT_CONFIG
    if not path.is_absolute():
        path = Path(base) / path
    if not path.exists():
        raise ConfigError(f"配置不存在：{path}")

    raw_bytes = path.read_bytes()
    # ⚠️ 统一按 LF 归一后再算哈希：本仓库在 Windows 上会把 \n 写成 \r\n，
    #    而 forward/sp4_config_v1.sha256 记录的是写入时的字节。两者都要能对上，
    #    所以这里同时保留 raw 与 lf 两个值，由调用方按用途取。
    sha_raw = hashlib.sha256(raw_bytes).hexdigest()
    sha_lf = hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest()

    try:
        doc = json.loads(raw_bytes.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置不是合法 JSON：{exc}") from exc

    version = _need(doc, "version", "配置根部")
    if version != PARAMS_VERSION:
        raise ConfigError(
            f"配置版本 {version!r} 与本模块声明的 {PARAMS_VERSION!r} 不一致。"
            "版本变了就必须一起改解析逻辑，否则字段语义可能已不同。"
        )

    st = _need(doc, "strategy", "配置")
    cost = _need(doc, "cost", "配置")

    pf = _need(st, "price_filter", "strategy")
    if not (isinstance(pf, (list, tuple)) and len(pf) == 2):
        raise ConfigError(f"strategy.price_filter 必须是 [下限, 上限]，实际 {pf!r}")

    liq_text = _need(st, "liquidity_filter", "strategy")
    band_text = _need(st, "band", "strategy")
    f3_text = _need(st, "f3_filter_entry", "strategy")

    amt_min = _require(liq_text, _RE_WAN, "strategy.liquidity_filter",
                       "amt20 > 500 万") * 1e4
    liq_max = _require(band_text, _RE_PCT_LT, "strategy.band",
                       "amt20 全市场分位 < 20%") / 100.0
    thresh = _require(f3_text, _RE_F3, "strategy.f3_filter_entry", "f3pct <= 0.70")

    return StrategyParams(
        version=version,
        config_sha256=sha_raw,
        config_path=str(path),
        n_hold=int(_need(st, "n_hold", "strategy")),
        thresh=thresh,
        liq_max=liq_max,
        amt_min=amt_min,
        price_lo=float(pf[0]),
        price_hi=float(pf[1]),
        stock_weight=float(_need(st, "stock_weight", "strategy")),
        cash_yield_annual=float(_need(st, "cash_yield_annual", "strategy")),
        commission_rate=float(_need(cost, "commission_rate", "cost")),
        min_commission=float(_need(cost, "min_commission", "cost")),
        stamp_duty_sell=float(_need(cost, "stamp_duty_sell", "cost")),
        transfer_fee=float(_need(cost, "transfer_fee", "cost")),
        spread_scenario=float(_need(cost, "spread_scenario", "cost")),
        raw_text_fields={
            "liquidity_filter": liq_text,
            "band": band_text,
            "f3_filter_entry": f3_text,
        },
    )


def sha256_lf(path: Path | str) -> str:
    """LF 归一的 sha256。`forward/sp4_config_v1.sha256` 用的是这个口径。"""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def config_sha_declared(base: Path | None = None) -> str:
    """读 `forward/sp4_config_v1.sha256` 声明的哈希（取第一列）。"""
    if base is None:
        base = Path(__file__).resolve().parents[2]
    p = Path(base) / "forward" / "sp4_config_v1.sha256"
    if not p.exists():
        raise ConfigError(f"缺 {p}，无法校验冻结配置")
    return p.read_text(encoding="utf-8").split()[0]
