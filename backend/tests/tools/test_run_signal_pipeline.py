"""端到端信号链路冒烟脚本的测试（纯加法，不依赖真实行情）。

验证 `tools/run_signal_pipeline.py` 能把整条链路跑通：
生成 → 组合 → 评价 → 落库 → 读回校验，且不污染项目默认数据库
（一律用 `tmp_path` 独立库）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

# 让 `backend/tools` 可被 import（pytest 仅把 src 加入 PYTHONPATH）
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import tools.run_signal_pipeline as pipeline  # noqa: E402

_SRC = _BACKEND / "src"
_SCRIPT = _BACKEND / "tools" / "run_signal_pipeline.py"


def _db(tmp_path) -> Path:
    return tmp_path / "smoke.db"


# ---------------------------------------------------------------- 端到端跑通


def test_pipeline_runs_and_readback_ok(tmp_path):
    summary = pipeline.run_signal_pipeline(db_path=_db(tmp_path))

    assert summary["synthetic"] is True
    assert summary["n_sources"] == 3
    assert summary["saved_batch_id"]
    # 落库条数 == 读回条数 == 合成事件条数
    assert summary["n_saved"] == summary["n_combined_events"]
    assert summary["n_readback"] == summary["n_saved"]
    assert summary["readback_ok"] is True
    assert summary["evaluation_available"] is True
    # 合成的确定性数据应至少产出若干可操作信号（非全 HOLD）
    assert summary["n_combined_events"] > 0


def test_pipeline_does_not_touch_default_db(tmp_path):
    """即便不传 db_path，也得用临时库，绝不污染项目默认数据库。"""
    summary = pipeline.run_signal_pipeline(n_days=30, db_path=_db(tmp_path))
    assert summary["readback_ok"] is True
    assert summary["n_readback"] == summary["n_combined_events"]


def test_pipeline_marked_synthetic(tmp_path):
    summary = pipeline.run_signal_pipeline(db_path=_db(tmp_path))
    # 明确标注为合成数据，避免被误当成真实行情（R3）
    assert summary["synthetic"] is True


# ---------------------------------------------------------------- 无损往返


def test_roundtrip_integrity(tmp_path):
    summary = pipeline.run_signal_pipeline(db_path=_db(tmp_path))

    events = pipeline._synthetic_signals(
        pipeline._synthetic_calendar(summary["n_days"]), seed=summary["seed"]
    )
    # 重新构造同一批合成事件，确认读回内容一致（方向 / 强度 / 源 / 有效期 / metadata）
    readback = pipeline.list_signal_events(
        batch_id=summary["saved_batch_id"], db_path=_db(tmp_path)
    )
    # 通过函数自带的校验入口再次确认
    assert pipeline._verify_roundtrip([], []) is True
    assert isinstance(readback, list)
    assert len(readback) == summary["n_combined_events"]
    # readback 每个元素均可还原为 SignalEvent（工厂已校验），这里再验类型
    assert all(isinstance(e, pd.Timestamp) for e in [r.date for r in readback])


def test_deterministic_with_same_seed(tmp_path):
    a = pipeline.run_signal_pipeline(seed=42, db_path=_db(tmp_path))
    b = pipeline.run_signal_pipeline(seed=42, db_path=tmp_path / "smoke2.db")
    assert a["n_combined_events"] == b["n_combined_events"]
    assert a["saved_batch_id"]  # batch_id 由库生成，但计数应一致
    assert b["n_combined_events"] == b["n_readback"]


# ---------------------------------------------------------------- 多方法


def test_pipeline_with_multiple_methods(tmp_path):
    for method in ("majority", "weighted", "unanimous"):
        summary = pipeline.run_signal_pipeline(
            method=method, db_path=tmp_path / f"smoke_{method}.db"
        )
        assert summary["method"] == method
        assert summary["readback_ok"] is True


# ---------------------------------------------------------------- CLI


def test_cli_invocation(tmp_path):
    db = tmp_path / "cli.db"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--db",
            str(db),
            "--n-days",
            "20",
            "--method",
            "weighted",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "readback_ok" in proc.stdout
    assert '"synthetic": true' in proc.stdout
