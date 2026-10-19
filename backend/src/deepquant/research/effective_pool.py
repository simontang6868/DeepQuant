"""名义池 ≠ 有效池：把「这只标的到底买不买得起」变成跑之前就能算的硬闸门。

为什么需要这个模块
------------------
`initial_cash=100_000` 与 A 股 100 股整手规则相互作用，会让部分标的**结构上**无法建仓：

    一手成本 = 价格 × 100 > 初始资金  ⇒  买单被拒（引擎行为正确）
    ⇒ 训练期零成交  ⇒  `min_trades` 不满足  ⇒  **选参失败**
    ⇒ 该标的在该窗口整段消失，而汇总表仍写「6 只池」

`docs/OOS_RESULTS_2026-10-19.json` 实测：6 只名义池里 `600519.SH` 自 2021 年起一手 > 10 万，
5 个样本外窗口 `sel_ok` 全为 False，贡献 **0** 个观测；实际有效池是 4 只。
这件事在跑之前就能算出来，却被留到跑完之后靠人看日志才发现。

`OOS_PREREG_V2 §3` 已把 G2（资金充足性）、G3（名义池 = 有效池，否则单列差集）写成判据，
但只有 `scripts/universe_preflight.py --strict` 的**按年最低价**近似，
且没有任何流水线在跑之前真的调用它。本模块补的正是这一层：

1. 粒度从「年」细化到「walk-forward 窗口的训练段」（选参发生在训练段，不是全年）；
2. 判据可切换 `strict`（训练段最高价）/ `lenient`（训练段最低价），默认 strict 与 G2 一致；
3. 判定结果分 `passed / failed`，`failed` 时给出**逐条阻断明细**，可直接写进报告；
4. 提供 `on_fail` 语义：`block`（不产生判定）/ `warn`（照跑但记录缺口）。

设计约束
--------
- **纯计算，无 IO**：不读文件、不看环境变量、不 import walkforward。
  窗口以 `Window` 五元组表达，由调用方从 `generate_windows` 的结果转换。
- **不改既有行为**：不 import 本模块时，仓库行为逐字节不变。
- **预测与实测要能对账**：`PoolGate.predicted_absent` 是「跑之前」的判断，
  `docs/EFFECTIVE_POOL_GATE_2026-10-19.md` 把它与真实 `sel_ok` 逐窗对照过——
  一个不能预见现实的闸门只是装饰。

价格口径提醒
------------
若输入是前复权（qfq）价格，一手成本是真实成本的**下界**（分红送转会下调历史价）。
因此「qfq 判买得起」不足以下结论，「qfq 判买不起」才是可靠的。方向不要搞反。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import pandas as pd

DEFAULT_LOT_SIZE = 100
"""A 股最小交易单位（股）。"""


@dataclass(frozen=True)
class Window:
    """一个 walk-forward 窗口的时间边界（与 `WindowSpec` 同构，但不依赖它）。"""

    window_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp


def coerce_window(obj) -> Window:
    """接受 `Window`、`WindowSpec` 或五元组，统一成 `Window`。

    故意用属性读取而不是 `isinstance`：本模块不 import walkforward，
    避免研究脚本与应用层耦合；只要字段名对得上就能用。
    """
    if isinstance(obj, Window):
        return obj
    if isinstance(obj, (tuple, list)):
        if len(obj) != 5:
            raise ValueError(f"窗口元组需要 5 个元素，收到 {len(obj)}")
        idx, ts, te, os_, oe = obj
        return Window(int(idx), pd.Timestamp(ts), pd.Timestamp(te),
                      pd.Timestamp(os_), pd.Timestamp(oe))
    return Window(
        window_index=int(getattr(obj, "window_index")),
        train_start=pd.Timestamp(getattr(obj, "train_start")),
        train_end=pd.Timestamp(getattr(obj, "train_end")),
        oos_start=pd.Timestamp(getattr(obj, "oos_start")),
        oos_end=pd.Timestamp(getattr(obj, "oos_end")),
    )


@dataclass(frozen=True)
class SymbolWindowCost:
    """单只标的在单个窗口内的一手成本区间。

    `train_*` 用于判「能不能选参」（选参发生在训练段），
    `oos_*` 用于判「参与后能不能真的建仓」。
    """

    code: str
    window_index: int
    train_rows: int
    oos_rows: int
    train_min_close: float
    train_max_close: float
    oos_min_close: float
    oos_max_close: float
    lot_size: int = DEFAULT_LOT_SIZE

    def one_lot(self, segment: str = "train", basis: str = "strict") -> float:
        if segment == "train":
            px = self.train_max_close if basis == "strict" else self.train_min_close
        elif segment == "oos":
            px = self.oos_max_close if basis == "strict" else self.oos_min_close
        else:
            raise ValueError(f"segment 只能是 train/oos，收到 {segment!r}")
        return round(px * self.lot_size, 2)


@dataclass(frozen=True)
class SymbolVerdict:
    """单只标的在整段回测上的闸门结论。

    两个层次必须分开，否则闸门会变成「永远不通过」：

    - ``absent``：**样本外一段都建不了仓**（0 个窗口可负担）→ 结构性缺席，
      汇总表里的「N 只池」就有假的成分，必须单列；
    - ``partially_blocked``：部分窗口买不起 → 固定资金 + 价格变化下的**常态**，
      报告里说明即可，不该阻断整轮研究。
    """

    code: str
    n_windows: int
    n_train_affordable: int
    n_oos_windows: int
    n_oos_affordable: int
    first_blocked_window: int | None
    one_lot_max_overall: float
    reason: str

    @property
    def absent(self) -> bool:
        """**样本外窗口里一个都建不了仓**：这只标的对样本外结果的贡献严格为 0。

        注意必须按样本外窗口数，不能按全段窗口数。600519 在 2017–2020 的窗口是买得起的，
        按全段算会被判成"没缺席"，而它在 2025 年之后的 5 个样本外窗口**全部**失败 ——
        「6 只池实际 4 只」说的正是后者。
        """
        return self.n_oos_windows > 0 and self.n_oos_affordable == 0

    @property
    def partially_blocked(self) -> bool:
        return 0 < self.n_oos_affordable < self.n_oos_windows

    @property
    def clean(self) -> bool:
        """全部窗口的训练段与样本外段都买得起。"""
        return self.n_train_affordable == self.n_windows and self.n_oos_affordable == self.n_oos_windows


@dataclass(frozen=True)
class PoolGate:
    """一次池闸门的完整结论。"""

    cash: float
    basis: str
    lot_size: int
    nominal_n: int
    min_effective_ratio: float
    per_symbol: Mapping[str, SymbolVerdict]
    per_window_effective: Mapping[int, int]
    per_window_nominal: Mapping[int, int]
    missing: Sequence[str] = field(default_factory=tuple)
    """名义池里有、但没进入本次判定的标的（如被数据闸门拦掉）。"""
    blocked: Sequence[dict] = field(default_factory=tuple)
    """**训练段**一手 > 资金的明细。训练段买不起 ⇒ 选参必然失败，这是"缺席"的成因。"""
    blocked_oos: Sequence[dict] = field(default_factory=tuple)
    """**样本外段**一手 > 资金的明细。样本外买不起 ⇒ 就算选参成功也建不了仓。

    两段必须分开记：`sel_ok` 说的是"选参成不成功"（取决于训练段），
    而"建不建得了仓"取决于样本外段。混在一起会让预测与实测对不上账。
    """
    note: str = ""

    @property
    def names(self) -> list[str]:
        return sorted(self.per_symbol)

    def effective_universe(self) -> tuple[str, ...]:
        """样本外至少有一个窗口能建仓的标的（即"不是结构性缺席"的那些）。"""
        return tuple(c for c in self.names if not self.per_symbol[c].absent)

    @property
    def predicted_absent(self) -> tuple[str, ...]:
        return tuple(c for c in self.names if self.per_symbol[c].absent)

    @property
    def partially_blocked(self) -> tuple[str, ...]:
        return tuple(c for c in self.names if self.per_symbol[c].partially_blocked)

    @property
    def g2_passed(self) -> bool:
        """G2（严格读法）：没有任何一个标的-窗口的**任何一段**一手 > 资金。

        这是预登记里「每个窗口」的字面要求。它很严，所以在真实池上通常为 False，
        此时**不等于**必须阻断——见 `enforce(block_on=...)`。
        """
        return len(self.blocked) == 0 and len(self.blocked_oos) == 0

    @property
    def g3_passed(self) -> bool:
        """G3：名义池里没有任何一只在样本外缺席（含"根本没数据"的那些）。"""
        return len(self.predicted_absent) == 0 and len(self.missing) == 0

    @property
    def worst_window_coverage(self) -> float:
        """最差窗口的「可建仓标的数 / 名义池」，比整段平均更能暴露缺口。"""
        if not self.per_window_nominal:
            return 0.0
        return min(
            self.per_window_effective.get(w, 0) / max(n, 1)
            for w, n in self.per_window_nominal.items()
        )

    def as_dict(self) -> dict:
        return {
            "cash": self.cash,
            "basis": self.basis,
            "lot_size": self.lot_size,
            "nominal_n": self.nominal_n,
            "n_effective": len(self.effective_universe()),
            "effective_universe": list(self.effective_universe()),
            "predicted_absent": list(self.predicted_absent),
            "missing": list(self.missing),
            "partially_blocked": list(self.partially_blocked),
            "min_effective_ratio": self.min_effective_ratio,
            "g2_passed": self.g2_passed,
            "g3_passed": self.g3_passed,
            "worst_window_coverage": round(self.worst_window_coverage, 4),
            "per_window_effective": {str(k): v for k, v in sorted(self.per_window_effective.items())},
            "per_window_nominal": {str(k): v for k, v in sorted(self.per_window_nominal.items())},
            "per_symbol": {
                c: {
                    "n_windows": v.n_windows,
                    "n_train_affordable": v.n_train_affordable,
                    "n_oos_windows": v.n_oos_windows,
                    "n_oos_affordable": v.n_oos_affordable,
                    "first_blocked_window": v.first_blocked_window,
                    "one_lot_max_overall": v.one_lot_max_overall,
                    "absent": v.absent,
                    "partially_blocked": v.partially_blocked,
                    "clean": v.clean,
                    "reason": v.reason,
                }
                for c, v in sorted(self.per_symbol.items())
            },
            "blocked": list(self.blocked),
            "blocked_oos": list(self.blocked_oos),
            "note": self.note,
        }


class PoolGateError(RuntimeError):
    """闸门不通过且 `on_fail="block"` 时抛出：不产生任何判定。"""

    def __init__(self, gate: PoolGate) -> None:
        self.gate = gate
        n_eff = len(gate.effective_universe())
        detail = "；".join(
            f"{b['code']} w{b['window_index']} 一手 {b['one_lot']:,.0f} > {gate.cash:,.0f}"
            for b in list(gate.blocked)[:5]
        )
        miss = f"；无数据 {list(gate.missing)}" if gate.missing else ""
        super().__init__(
            f"有效池闸门未通过：名义 {gate.nominal_n} 只 / 可建仓 {n_eff} 只"
            f"（G2={gate.g2_passed}, G3={gate.g3_passed}）。"
            f"样本外缺席 {list(gate.predicted_absent)}{miss}。{detail}"
        )


def _window_cost(
    code: str,
    frame: pd.DataFrame,
    win: Window,
    *,
    price_col: str = "close",
    lot_size: int = DEFAULT_LOT_SIZE,
) -> SymbolWindowCost:
    if price_col not in frame.columns:
        raise KeyError(f"{code}: 缺少列 {price_col!r}")
    if "date" not in frame.columns:
        raise KeyError(f"{code}: 缺少 date 列，无法切分窗口")

    s = frame[["date", price_col]].copy()
    s["date"] = pd.to_datetime(s["date"])
    s[price_col] = pd.to_numeric(s[price_col], errors="coerce")
    s = s.dropna(subset=["date", price_col])
    s = s[s[price_col] > 0]  # qfq 负价是已知瑕疵，不能用来算成本

    tr = s[(s["date"] >= win.train_start) & (s["date"] <= win.train_end)][price_col]
    oo = s[(s["date"] >= win.oos_start) & (s["date"] <= win.oos_end)][price_col]

    # 训练段没有数据时退化为全窗口，避免拿空序列判「买得起」（会得到沉默的 False）
    if len(tr) == 0:
        tr = oo
    if len(oo) == 0:
        oo = tr
    if len(tr) == 0:
        raise ValueError(f"{code} w{win.window_index}: 训练段与样本外段都没有可用价格")

    return SymbolWindowCost(
        code=code,
        window_index=win.window_index,
        train_rows=int(len(tr)),
        oos_rows=int(len(oo)),
        train_min_close=float(tr.min()),
        train_max_close=float(tr.max()),
        oos_min_close=float(oo.min()),
        oos_max_close=float(oo.max()),
        lot_size=lot_size,
    )


def evaluate_pool(
    frames: Mapping[str, pd.DataFrame],
    windows,
    *,
    cash: float,
    nominal_n: int | None = None,
    basis: str = "strict",
    cut: pd.Timestamp | str | None = None,
    lot_size: int = DEFAULT_LOT_SIZE,
    min_effective_ratio: float = 1.0,
    expected_codes: Sequence[str] | None = None,
) -> PoolGate:
    """对整池 × 全窗口做一次资金充足性判定。

    参数
    ----
    frames      : 代码 → 含 `date`/`close` 的日线表
    windows     : 两种形态都接受
                  - 序列：一套窗口套用到全池（各标的交易日历一致时用）；
                  - 映射 `code → 序列`：逐标的自有窗口。
                    **walk-forward 的窗口是按各标的自己的交易日历生成的**
                    （300750 是 2018 年上市，窗口数与 600519 不同），
                    在这种场景下必须用映射形态，否则会拿不属于该标的的日期去取价。
    cash        : 初始资金（整笔，不是单只预算——单标的 walk-forward 时整笔可投入）
    nominal_n   : 名义池规模；默认等于 `len(frames)`。传更大的值可以表达
                  「清单里有 N 只但只有 M 只有数据」这类装配缺口
    basis       : `strict`（训练段最高价，与 G2 一致）/ `lenient`（最低价）
    cut         : IS/OOS 分界日。`oos_start > cut` 的窗口算样本外窗口，
                  缺席判定只数这些窗口；`None` 表示全部窗口都算样本外窗口。
    """
    if basis not in ("strict", "lenient"):
        raise ValueError(f"basis 只能是 strict/lenient，收到 {basis!r}")
    if cash <= 0:
        raise ValueError("cash 必须为正数")
    cut_ts = None if cut is None else pd.Timestamp(cut)

    def wins_for(code: str) -> list[Window]:
        src = windows[code] if isinstance(windows, Mapping) else windows
        out = [coerce_window(w) for w in src]
        return [w for w in out if getattr(w, "valid", True)]

    if not frames:
        raise ValueError("空标的集合无法判定有效池")
    first = next(iter(frames))
    if not wins_for(first):
        raise ValueError("没有可用窗口，无法判定有效池")

    per_symbol: dict[str, SymbolVerdict] = {}
    blocked: list[dict] = []
    blocked_oos: list[dict] = []
    win_eff: dict[int, int] = {}
    win_nom: dict[int, int] = {}

    for code in sorted(frames):
        wins = wins_for(code)
        n_tr_ok = 0
        n_oos_win = 0
        n_oos_ok = 0
        first_blocked: int | None = None
        worst_one_lot = 0.0

        for w in wins:
            is_oos_window = cut_ts is None or w.oos_start > cut_ts
            win_nom[w.window_index] = win_nom.get(w.window_index, 0) + 1
            if is_oos_window:
                n_oos_win += 1
            c = _window_cost(code, frames[code], w, lot_size=lot_size)
            need_tr = c.one_lot("train", basis)
            need_oos = c.one_lot("oos", basis)
            worst_one_lot = max(worst_one_lot, need_tr, need_oos)
            tr_ok = need_tr <= cash
            oos_ok = need_oos <= cash
            if tr_ok:
                n_tr_ok += 1
            if is_oos_window and oos_ok:
                n_oos_ok += 1
            if tr_ok and oos_ok:
                win_eff[w.window_index] = win_eff.get(w.window_index, 0) + 1
            if not tr_ok:
                if first_blocked is None:
                    first_blocked = w.window_index
                blocked.append({
                    "code": code,
                    "window_index": w.window_index,
                    "segment": "train",
                    "is_oos_window": bool(is_oos_window),
                    "one_lot": need_tr,
                    "cash": float(cash),
                    "price": round(need_tr / lot_size, 4),
                    "train_start": str(w.train_start.date()),
                    "train_end": str(w.train_end.date()),
                })
            if not oos_ok:
                blocked_oos.append({
                    "code": code,
                    "window_index": w.window_index,
                    "segment": "oos",
                    "is_oos_window": bool(is_oos_window),
                    "one_lot": need_oos,
                    "cash": float(cash),
                    "price": round(need_oos / lot_size, 4),
                    "oos_start": str(w.oos_start.date()),
                    "oos_end": str(w.oos_end.date()),
                })

        if n_tr_ok == len(wins):
            reason = "全窗口训练段均买得起"
        elif n_oos_ok == 0 and n_oos_win > 0:
            reason = (
                f"样本外 {n_oos_win} 个窗口**全部**买不起（最贵一手 {worst_one_lot:,.0f} 元 "
                f"> 资金 {cash:,.0f} 元），对样本外结果贡献为 0"
            )
        else:
            reason = (
                f"{len(wins) - n_tr_ok}/{len(wins)} 个窗口训练段一手成本 > 资金，"
                f"选参必然失败（首个 w{first_blocked}，最贵一手 {worst_one_lot:,.0f} 元）"
            )
        per_symbol[code] = SymbolVerdict(
            code=code,
            n_windows=len(wins),
            n_train_affordable=n_tr_ok,
            n_oos_windows=n_oos_win,
            n_oos_affordable=n_oos_ok,
            first_blocked_window=first_blocked,
            one_lot_max_overall=round(worst_one_lot, 2),
            reason=reason,
        )

    n_windows = max((v.n_windows for v in per_symbol.values()), default=0)
    nominal = int(nominal_n if nominal_n is not None else len(frames))
    expected = tuple(expected_codes) if expected_codes is not None else tuple(frames)
    missing = tuple(sorted(set(expected) - set(frames)))
    return PoolGate(
        cash=float(cash),
        basis=basis,
        lot_size=lot_size,
        nominal_n=nominal,
        min_effective_ratio=min_effective_ratio,
        per_symbol=per_symbol,
        per_window_effective=win_eff,
        per_window_nominal=win_nom,
        missing=missing,
        blocked=tuple(blocked),
        blocked_oos=tuple(blocked_oos),
        note=(
            f"{basis} 判据（训练段{'最高' if basis == 'strict' else '最低'}收盘 × {lot_size} 股）；"
            f"{len(frames)} 只标的 × 最多 {n_windows} 个窗口；"
            f"样本外窗口分界 {cut_ts.date() if cut_ts is not None else '未设（全部算样本外）'}"
        ),
    )


def enforce(gate: PoolGate, on_fail: str = "block", block_on: str = "absent") -> PoolGate:
    """按 `on_fail` / `block_on` 语义处理闸门结果。

    `block_on` 三档，差别只在「多严才算过不去」：

    - ``absent``（默认）：只要有标的**在样本外一段都建不了仓**就不通过。
      这是 `OOS_RESULTS` 里「6 只池实际 4 只」那一类结构缺口，也是最该被拦的。
    - ``partial``：连「部分窗口买不起」也不放过（G2 字面最严读法）。
      固定资金 + 价格变动下几乎任何池都过不了，只适合对单窗口做实验时用。
    - ``none``：什么都不拦，只记录（用于复跑历史规格时对账）。

    `on_fail="warn"` 时一律不抛，缺口写进报告即可——历史规格的复跑必须能跑完，
    否则「同一份规格可复现」这句话就没人验证得了。
    """
    if on_fail not in ("block", "warn"):
        raise ValueError(f"on_fail 只能是 block/warn，收到 {on_fail!r}")
    if block_on not in ("absent", "partial", "none"):
        raise ValueError(f"block_on 只能是 absent/partial/none，收到 {block_on!r}")

    failed = False
    if block_on == "absent":
        failed = (not gate.g3_passed) or (
            len(gate.effective_universe()) < gate.min_effective_ratio * gate.nominal_n
        )
    elif block_on == "partial":
        failed = (not gate.g2_passed) or (not gate.g3_passed)
    if failed and on_fail == "block":
        raise PoolGateError(gate)
    return gate
