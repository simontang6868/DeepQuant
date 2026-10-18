"""股债利差研究：独立运行入口。

用法（在仓库根目录）：
    set PYTHONPATH=backend/src
    python scripts/run_equity_bond_spread.py --data-dir data/equity_bond_spread \
        --out-dir data/equity_bond_spread/results

产出：
- ``events_<experiment>.csv``      全部信号事件与前瞻收益
- ``comparison_<experiment>.csv``  信号组 vs 无条件基准组
- ``sensitivity.csv``              参数敏感性网格
- ``summary.json``                 机器可读的实验记录（含配置、数据摘要、结论分级）
- ``EQUITY_BOND_SPREAD_RESEARCH_REPORT.md``  中文研究报告

设计约束：
- 参数只从配置文件/命令行传入，运行结果不回写参数；
- 每次运行都记录数据文件 SHA-256、配置、运行时间，保证可复现；
- 不连接任何券商，不生成交易指令。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from deepquant.research.equity_bond_spread.cases import check_cases  # noqa: E402
from deepquant.research.equity_bond_spread.config import (  # noqa: E402
    IndicatorType,
    ResearchConfig,
    sensitivity_grid,
)
from deepquant.research.equity_bond_spread.data import build_panel, validate_panel  # noqa: E402
from deepquant.research.equity_bond_spread.experiment import (  # noqa: E402
    run_experiment,
    run_sensitivity,
)
from deepquant.research.equity_bond_spread.report import (  # noqa: E402
    VERDICT_LABELS,
    build_report_input,
    render_report,
    verdict,
)


def _pick(rows: list[dict], window_td: int) -> dict | None:
    for r in rows:
        if r["window_td"] == window_td:
            return r
    return rows[-1] if rows else None


def main() -> int:
    ap = argparse.ArgumentParser(description="运行股债利差研究")
    ap.add_argument("--data-dir", type=Path,
                    default=Path("data/equity_bond_spread"))
    ap.add_argument("--out-dir", type=Path,
                    default=Path("data/equity_bond_spread/results"))
    ap.add_argument("--skip-sensitivity", action="store_true",
                    help="跳过敏感性网格（加快运行）")
    args = ap.parse_args()

    data_dir: Path = args.data_dir
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    equity_csv = data_dir / "csi300_monthly.csv"
    bond_csv = data_dir / "cn10y.csv"
    if not equity_csv.exists() or not bond_csv.exists():
        print(f"缺少数据文件：{equity_csv} / {bond_csv}", file=sys.stderr)
        print("请先运行 scripts/prepare_equity_bond_spread_data.py", file=sys.stderr)
        return 2

    panel = build_panel(equity_csv, bond_csv)
    info = panel.to_dict()
    vrep = validate_panel(panel, min_obs=24)
    info["validation_errors"] = vrep.error_codes()
    info["validation_issues"] = [
        {"level": i.level, "code": i.code, "message": i.message, "count": i.count}
        for i in vrep.issues
    ]
    if vrep.error_codes():
        print(f"数据校验未通过：{vrep.error_codes()}", file=sys.stderr)

    manifest_path = data_dir / "manifest.json"
    if manifest_path.exists():
        info["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))

    # --- 主实验：假设 A 与假设 B ---
    cfg_b = ResearchConfig(indicator=IndicatorType.B, threshold_pct=90.0,
                           experiment_name="B_primary_thr90")
    cfg_a = ResearchConfig(indicator=IndicatorType.A, lookback_years=5,
                           threshold_pct=90.0, experiment_name="A_primary_5y_thr90")

    results = {}
    cases_raw = {}
    for key, cfg in (("A", cfg_a), ("B", cfg_b)):
        res = run_experiment(panel.frame, cfg, info)
        results[key] = res
        cases_raw[key] = check_cases(res.indicator_frame, res.event_df, cfg)

        res.event_df.to_csv(out_dir / f"events_{key}.csv", index=False)
        pd.DataFrame(res.comparison + res.comparison_is + res.comparison_oos).to_csv(
            out_dir / f"comparison_{key}.csv", index=False
        )
        res.indicator_frame.to_csv(out_dir / f"indicator_{key}.csv")

    # --- 敏感性 ---
    sens = pd.DataFrame()
    if not args.skip_sensitivity:
        sens = run_sensitivity(panel.frame, sensitivity_grid(), info)
        sens.to_csv(out_dir / "sensitivity.csv", index=False)

    # 指标（历史百分位）的实际可用起点：早于此的区间无法验证
    if "B" in results:
        pv = results["B"].indicator_frame["percentile"].dropna()
        if len(pv):
            notes_first = (
                f"历史百分位首个有效日期为 **{pv.index.min().date()}**"
                "（需累计足够历史样本），早于此的区间无法验证该指标。"
            )
        else:
            notes_first = "历史百分位无有效值，指标不可用。"
    else:
        notes_first = ""

    # --- 数据限制说明 ---
    notes = [notes_first] if notes_first else []
    notes += [
        "观测频率为**月频**（数据源限制：行情接口的历史逐点查询有速率限制，"
        "无法在合理时间内取到全历史日频序列），前瞻窗口按 252 交易日/年折算为月数，"
        "属「近似交易日」口径。",
        "股票序列为**价格指数**口径，不含分红再投资；「近似总收益」= 价格收益 + "
        "窗口内平均股息率，是近似值。",
        "PE 口径未经指数公司官方确认；缺失或非法 PE 已置空，不参与信号。",
        "国债收益率曲线起始于 2006-03-01，此前区间的股债利差为缺失，不参与信号。",
    ]
    if info.get("validation_errors"):
        notes.append(f"数据校验报错：{info['validation_errors']}")

    rep_inp = build_report_input(info, results, sens, cases_raw, notes)
    md = render_report(rep_inp)
    report_path = out_dir / "EQUITY_BOND_SPREAD_RESEARCH_REPORT.md"
    report_path.write_text(md, encoding="utf-8")
    # 同时写到仓库根目录，便于直接查阅
    root_report = Path(__file__).resolve().parents[1] / "EQUITY_BOND_SPREAD_RESEARCH_REPORT.md"
    root_report.write_text(md, encoding="utf-8")

    # --- 机器可读摘要 ---
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": info,
        "experiments": {},
        "verdicts": {},
    }
    for key, res in results.items():
        summary["experiments"][key] = res.to_dict()
        r_is = _pick(res.comparison_is, max(res.config.forward_windows_td))
        r_oos = _pick(res.comparison_oos, max(res.config.forward_windows_td))
        n_oos = int(r_oos["n_signals"]) if r_oos else 0
        v = verdict(r_is["mean_difference"] if r_is else None,
                    r_oos["mean_difference"] if r_oos else None, n_oos)
        summary["verdicts"][key] = {
            "verdict": v,
            "label": VERDICT_LABELS[v],
            "is_mean_difference": r_is["mean_difference"] if r_is else None,
            "oos_mean_difference": r_oos["mean_difference"] if r_oos else None,
            "n_oos_events": n_oos,
        }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    print(f"报告已生成：{report_path}")
    for key, v in summary["verdicts"].items():
        print(f"假设 {key}: {v['label']} "
              f"(250td 持有期均值差 样本内 {v['is_mean_difference']}, "
              f"样本外 {v['oos_mean_difference']}, "
              f"样本外事件 {v['n_oos_events']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
