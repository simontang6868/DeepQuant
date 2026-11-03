"""未来函数检测：静态扫一遍源码，动态做一次「截断重算」。

两条腿都要有，单靠一条都不够
----------------------------
- **静态**（AST）能抓到写法上就错的：``rolling(center=True)``、``shift(-1)``、
  ``bfill()``。代价是只认识已知模式，换个马甲（自定义卷积、倒序索引）就漏。
- **动态**（截断重算）不认写法，只认行为：用前 t 行算出的信号，必须与用全样本
  算出的信号在前 t 行完全一致。任何"偷看未来"的实现都会在这里露馅，代价是慢、
  且对"必须全样本才能算"的指标（如全期 z-score）会误判——所以它只是证据之一，
  结论仍由闸门按"任一命中即否决"处理。

为什么把"截断"当成金标准
------------------------
t 时刻能拿到的数据就是 ``df[:t+1]``，这不是我们的假设，是时点的定义。凡是在
``df`` 上算出来、却与 ``df[:t+1]`` 不同的信号，就一定用到了 t 之后的信息——不需要
再讨论它"其实合不合理"。
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

#: 采样多少个截断点。点越多越可能抓到只在小窗口露馅的实现，但耗时线性增长。
DEFAULT_PROBES = 8

#: 静态扫描只认这些模式；新增模式必须同时补一条变异测试，否则等于没检查。
PATTERN_LABELS = {
    "center_rolling": "rolling(center=True)：窗口包含未来值",
    "negative_shift": "shift(负数)：直接取未来行",
    "backfill": "bfill()/fillna(method='bfill')：用未来值回填",
    "negative_period": "pct_change(负数)/diff(负数)：跨向未来",
}


@dataclass(frozen=True)
class LeakFinding:
    code: str
    detail: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "location": self.location}


@dataclass
class LeakResult:
    static: list[LeakFinding] = field(default_factory=list)
    dynamic: list[LeakFinding] = field(default_factory=list)
    probes_tested: int = 0
    probes_skipped: int = 0
    dynamic_note: str = ""

    @property
    def findings(self) -> list[LeakFinding]:
        return self.static + self.dynamic

    @property
    def leaked(self) -> bool:
        return bool(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaked": self.leaked,
            "static": [item.to_dict() for item in self.static],
            "dynamic": [item.to_dict() for item in self.dynamic],
            "probes_tested": self.probes_tested,
            "probes_skipped": self.probes_skipped,
            "dynamic_note": self.dynamic_note,
        }


def _is_negative(node: ast.AST) -> bool:
    """识别 ``-1`` 这种字面量负值（含 ``ast.USub`` 包裹的常量）。"""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = node.operand
        return isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float)) and operand.value > 0
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value < 0


def _truthy(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def scan_source(source: str, *, location: str = "") -> list[LeakFinding]:
    """扫描一段源码文本，返回静态未来函数线索。"""
    findings: list[LeakFinding] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # 源码不可解析 → 不能声称"干净"
        return [LeakFinding("unparsable_source", f"源码无法解析：{exc}", location)]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else None
        where = f"{location}:{getattr(node, 'lineno', '?')}"

        if attr in ("rolling", "expanding"):
            for keyword in node.keywords:
                if keyword.arg == "center" and _truthy(keyword.value):
                    findings.append(
                        LeakFinding("center_rolling", PATTERN_LABELS["center_rolling"], where)
                    )
        if attr in ("shift", "pct_change", "diff") and node.args and _is_negative(node.args[0]):
            code = "negative_shift" if attr == "shift" else "negative_period"
            findings.append(LeakFinding(code, PATTERN_LABELS[code], where))
        if attr == "bfill":
            findings.append(LeakFinding("backfill", PATTERN_LABELS["backfill"], where))
        if attr == "fillna":
            for keyword in node.keywords:
                if keyword.arg == "method" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "bfill":
                    findings.append(LeakFinding("backfill", PATTERN_LABELS["backfill"], where))
    return findings


def scan_strategy(strategy: Any) -> list[LeakFinding]:
    """扫描策略实现（类 → 源码）。取不到源码时返回 ``source_unavailable``，不当作干净。"""
    target = strategy if inspect.isclass(strategy) else type(strategy)
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError) as exc:
        return [LeakFinding("source_unavailable", f"无法获取策略源码：{exc}", target.__name__)]
    return scan_source(source, location=target.__name__)


def _series_equal(left: pd.Series, right: pd.Series) -> bool:
    if len(left) != len(right):
        return False
    left_values = left.tolist()
    right_values = right.tolist()
    for a, b in zip(left_values, right_values):
        if pd.isna(a) and pd.isna(b):
            continue
        if a != b:
            return False
    return True


def truncation_test(
    strategy: Any,
    data: pd.DataFrame,
    *,
    probes: int = DEFAULT_PROBES,
) -> LeakResult:
    """动态截断检验：前缀信号必须等于全样本信号的前缀。

    ``probes`` 个截断点按分位数取（跳过最前面的 40%，避开预热期导致的必然差异）。
    """
    result = LeakResult()
    if len(data) < 20:
        result.dynamic_note = f"数据仅 {len(data)} 行，无法做截断检验"
        return result

    full = strategy.generate_signals(data)
    if not isinstance(full, pd.Series):
        result.dynamic.append(
            LeakFinding("bad_signal_type", f"信号不是 Series：{type(full).__name__}")
        )
        return result

    low = int(len(data) * 0.4)
    if low >= len(data) - 1:
        result.dynamic_note = "可用截断点不足（数据过短）"
        return result
    positions = sorted({int(round(x)) for x in pd.Series(range(low, len(data))).quantile(
        [i / (probes + 1) for i in range(1, probes + 1)]
    ).tolist()})
    for position in positions:
        prefix = data.iloc[: position + 1]
        try:
            partial = strategy.generate_signals(prefix)
        except Exception as exc:  # noqa: BLE001 - 策略在短窗口上算不出属可解释情形
            result.probes_skipped += 1
            result.dynamic_note = f"截断点 {position} 生成信号失败：{type(exc).__name__}: {exc}"
            continue
        result.probes_tested += 1
        expected = full.iloc[: position + 1]
        if not _series_equal(partial, expected):
            stamp = data["date"].iloc[position] if "date" in data.columns else position
            result.dynamic.append(
                LeakFinding(
                    "prefix_mismatch",
                    f"用前 {position + 1} 行算出的信号与全样本信号前段不一致（时点 {stamp}）",
                    f"probe@{position}",
                )
            )
            break  # 一处不一致就足够否决，不必跑完
    if not result.dynamic and result.probes_tested == 0:
        result.dynamic_note = result.dynamic_note or "没有可用截断点，本项未取得证据"
    return result


def check_leakage(strategy: Any, data: pd.DataFrame, *, probes: int = DEFAULT_PROBES) -> LeakResult:
    """静态 + 动态一起跑，合并成一份结论。"""
    result = truncation_test(strategy, data, probes=probes)
    result.static = scan_strategy(strategy)
    return result


def probe_positions(data: pd.DataFrame, probes: int = DEFAULT_PROBES) -> Sequence[int]:
    """（供测试与调试）返回会实际使用的截断位置。"""
    if len(data) < 20:
        return ()
    low = int(len(data) * 0.4)
    quantiles = [i / (probes + 1) for i in range(1, probes + 1)]
    series = pd.Series(range(low, len(data)))
    return tuple(sorted({int(round(x)) for x in series.quantile(quantiles).tolist()}))
