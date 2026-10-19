"""A-P1 测试辅助（**不是 conftest**）。

⚠️ 教训：本目录最初放了 `conftest.py`，结果**遮蔽了 `backend/tests/conftest.py`** ——
pytest 会把测试目录插进 `sys.path`，`backend/tests/api/test_backtest_history_api.py` 里
`from conftest import ingest_dataset` 于是解析到本目录那份，全量套件直接多出 2 条 ImportError。
因此这里改用普通模块名 `_ap1_support`，不占用 `conftest` 这个名字。

**为什么惰性加载 `gate1_run`**：`scripts/gate1_run.py` 正被 B 线并行修改
（2026-10-19 01:24、01:25 均有落盘）。在模块导入期加载它，一旦撞上保存到一半的
中间状态，整个目录会变成 collection error（ERROR 而非 failure），干扰「零 failure」判定。
放到函数内加载，最坏情况也只是该用例失败。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_gate1_run():
    """按文件路径加载 `scripts/gate1_run.py`（每次调用重新加载，取最新版本）。"""
    path = REPO_ROOT / "scripts" / "gate1_run.py"
    if not path.exists():
        raise FileNotFoundError(f"脚本不存在：{path}")
    spec = importlib.util.spec_from_file_location("_ap1_gate1_run", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御
        raise ImportError(f"无法为 {path} 构造 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_run_result(
    gate1_run,
    *,
    name: str = "main",
    cumulative_return: float = 5.0,
    benchmark_cumulative_return: float = 0.2,
    monthly_returns: tuple[float, ...] = (0.10, -0.10),
    benchmark_monthly: tuple[float, ...] | None = None,
):
    """造一个最小可用的 `RunResult`，用于只测「落盘契约」而不跑任何回测。

    `strategy.cumulative_return` 是**全程**累计（BACKTEST_START..BACKTEST_END），
    与「评估窗口内累计」是两回事 —— 这里刻意让两者取不同值，好让测试能区分它们。
    """
    bench = benchmark_monthly or tuple(0.0 for _ in monthly_returns)
    paired = pd.DataFrame(
        {
            "month": [f"2021-{i + 1:02d}-28" for i in range(len(monthly_returns))],
            "r_strategy": list(monthly_returns),
            "r_benchmark": list(bench),
        }
    )
    paired["d"] = paired["r_strategy"] - paired["r_benchmark"]

    stats = SimpleNamespace(
        n=len(paired),
        mean_d=float(paired["d"].mean()),
        std_d=float(paired["d"].std(ddof=1)) if len(paired) > 1 else 0.0,
        t_stat=0.0,
        p_value=1.0,
        win_ratio=float((paired["d"] > 0).mean()),
        as_dict=lambda: {"n": len(paired), "mean_d": float(paired["d"].mean())},
    )

    return gate1_run.RunResult(
        spec=gate1_run.ExperimentSpec(name=name, variable="—", value="测试"),
        strategy=SimpleNamespace(cumulative_return=cumulative_return),
        benchmark=SimpleNamespace(cumulative_return=benchmark_cumulative_return),
        paired=paired,
        stats=stats,
        elapsed=0.0,
        strategy_total_return_eval=float((1.0 + paired["r_strategy"]).prod() - 1.0),
        benchmark_total_return_eval=float((1.0 + paired["r_benchmark"]).prod() - 1.0),
    )


def judgement_stub():
    """`write_outputs` 只调用 `judgement.as_dict()`，给一个最小替身即可。"""
    return SimpleNamespace(as_dict=lambda: {"verdict": "failure", "rule_hits": []})
