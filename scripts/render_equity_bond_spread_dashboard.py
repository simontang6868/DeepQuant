"""股债利差研究：生成自包含的 HTML 仪表盘（零额外依赖，内联 SVG）。

所有图表数据都来自 ``run_equity_bond_spread.py`` 的真实计算结果，
**不生成任何模拟或占位数据**；数据缺失的区间在图上直接留空并标注。
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import pandas as pd

W, H = 960, 300
PAD_L, PAD_R, PAD_T, PAD_B = 60, 20, 24, 40


def _svg_line_chart(
    dates: pd.Series,
    values: pd.Series,
    *,
    title: str,
    ylabel: str,
    markers: list[tuple] | None = None,
    hlines: list[tuple[float, str, str]] | None = None,
    color: str = "#2563eb",
) -> str:
    """画一条折线图（缺失值处断开，不插值）。"""
    df = pd.DataFrame({"d": pd.to_datetime(dates), "v": pd.to_numeric(values, errors="coerce")})
    df = df.dropna(subset=["d"])
    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{title}" style="width:100%;height:auto">'
    )
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
    parts.append(
        f'<text x="{PAD_L}" y="18" font-size="13" font-weight="600" fill="#111827">{title}</text>'
    )
    valid = df.dropna(subset=["v"])
    if valid.empty:
        parts.append(
            f'<text x="{W/2}" y="{H/2}" font-size="13" fill="#6b7280" text-anchor="middle">'
            f"无有效数据</text>"
        )
        parts.append("</svg>")
        return "".join(parts)

    vmin, vmax = float(valid["v"].min()), float(valid["v"].max())
    if hlines:
        for yv, _, _ in hlines:
            vmin, vmax = min(vmin, yv), max(vmax, yv)
    if vmin == vmax:
        vmin, vmax = vmin - 1, vmax + 1
    span = vmax - vmin
    vmin -= span * 0.05
    vmax += span * 0.05

    dmin, dmax = df["d"].min(), df["d"].max()
    tspan = max((dmax - dmin).total_seconds(), 1)

    def _x(d):
        return PAD_L + (d - dmin).total_seconds() / tspan * (W - PAD_L - PAD_R)

    def _y(v):
        return H - PAD_B - (float(v) - vmin) / (vmax - vmin) * (H - PAD_T - PAD_B)

    # 网格与 Y 轴刻度
    for i in range(5):
        vv = vmin + (vmax - vmin) * i / 4.0
        yy = _y(vv)
        parts.append(
            f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W-PAD_R}" y2="{yy:.1f}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_L-8}" y="{yy+4:.1f}" font-size="11" fill="#6b7280" '
            f'text-anchor="end">{vv:,.2f}</text>'
        )
    # X 轴年份
    years = sorted({int(d.year) for d in df["d"]})
    step = max(1, len(years) // 12)
    for yr in years[::step]:
        d = pd.Timestamp(year=yr, month=1, day=1)
        if d < dmin or d > dmax:
            continue
        xx = _x(d)
        parts.append(
            f'<line x1="{xx:.1f}" y1="{PAD_T}" x2="{xx:.1f}" y2="{H-PAD_B}" '
            f'stroke="#f3f4f6" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{xx:.1f}" y="{H-PAD_B+16}" font-size="10" fill="#6b7280" '
            f'text-anchor="middle">{yr}</text>'
        )
    parts.append(
        f'<text x="12" y="{H/2}" font-size="11" fill="#6b7280" '
        f'transform="rotate(-90 12 {H/2})" text-anchor="middle">{ylabel}</text>'
    )

    # 阈值横线
    for yv, label, clr in (hlines or []):
        yy = _y(yv)
        parts.append(
            f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W-PAD_R}" y2="{yy:.1f}" '
            f'stroke="{clr}" stroke-width="1.5" stroke-dasharray="6 4"/>'
        )
        parts.append(
            f'<text x="{W-PAD_R}" y="{yy-5:.1f}" font-size="10" fill="{clr}" '
            f'text-anchor="end">{label}</text>'
        )

    # 折线（按缺失分段）
    seg: list[str] = []
    for _, row in df.iterrows():
        if pd.isna(row["v"]):
            if seg:
                pts = " ".join(seg)
                parts.append(
                    f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>'
                )
                seg = []
            continue
        seg.append(f"{_x(row['d']):.1f},{_y(row['v']):.1f}")
    if seg:
        parts.append(
            f'<polyline points="{" ".join(seg)}" fill="none" stroke="{color}" stroke-width="1.8"/>'
        )

    # 信号标记
    for md, mlabel in (markers or []):
        md = pd.Timestamp(md)
        if md < dmin or md > dmax:
            continue
        xx = _x(md)
        parts.append(
            f'<line x1="{xx:.1f}" y1="{PAD_T}" x2="{xx:.1f}" y2="{H-PAD_B}" '
            f'stroke="#dc2626" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.8"/>'
        )
        parts.append(
            f'<circle cx="{xx:.1f}" cy="{PAD_T+6}" r="3" fill="#dc2626">'
            f"<title>{mlabel}</title></circle>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _table(df: pd.DataFrame, max_rows: int = 40) -> str:
    if df is None or len(df) == 0:
        return "<p class='muted'>（无数据）</p>"
    d = df.head(max_rows).copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:,.4f}")
        elif pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d")
    return d.to_html(index=False, classes="tbl", border=0, escape=False)


def _read_csv(path: Path) -> pd.DataFrame | None:
    """读取结果 CSV；文件不存在或为空时返回 None（不报错、不造假数据）。"""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None


def render(results_dir: Path, out_html: Path) -> None:
    summary_p = results_dir / "summary.json"
    summary = json.loads(summary_p.read_text(encoding="utf-8")) if summary_p.exists() else {}

    ind_b = results_dir / "indicator_B.csv"
    ind_a = results_dir / "indicator_A.csv"
    ev_b = results_dir / "events_B.csv"
    ev_a = results_dir / "events_A.csv"
    sens = results_dir / "sensitivity.csv"

    charts: list[str] = []
    if ind_b.exists():
        dfb = pd.read_csv(ind_b)
        marks = []
        ev = _read_csv(ev_b)
        if ev is not None and "signal_date" in ev.columns:
            marks = [(str(x), f"事件 {i+1}") for i, x in enumerate(ev["signal_date"])]
        charts.append(_svg_line_chart(
            dfb["date"], dfb["price"], title="沪深300 收盘价（价格指数口径，月频）",
            ylabel="点位", markers=marks, color="#111827"))
        charts.append(_svg_line_chart(
            dfb["date"], dfb["spread_pct"],
            title="假设B：盈利收益率 − 10年期国债收益率（百分点）",
            ylabel="利差", markers=marks))
        charts.append(_svg_line_chart(
            dfb["date"], dfb["percentile"],
            title="假设B：股债利差历史百分位（扩展窗口）", ylabel="百分位",
            markers=marks, hlines=[(90.0, "90% 阈值", "#dc2626")], color="#059669"))
    if ind_a.exists():
        dfa = pd.read_csv(ind_a)
        charts.append(_svg_line_chart(
            dfa["date"], dfa["spread_pct"],
            title="假设A：过去5年股票年化收益 − 10年期国债收益率（百分点）",
            ylabel="利差", color="#7c3aed"))

    cov = summary.get("data", {}).get("coverage", {})
    verdicts = summary.get("verdicts", {})

    kpis = []
    for k, v in verdicts.items():
        kpis.append(
            f'<div class="card"><div class="k">假设 {k} 结论</div>'
            f'<div class="v">{v.get("label","—")}</div>'
            f'<div class="s">样本内超额 {_fmt(v.get("is_mean_difference"))} ｜ '
            f'样本外超额 {_fmt(v.get("oos_mean_difference"))} ｜ '
            f'样本外事件 {v.get("n_oos_events")}</div></div>'
        )
    kpis.append(
        f'<div class="card"><div class="k">面板观测数</div>'
        f'<div class="v">{cov.get("n_obs","—")}</div>'
        f'<div class="s">{cov.get("date_min","—")} ~ {cov.get("date_max","—")}</div></div>'
    )
    for c in ("price", "pe", "bond_10y"):
        key = f"coverage_{c}"
        if key in cov:
            kpis.append(
                f'<div class="card"><div class="k">{c} 覆盖率</div>'
                f'<div class="v">{cov[key]*100:.1f}%</div>'
                f'<div class="s">缺失率 {(1-cov[key])*100:.1f}%</div></div>'
            )

    sens_html = ""
    if sens.exists():
        s = pd.read_csv(sens)
        s = s[s["segment"].isin(["is", "oos"])]
        cols = [c for c in ["experiment", "indicator", "lookback_years", "threshold_pct",
                            "segment", "window_td", "n_signals", "signal_mean",
                            "baseline_mean", "mean_difference"] if c in s.columns]
        sens_html = _table(s[cols].sort_values(
            ["indicator", "threshold_pct", "segment", "window_td"]), max_rows=100)

    ev_tables = ""
    for name, label in ((ev_a, "假设A"), (ev_b, "假设B")):
        e = _read_csv(name)
        if e is None or len(e) == 0:
            ev_tables += f"<h3>{label} 全部信号事件（0 个）</h3><p class='muted'>（无满足规则的信号事件）</p>"
            continue
        cols = [c for c in ["event_id", "signal_date", "exec_date", "spread_at_signal",
                            "percentile_at_signal", "pe_at_signal", "bond_10y_at_signal",
                            "ret_20td", "ret_60td", "ret_120td", "ret_250td",
                            "dd_250td", "immature_any"] if c in e.columns]
        ev_tables += f"<h3>{label} 全部信号事件（{len(e)} 个）</h3>" + _table(e[cols], max_rows=100)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>股债利差研究仪表盘</title>
<style>
body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;
background:#f9fafb;color:#111827}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:17px;margin:28px 0 10px}}
h3{{font-size:14px;margin:18px 0 8px;color:#374151}}
.muted{{color:#6b7280;font-size:13px}}
.kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;min-width:190px}}
.card .k{{font-size:12px;color:#6b7280}}
.card .v{{font-size:18px;font-weight:600;margin:4px 0}}
.card .s{{font-size:11px;color:#6b7280}}
.chart{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:8px;margin-bottom:14px}}
.tbl{{border-collapse:collapse;width:100%;font-size:12px;background:#fff}}
.tbl th,.tbl td{{border:1px solid #e5e7eb;padding:5px 8px;text-align:right}}
.tbl th{{background:#f3f4f6;text-align:center}}
.notice{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 14px;font-size:13px}}
</style></head><body><div class="wrap">
<h1>股债利差与长期市场机会识别 · 研究仪表盘</h1>
<p class="muted">标的 沪深300 ｜ 债券基准 中国10年期国债收益率 ｜
数据均为真实计算结果；本研究不构成投资建议。</p>
<div class="kpis">{''.join(kpis)}</div>
<div class="notice"><b>口径提示：</b>股票序列为价格指数口径（不含分红再投资）；
观测频率为月频，前瞻窗口 20/60/120/250 交易日按 252 交易日/年折算为 1/3/6/12 个月，属近似口径；
PE 口径未经指数公司官方确认。</div>
<h2>指标曲线</h2>
{''.join(f'<div class="chart">{c}</div>' for c in charts)}
<h2>信号事件与前瞻收益</h2>
{ev_tables}
<h2>参数敏感性（预先定义网格）</h2>
{sens_html}
<p class="muted" style="margin-top:24px">
⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。</p>
</div></body></html>"""
    out_html.write_text(html, encoding="utf-8")


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        return "—"
    return f"{float(v)*100:.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path,
                    default=Path("data/equity_bond_spread/results"))
    ap.add_argument("--out", type=Path,
                    default=Path("data/equity_bond_spread/results/index.html"))
    a = ap.parse_args()
    render(a.results_dir, a.out)
    print(f"仪表盘已生成：{a.out}")


if __name__ == "__main__":
    main()
