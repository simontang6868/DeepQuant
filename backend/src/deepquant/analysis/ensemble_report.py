"""ensemble 评价结果导出（Step 8.5' 配套，纯加法）。

把 `EnsembleEvalReport` 导出为 JSON / CSV，便于存档与人工查看。

红线与诚实性约定：
- 本模块**只做序列化 / 落盘**，不评价、不选择、不重新计算、不聚合出任何
  用于挑参的指标（R2 / R11）。所有数值均来自 `EnsembleEvalReport`，
  本模块不排序择优、不挑权重 / 方法。
- 导出的命中率仍不含费用、滑点、涨跌停、T+1 与整手约束，也不模拟撮合；
  本模块**原样写出** `EnsembleEvalReport.notes`（其中已声明「使用未来收益仅用于
  评价、不得用于参数选择」），不删除、不改写，避免把评价伪装成可执行 / 可盈利结论。
- 不把合成行情标为真实（R3）：若上游用合成数据，报告 notes 会如实说明，本模块
  照写不误，不替数据「加冕」成真实行情。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from deepquant.analysis.ensemble_eval import EnsembleEvalReport


def _report_rows(report: EnsembleEvalReport) -> list:
    """把报告展开为「一行一个 SignalEvalReport」的列表（先 ensemble，后各源）。"""
    rows = [report.ensemble]
    rows.extend(report.per_source.values())
    return rows


def export_ensemble_report_json(report: EnsembleEvalReport, path) -> Path:
    """把报告导出为 JSON 文件，返回写入路径。

    JSON 保留完整的嵌套结构（ensemble / per_source / notes），便于程序化再读取。
    """
    path = Path(path)
    data = report.to_dict()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def export_ensemble_report_csv(report: EnsembleEvalReport, path) -> Path:
    """把报告导出为 CSV 文件（一行一个源 + ensemble 行），返回写入路径。

    列来自 `SignalEvalReport.to_dict()` 的键；`notes` 是 list，写入单格时
    序列化为 JSON 字符串（不静默丢弃）。各源与 ensemble 行并列，仅为描述性
    展示，**不构成排序择优**（R2 / R11）。
    """
    path = Path(path)
    rows = _report_rows(report)
    if not rows:
        # ensemble 恒非空，这里只是防御性分支
        path.write_text("", encoding="utf-8")
        return path

    fieldnames = list(rows[0].to_dict().keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = {}
            for key, value in row.to_dict().items():
                # list 类型（notes）需压成标量，避免 CSV 单元格类型错误
                flat[key] = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, list)
                    else value
                )
            writer.writerow(flat)
    return path


def export_ensemble_report(
    report: EnsembleEvalReport, path, *, fmt: str = "json"
) -> Path:
    """按格式导出报告（dispatch）。

    Args:
        report: 待导出的 `EnsembleEvalReport`。
        path: 输出文件路径。
        fmt: ``"json"`` 或 ``"csv"``。

    Raises:
        ValueError: 不支持的格式。
    """
    fmt = fmt.lower()
    if fmt == "json":
        return export_ensemble_report_json(report, path)
    if fmt == "csv":
        return export_ensemble_report_csv(report, path)
    raise ValueError(f"不支持的导出格式：{fmt!r}（仅支持 'json' / 'csv'）")
