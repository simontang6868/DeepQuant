"""Gate 1 开跑前的「证据不足触发项可达性」审计（只读，**不算任何收益**）。

预登记 §10.2 末尾列了三条会把结论降级为「证据不足」的触发项，每条都写了一个
判定方式。本脚本只回答一个问题：**这三条判定，在本装置上究竟能不能被执行、
执行出来有没有信息量。**

为什么这件事必须在开跑前做、而不能跑完再说
--------------------------------------------
如果等看到 `mean(d)` 再回头检查「触发项是否可执行」，那么「这条不可执行」本身
就成了一个可以按结果挑选的解读。反过来，先证明某条触发项的结构性质，结论就只能
照着这个结构走。

本脚本**不调用 `run_portfolio_backtest`、不产生任何收益率、不创建 `gate1_out/`**。
它只做目标组合层面的比较（选股名单），以及从行情里读价格做算术。
输出 JSON 里 `will_compute_returns` 恒为 `false`，并有测试守着这一点。

三个探针
--------
T1（创业板/科创板 2017~2020 移出候选池）
    - ``t1a_oos_lookback_boundary``：扰动 2021 年前的全部价格，OOS 目标是否变化。
      若为 0，说明 OOS 决策只依赖近端 ``lookback+skip`` 个交易日，**IS 期的池子
      怎么改都进不了 OOS 判据**——触发项在结构上就不可能触发。
    - ``t1b_literal_exclusion``：按字面读法真的把双创 2017~2020 数据删掉，数一数
      OOS 48 个月里有几个月目标变了。变了的那几个月，机制是「少了回看数据」，
      不是「涨跌停约束」。
    - ``t1c_window_mismatch``：触发项要移出的区间（2017~2020）恰好是 flat 10%
      **判定正确**的区间；flat 10% 判错的区间（2020-08-24 起）反而被保留，
      而 OOS 48 个月全在这段里。数一数那段里双创占了多少仓位。

T2（`600809.SH` 数据缺陷影响能否隔离）
    - 数该股在 OOS 目标里出现了几个月，以及 100 万本金下 1 手是否买得起。
      若从未出现 → 排除版与包含版**必然**给出相同结果 → 触发项只会自动判
      「影响可隔离」，而真实答案是「本装置未检验」。

T3（成本档提高到 high_cost 后 `mean(d)` 由正转负）
    - 只能靠真跑判定，本脚本只记录两档费率的**参数差**，并标 ``requires_run``。

用法::

    python scripts/gate1_trigger_reachability.py
    python scripts/gate1_trigger_reachability.py --out-root probe_out
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
for _p in (_SCRIPTS, _REPO / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# gate1_run 不是包内模块，用文件路径加载，避免 `import scripts.gate1_run` 依赖 __init__
_spec = importlib.util.spec_from_file_location("gate1_run_for_probe", _SCRIPTS / "gate1_run.py")
gate1_run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gate1_run
_spec.loader.exec_module(gate1_run)

from deepquant.backtest.cross_section import (  # noqa: E402
    CrossSectionConfig,
    CrossSectionPanel,
    trading_calendar,
)
from deepquant.backtest.fee_presets import get_preset  # noqa: E402


OOS_START = gate1_run.OOS_START
OOS_END = gate1_run.OOS_END
INITIAL_CASH = gate1_run.INITIAL_CASH
GEM_REGIME_START = "2020-08-24"   # 创业板涨跌幅由 10% 改为 20% 的生效日
PRICE_PERTURB_CUTOFF = "2020-06-30"  # 早于该日的数据若影响 OOS，说明存在长程依赖
LITERAL_CUTOFF = "2021-01-01"     # 「2017~2020 移出候选池」的字面边界


@dataclass(frozen=True)
class ProbeConfig:
    """探针统一用主实验口径，避免「换个窗口再比较」这类隐性自由度。"""

    lookback: int = 60
    skip: int = 5
    top_fraction: float = 0.2


# --------------------------------------------------------------------------- #
# 基础构件（纯函数，测试直接打这些）
# --------------------------------------------------------------------------- #


def gem_symbols(prices: Mapping[str, pd.DataFrame]) -> list[str]:
    """按代码前缀判定双创标的（前缀是规则，行业/名称不是）。"""
    return sorted(c for c in prices if c[:3] in gate1_run.GEM_PREFIXES)


def build_targets(
    prices: Mapping[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
    config: ProbeConfig = ProbeConfig(),
) -> dict[pd.Timestamp, dict[str, float]]:
    """信号日 → 目标权重。只做选股，不碰撮合，因此不产生任何收益数字。"""
    panel = CrossSectionPanel(
        prices,
        config=CrossSectionConfig(lookback=config.lookback, skip=config.skip),
        calendar=calendar,
    )
    signal_dates = gate1_run.month_end_signal_dates(calendar)
    scores = panel.scores_by_date(signal_dates)
    targets = gate1_run.build_targets(
        scores, top_fraction=config.top_fraction, weighting="equal"
    )
    return {pd.Timestamp(t.signal_date): dict(t.weights) for t in targets}


def oos_keys(targets: Mapping[pd.Timestamp, Mapping[str, float]]) -> list[pd.Timestamp]:
    return sorted(d for d in targets if OOS_START <= str(d.date()) <= OOS_END)


def changed_months(
    base: Mapping[pd.Timestamp, Mapping[str, float]],
    other: Mapping[pd.Timestamp, Mapping[str, float]],
    keys: Sequence[pd.Timestamp],
) -> list[dict[str, object]]:
    """逐月比对**标的集合**（等权口径下权重恒定，集合相同即权重相同）。"""
    out = []
    for d in keys:
        a, b = set(base.get(d, {})), set(other.get(d, {}))
        if a != b:
            out.append({
                "signal_date": str(d.date()),
                "only_in_base": sorted(a - b),
                "only_in_variant": sorted(b - a),
            })
    return out


def perturb_before(
    prices: Mapping[str, pd.DataFrame], cutoff: str, factor: float
) -> dict[str, pd.DataFrame]:
    """把 cutoff 之前的四价整体乘以 factor。

    用途是**证伪长程依赖**：若 OOS 目标不变，说明决策只吃近端窗口；
    若变了，说明有跨越 cutoff 的依赖（那就必须先讲清楚是什么）。
    """
    cut = pd.Timestamp(cutoff)
    out: dict[str, pd.DataFrame] = {}
    for code, df in prices.items():
        v = df.copy()
        mask = v["date"] < cut
        for col in ("open", "high", "low", "close"):
            v.loc[mask, col] = v.loc[mask, col] * factor
        out[code] = v
    return out


def drop_rows_before(
    prices: Mapping[str, pd.DataFrame], symbols: Sequence[str], cutoff: str
) -> dict[str, pd.DataFrame]:
    """把指定标的 cutoff 之前的行整段删除（字面意义的「移出候选池」）。"""
    cut = pd.Timestamp(cutoff)
    target = set(symbols)
    out: dict[str, pd.DataFrame] = {}
    for code, df in prices.items():
        if code in target:
            out[code] = df[df["date"] >= cut].reset_index(drop=True)
        else:
            out[code] = df
    return out


def symbol_reachability(
    targets: Mapping[pd.Timestamp, Mapping[str, float]], code: str
) -> dict[str, object]:
    """某标的在 OOS 目标里出现的月份数。

    为 0 意味着「包含/排除」两版的 OOS 目标完全一致 → 收益必然一致 →
    符号必不翻转 → 触发项自动判「影响可隔离」，而这是**未检验**，不是证据。
    """
    keys = oos_keys(targets)
    months = [str(d.date()) for d in keys if code in targets[d]]
    return {
        "symbol": code,
        "oos_target_months": len(keys),
        "held_months": len(months),
        "first_held": months[0] if months else None,
        "last_held": months[-1] if months else None,
        "can_flip_sign": bool(months),
    }


def lot_affordability(
    df: pd.DataFrame, *, initial_cash: float, n_names: int, start: str, end: str
) -> dict[str, object]:
    """OOS 区间内 1 手成本 vs 单只预算（100 股整手）。

    ``n_names`` 必须传**最不利侧**（OOS 内最大持仓数：持仓越多，单只预算越小）。
    用最小持仓数会让预算虚高，从而把「某些月份其实买不起」判成「买得起」。
    """
    window = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    if window.empty:
        return {"ok": False, "reason": "OOS 区间无行情"}
    lots = (window["close"].astype(float) * 100).round(2)
    budget = initial_cash / n_names
    return {
        "lot_cost_min": float(lots.min()),
        "lot_cost_max": float(lots.max()),
        "budget_per_name": float(budget),
        "affordable_always": bool(lots.max() <= budget),
        "affordable_never": bool(lots.min() > budget),
    }


def fee_preset_delta(main: str, high: str) -> dict[str, object]:
    """两档费率的参数差（只读常量，不跑回测）。"""
    a, b = get_preset(main), get_preset(high)
    fields = (
        "commission_rate",
        "min_commission",
        "stamp_duty_rate",
        "transfer_fee_rate",
        "slippage_buy",
        "slippage_sell",
    )
    return {
        "main": {f: getattr(a, f) for f in fields},
        "high": {f: getattr(b, f) for f in fields},
        "changed": [f for f in fields if getattr(a, f) != getattr(b, f)],
        "same": [f for f in fields if getattr(a, f) == getattr(b, f)],
    }


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def build_report(
    prices: Mapping[str, pd.DataFrame],
    *,
    data_root: Path,
    config: ProbeConfig = ProbeConfig(),
) -> dict[str, object]:
    calendar = trading_calendar(prices)
    base = build_targets(prices, calendar, config)
    keys = oos_keys(base)
    gem = gem_symbols(prices)

    # -------- T1a：OOS 是否只依赖近端窗口 --------
    perturbed = build_targets(perturb_before(prices, PRICE_PERTURB_CUTOFF, 1.10), calendar, config)
    t1a_diff = changed_months(base, perturbed, keys)

    # -------- T1b：字面读法下 OOS 实际被改动的月份 --------
    literal = build_targets(drop_rows_before(prices, gem, LITERAL_CUTOFF), calendar, config)
    t1b_diff = changed_months(base, literal, keys)

    # -------- T1c：flat 10% 判错的区间里，双创占多少仓位 --------
    gem_set = set(gem)
    regime_months = [d for d in keys if str(d.date()) >= GEM_REGIME_START]
    months_with_gem = [d for d in regime_months if gem_set & set(base[d])]
    slots = sum(len(base[d]) for d in regime_months)
    gem_slots = sum(len(gem_set & set(base[d])) for d in regime_months)

    # -------- T2：600809 / 000568 的可达性 --------
    holdings = [len(base[d]) for d in keys if base[d]]
    min_names = min(holdings) if holdings else 1
    max_names = max(holdings) if holdings else 1
    reach = {}
    for code in ("600809.SH", "000568.SZ"):
        info = symbol_reachability(base, code)
        if code in prices:
            info["lot"] = lot_affordability(
                prices[code],
                initial_cash=INITIAL_CASH,
                n_names=max_names,      # 最不利侧：持仓最多时单只预算最小
                start=OOS_START,
                end=OOS_END,
            )
        reach[code] = info

    # -------- T3：成本档 --------
    fee_delta = fee_preset_delta(gate1_run.MAIN_FEE, gate1_run.HIGH_FEE)

    return {
        "mode": "trigger-reachability",
        "data_root": str(data_root),
        "prereg": {
            "version": gate1_run.PREREG_VERSION,
            "sha256_prefix": gate1_run.PREREG_SHA256,
        },
        "probe_config": {
            "lookback": config.lookback,
            "skip": config.skip,
            "top_fraction": config.top_fraction,
            "price_limit_rate": gate1_run.PORTFOLIO_PRICE_LIMIT_RATE,
        },
        "pool": {
            "n_symbols": len(prices),
            "n_oos_targets": len(keys),
            "n_gem": len(gem),
            "gem_symbols": gem,
            "min_holdings": min_names,
            "max_holdings": max_names,
        },
        "T1_gem_exclusion": {
            "t1a_oos_lookback_boundary": {
                "perturb_before": PRICE_PERTURB_CUTOFF,
                "factor": 1.10,
                "n_changed": len(t1a_diff),
                "changed_months": t1a_diff,
                "reading": (
                    "0 表示 OOS 决策不依赖该日之前的任何数据 → IS 期池子怎么改都进不了 OOS 判据"
                    if not t1a_diff else "存在跨越该日期的长程依赖，须先解释清楚"
                ),
            },
            "t1b_literal_exclusion": {
                "cutoff": LITERAL_CUTOFF,
                "excluded_symbols": gem,
                "n_changed": len(t1b_diff),
                "changed_months": t1b_diff,
                "reading": (
                    "受影响月份全部集中在回看窗口仍触及 2020 年的头几个月；"
                    "机制是「少了回看数据」，不是「涨跌停约束」"
                ),
            },
            "t1c_window_mismatch": {
                "gem_regime_start": GEM_REGIME_START,
                "oos_months_in_regime": len(regime_months),
                "oos_months_holding_gem": len(months_with_gem),
                "slots": slots,
                "gem_slots": gem_slots,
                "gem_slot_share": (gem_slots / slots) if slots else None,
                "reading": (
                    "触发项移出的是 flat 10% 判定正确的区间（2017~2020），"
                    "保留的是判定偏严的区间（2020-08-24 起），而 OOS 全部落在后者"
                ),
            },
        },
        "T2_symbol_exclusion": reach,
        "T3_cost_tier": {
            "main_fee": gate1_run.MAIN_FEE,
            "high_fee": gate1_run.HIGH_FEE,
            "preset_delta": fee_delta,
            "requires_run": True,
            "reading": "只能靠真跑判定；本脚本只记录参数差，不对收益作任何推测",
        },
        "will_compute_returns": False,
    }


def write_report(report: Mapping[str, object], out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "gate1_trigger_reachability.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gate 1 证据不足触发项可达性审计（只读）")
    p.add_argument("--data-root", default=r"D:\DeepQuant power by txm\b50_v3")
    p.add_argument("--out-root", default=None, help="默认 <repo>/probe_out")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    data_root = Path(args.data_root)
    prices = gate1_run.load_prices(data_root)
    out_root = Path(args.out_root) if args.out_root else _REPO / "probe_out"
    report = build_report(prices, data_root=data_root)
    path = write_report(report, out_root)

    t1a = report["T1_gem_exclusion"]["t1a_oos_lookback_boundary"]
    t1b = report["T1_gem_exclusion"]["t1b_literal_exclusion"]
    t1c = report["T1_gem_exclusion"]["t1c_window_mismatch"]
    print(f"[T1a] 扰动 {t1a['perturb_before']} 前全部价格 → OOS 目标变更 {t1a['n_changed']} 个月")
    print(f"[T1b] 字面移出双创 2017~2020 → OOS 目标变更 {t1b['n_changed']} 个月")
    print(
        f"[T1c] flat 10% 判错区间内：OOS {t1c['oos_months_in_regime']} 个月，"
        f"其中 {t1c['oos_months_holding_gem']} 个月持有双创，"
        f"仓位占比 {t1c['gem_slot_share']:.1%}"
    )
    for code, info in report["T2_symbol_exclusion"].items():
        lot = info.get("lot") or {}
        print(
            f"[T2 ] {code} OOS 持有 {info['held_months']}/{info['oos_target_months']} 个月；"
            f"1 手 {lot.get('lot_cost_min', float('nan')):,.0f}~{lot.get('lot_cost_max', float('nan')):,.0f} 元 "
            f"vs 单只预算 {lot.get('budget_per_name', float('nan')):,.0f} 元"
        )
    print(f"[T3 ] 成本档参数差：{report['T3_cost_tier']['preset_delta']['changed']}（需真跑判定）")
    print(f"\n[WRITE] {path}")
    print("[PROBE] 未计算任何收益，未创建 gate1_out/，未调用 run_portfolio_backtest。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
