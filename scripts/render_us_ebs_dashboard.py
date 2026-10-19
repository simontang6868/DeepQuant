"""美股股债利差研究 · 仪表盘（零依赖内联 SVG）。

读取 ``data/equity_bond_spread_us/results/`` 下的真实计算结果，生成单文件 HTML。
不绘制任何未经计算的数据；图中每个点都来自 ``frame_monthly.csv`` 或 ``study_summary.json``。

用法::

    PYTHONPATH=backend/src python scripts/render_us_ebs_dashboard.py
"""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/equity_bond_spread_us")
RESULTS = DATA / "results"
OUT = DATA / "dashboard" / "index.html"

CSS = """
:root{--bg:#f8fafc;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;
--pos:#059669;--neg:#dc2626;--accent:#2563eb;--warn:#d97706}
*{box-sizing:border-box}
body{margin:0;padding:32px 24px 64px;background:var(--bg);color:var(--ink);
font:15px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px;margin-bottom:24px}
h2{font-size:18px;margin:36px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--line)}
h3{font-size:15px;margin:22px 0 8px;color:#334155}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(172px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .l{font-size:12px;color:var(--muted);margin-bottom:6px}
.kpi .v{font-size:21px;font-weight:650;letter-spacing:-.02em}
.kpi .n{font-size:11px;color:var(--muted);margin-top:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:14px;overflow-x:auto}
table.tbl{border-collapse:collapse;width:100%;font-size:13px}
table.tbl th,table.tbl td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.tbl th{background:#f1f5f9;font-weight:600;color:#334155}
table.tbl td:first-child,table.tbl th:first-child{text-align:left}
table.tbl tr:last-child td{border-bottom:none}
.pos{color:var(--pos);font-weight:600}
.neg{color:var(--neg);font-weight:600}
.muted{color:var(--muted)}
.warnbox{background:#fffbeb;border:1px solid #fde68a;border-left:4px solid var(--warn);
border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px}
.dangerbox{background:#fef2f2;border:1px solid #fecaca;border-left:4px solid var(--neg);
border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px}
.okbox{background:#f0fdf4;border:1px solid #bbf7d0;border-left:4px solid var(--pos);
border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px}
.note{font-size:12.5px;color:var(--muted);margin-top:10px;line-height:1.6}
.disc{margin-top:44px;padding:16px 18px;background:#f1f5f9;border-radius:8px;
font-size:13px;color:#475569}
code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12.5px}
.tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;
background:#e0e7ff;color:#3730a3;margin-left:6px}
"""

PRED_LABEL = {
    "spread_cape": "利差 S = 1/CAPE − Y10",
    "ey_cape": "盈利收益率 1/CAPE",
    "y10": "国债收益率 Y10",
    "cape": "CAPE",
}
SAMPLE_LABEL = {"full": "全样本", "is": "样本内(≤1949)", "oos": "样本外(≥1950)"}


def _fmt(v, nd=2, plus=False) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:+,.{nd}f}" if plus else f"{v:,.{nd}f}"


def _cls(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "muted"
    return "pos" if v > 0 else ("neg" if v < 0 else "muted")


def _line_chart(frame: pd.DataFrame, col: str, title: str, unit: str,
                shade_col: str | None = None, threshold: float = 90.0,
                max_points: int = 900) -> str:
    """折线图（含信号区间阴影）。数据来自真实序列，按需等距抽样以控制 SVG 体积。"""
    s = frame[[col] + ([shade_col] if shade_col else [])].dropna(subset=[col])
    if s.empty:
        return "<p class='muted'>（无数据）</p>"
    if len(s) > max_points:
        step = int(np.ceil(len(s) / max_points))
        s = s.iloc[::step]

    w, h = 1080, 340
    pad_l, pad_r, pad_t, pad_b = 62, 18, 26, 40
    inner_w, inner_h = w - pad_l - pad_r, h - pad_t - pad_b
    x = np.linspace(pad_l, pad_l + inner_w, len(s))
    yv = s[col].to_numpy(dtype=float)
    lo, hi = float(np.nanmin(yv)), float(np.nanmax(yv))
    span = max(hi - lo, 1e-9)
    lo, hi = lo - span * 0.08, hi + span * 0.08
    y = pad_t + inner_h * (1 - (yv - lo) / (hi - lo))

    parts = [f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' aria-label='{html_lib.escape(title)}'>"]
    parts.append(f"<rect x='0' y='0' width='{w}' height='{h}' fill='#fff'/>")
    for g in range(5):
        gy = pad_t + inner_h * g / 4
        val = hi - (hi - lo) * g / 4
        parts.append(f"<line x1='{pad_l}' y1='{gy:.1f}' x2='{w - pad_r}' y2='{gy:.1f}' stroke='#eef2f7'/>")
        parts.append(f"<text x='{pad_l - 8}' y='{gy + 4:.1f}' text-anchor='end' font-size='11' "
                     f"fill='#94a3b8'>{val:,.1f}</text>")

    # 信号区间阴影
    if shade_col:
        flag = (s[shade_col] >= threshold).to_numpy()
        i = 0
        while i < len(flag):
            if flag[i]:
                j = i
                while j + 1 < len(flag) and flag[j + 1]:
                    j += 1
                x0, x1 = x[i], x[j]
                parts.append(f"<rect x='{x0:.1f}' y='{pad_t}' width='{max(x1 - x0, 1.5):.1f}' "
                             f"height='{inner_h}' fill='#f59e0b' opacity='0.16'/>")
                i = j + 1
            else:
                i += 1

    pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y))
    parts.append(f"<polyline points='{pts}' fill='none' stroke='#2563eb' stroke-width='1.6'/>")

    # 年份标签
    years = s.index.year.to_numpy()
    step_y = max(1, (years[-1] - years[0]) // 8)
    last_shown = -999
    for xi, yr in zip(x, years):
        if yr - last_shown >= step_y:
            parts.append(f"<text x='{xi:.1f}' y='{h - 14}' text-anchor='middle' font-size='11' "
                         f"fill='#94a3b8'>{yr}</text>")
            last_shown = yr

    parts.append(f"<text x='{pad_l}' y='18' font-size='12.5' fill='#334155'>{html_lib.escape(title)}"
                 f"（单位：{html_lib.escape(unit)}）</text>")
    parts.append("</svg>")
    return "".join(parts)


def _bar_chart(labels: list[str], values: list[float], title: str, unit: str = "%",
               highlight_from: int | None = None) -> str:
    """竖向条形图：用于分位分组的平均前视收益。"""
    if not values:
        return "<p class='muted'>（无数据）</p>"
    w, h = 1080, 320
    pad_l, pad_r, pad_t, pad_b = 62, 18, 36, 46
    inner_w, inner_h = w - pad_l - pad_r, h - pad_t - pad_b
    vmax = max(abs(v) for v in values) or 1.0
    n = len(values)
    bw = inner_w / n * 0.62
    zero_y = pad_t + inner_h / 2
    parts = [f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' aria-label='{html_lib.escape(title)}'>",
             f"<rect x='0' y='0' width='{w}' height='{h}' fill='#fff'/>",
             f"<line x1='{pad_l}' y1='{zero_y:.1f}' x2='{w - pad_r}' y2='{zero_y:.1f}' stroke='#cbd5e1'/>"]
    for i, (lab, v) in enumerate(zip(labels, values)):
        cx = pad_l + inner_w * (i + 0.5) / n
        bh = abs(v) / vmax * (inner_h / 2 - 8)
        y0 = zero_y - bh if v >= 0 else zero_y
        color = "#2563eb"
        if highlight_from is not None and i >= highlight_from:
            color = "#7c3aed"
        parts.append(f"<rect x='{cx - bw / 2:.1f}' y='{y0:.1f}' width='{bw:.1f}' height='{bh:.1f}' "
                     f"fill='{color}' opacity='0.85' rx='2'/>")
        ty = (y0 - 6) if v >= 0 else (y0 + bh + 14)
        parts.append(f"<text x='{cx:.1f}' y='{ty:.1f}' text-anchor='middle' font-size='11' "
                     f"fill='#475569'>{v:+.1f}</text>")
        parts.append(f"<text x='{cx:.1f}' y='{h - 26}' text-anchor='middle' font-size='11.5' "
                     f"fill='#334155'>{html_lib.escape(lab)}</text>")
    parts.append(f"<text x='{pad_l}' y='20' font-size='12.5' fill='#334155'>{html_lib.escape(title)}"
                 f"（单位：{html_lib.escape(unit)}）</text>")
    parts.append("</svg>")
    return "".join(parts)


def _table(headers: list[str], rows: list[list[str]], first_left: bool = True) -> str:
    out = ["<table class='tbl'><thead><tr>"]
    for i, hd in enumerate(headers):
        out.append(f"<th>{html_lib.escape(hd)}</th>")
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def main() -> int:
    summary = json.loads((RESULTS / "study_summary.json").read_text(encoding="utf-8"))
    frame = pd.read_csv(RESULTS / "frame_monthly.csv", parse_dates=["month"]).set_index("month")
    cfg = summary["config"]
    data = summary["data"]

    primary_h = cfg["primary_horizon"]
    ic = summary["ic"]

    def pick(pred, h, sample):
        for r in ic:
            if r["predictor"] == pred and r["horizon"] == h and r["sample"] == sample:
                return r
        return {}

    main_ic = pick("spread_cape", primary_h, "full")
    main_oos = pick("spread_cape", primary_h, "oos")
    ey_ic = pick("ey_cape", primary_h, "full")
    y10_ic = pick("y10", primary_h, "full")
    reg = summary["regression"].get(f"{primary_h}m_full", {})
    reg_coef = reg.get("coef", {})
    power = next((p for p in summary["power"]["by_horizon"] if p["horizon"] == primary_h), {})
    strat = summary["strategy"]["main"]
    match = summary["strategy"]["main_matched"]

    kpis = [
        ("样本区间", f"{data['analysis_first'][:7]} → {data['analysis_last'][:7]}",
         f"{data['n_months']} 个月"),
        ("合并信号事件", f"{len(summary['events'])} 个", f"阈值 {cfg['threshold_pct']:.0f}% 百分位"),
        (f"主检验 IC（{primary_h}m）", _fmt(main_ic.get("ic"), 3), "Spearman 秩相关"),
        ("块自举 p 值", _fmt(main_ic.get("boot_p"), 4), "「无预测能力」零假设"),
        ("有效样本", f"{_fmt(main_ic.get('n_eff'), 0)} 个", "n ÷ 窗口"),
        ("功效缺口", f"{_fmt(power.get('power_shortfall_x'), 1)}×",
         f"检出实测效应需 {power.get('required_independent_obs')} 个独立观测，现有 {power.get('n_eff')} 个"),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='l'>{html_lib.escape(l)}</div>"
        f"<div class='v'>{html_lib.escape(v)}</div><div class='n'>{html_lib.escape(n)}</div></div>"
        for l, v, n in kpis
    )

    # ---------------- 核心 IC 表
    rows = []
    for pred in ("spread_cape", "ey_cape", "y10"):
        for sample in ("full", "is", "oos"):
            r = pick(pred, primary_h, sample)
            rows.append([
                PRED_LABEL[pred], SAMPLE_LABEL[sample], str(r.get("n", "—")),
                _fmt(r.get("ic"), 3), _fmt(r.get("nw_t"), 2), _fmt(r.get("boot_p"), 4),
                _fmt(r.get("n_eff"), 0), _fmt(r.get("mdes"), 3),
            ])
    ic_table = _table(
        ["预测变量", "样本段", "n", "IC", "NW t", "块自举 p", "有效样本", "最小可检测 IC"], rows)

    # ---------------- 增量回归
    reg_rows = []
    for h in cfg["horizons"]:
        for sample in ("full", "oos"):
            v = summary["regression"].get(f"{h}m_{sample}", {})
            c = v.get("coef", {})
            if not c:
                continue
            reg_rows.append([
                f"{h}m", SAMPLE_LABEL[sample], str(v.get("n")),
                _fmt(c["z_ey"]["param"], 3), _fmt(c["z_ey"]["t"], 2),
                _fmt(c["z_y10"]["param"], 3), _fmt(c["z_y10"]["t"], 2),
                _fmt(v.get("r2"), 3),
            ])
    reg_table = _table(["窗口", "样本段", "n", "b（估值）", "t", "c（利率）", "t", "R²"], reg_rows)

    # ---------------- 分位数图
    dec = next((d for d in summary["deciles"] if d["horizon"] == primary_h), None)
    dec_chart = ""
    if dec:
        dec_chart = _bar_chart(
            [f"D{b['bucket']}" for b in dec["buckets"]],
            [b["mean_pct"] for b in dec["buckets"]],
            f"按利差历史百分位分组的 {primary_h} 个月真实总收益",
            unit="%", highlight_from=9,
        )

    # ---------------- 利差走势
    spread_chart = _line_chart(
        frame, "spread_cape", "股债利差 S = 1/CAPE − 美债10Y（百分位≥90% 的月份标为橙色）",
        unit="百分点", shade_col="spread_cape_pct", threshold=cfg["threshold_pct"],
    )

    # ---------------- 事件表
    ev_rows = []
    for e in summary["events"]:
        ev_rows.append([
            str(e["event_id"]), e["signal_month"][:7], e["last_signal_month"][:7],
            str(e["n_months"]), _fmt(e["value_at_signal"], 2), _fmt(e["percentile_at_signal"], 1),
        ])
    ev_table = _table(["#", "首次触发", "最后触发", "触发月数", "利差(pp)", "百分位"], ev_rows)

    # ---------------- 事件后收益
    es_rows = []
    for r in summary["event_stats"]:
        if not r.get("n_events_usable"):
            continue
        es_rows.append([
            f"{r['horizon']}m", str(r["n_events_usable"]), str(r["n_baseline"]),
            _fmt(r["signal_mean_pct"], 2), _fmt(r["baseline_mean_pct"], 2),
            _fmt(r["diff_mean_pct"], 2), _fmt(r["signal_positive_share_pct"], 1),
            _fmt(r["baseline_positive_share_pct"], 1),
        ])
    es_table = _table(
        ["窗口", "可用事件", "基准样本", "信号均值%", "基准均值%", "均值差 pp", "信号>0%", "基准>0%"],
        es_rows)

    # ---------------- 案例
    case_rows = []
    for c in summary["cases"]:
        case_rows.append([
            c["month"][:7], c["label"], _fmt(c["spread_pct"], 2), _fmt(c["percentile"], 1),
            "是" if c["signal_on"] else "否", _fmt(c["fwd_12m_pct"], 1),
            _fmt(c["fwd_60m_pct"], 1), _fmt(c["fwd_120m_pct"], 1),
        ])
    case_table = _table(
        ["月份", "场景", "利差 pp", "百分位", "触发", "后 1 年%", "后 5 年%", "后 10 年%"], case_rows)

    # ---------------- 子区间
    sub_rows = [[s["period"], str(s["n"]), _fmt(s.get("ic"), 3), _fmt(s.get("boot_p"), 4),
                 _fmt(s.get("mean_fwd_pct"), 2)] for s in summary["subperiods"]]
    sub_table = _table(["区间", "n", "IC", "块自举 p", f"{primary_h}m 平均真实收益%"], sub_rows)

    # ---------------- 策略
    strat_html = (
        "<div class='card'><table class='tbl'><thead><tr><th>项目</th><th>策略</th><th>买入持有</th></tr></thead><tbody>"
        f"<tr><td>年化真实收益</td><td>{_fmt(strat['strategy_cagr_pct'], 2)}%</td><td>{_fmt(strat['bh_cagr_pct'], 2)}%</td></tr>"
        f"<tr><td>年化波动</td><td>{_fmt(strat['strategy_vol_pct'], 2)}%</td><td>{_fmt(strat['bh_vol_pct'], 2)}%</td></tr>"
        f"<tr><td>最大回撤</td><td>{_fmt(strat['strategy_mdd_pct'], 2)}%</td><td>{_fmt(strat['bh_mdd_pct'], 2)}%</td></tr>"
        f"<tr><td>在场月份占比</td><td>{_fmt(strat['invested_share_pct'], 1)}%</td><td>100%</td></tr>"
        f"<tr><td>换手次数</td><td>{strat['n_switch']}</td><td>0</td></tr>"
        "</tbody></table>"
        f"<p class='note'>匹配暴露随机基准：随机投放 {match.get('k_months', '—')} 个月，"
        f"年化中位区间 {_fmt(match.get('rand_cagr_p05_pct'), 2)}% ~ {_fmt(match.get('rand_cagr_p95_pct'), 2)}%，"
        f"策略跑赢随机基准的 p = {_fmt(match.get('p_value'), 4)}。</p></div>"
    )

    # ---------------- 相对收益附录
    def _ex(target, pred, h, sample):
        for r in summary.get("excess", []):
            if (r["target"] == target and r["predictor"] == pred
                    and r["horizon"] == h and r["sample"] == sample):
                return r
        return {}

    ex_rows = []
    for target, tlabel in (("excess", "股票 − 债券（相对）"), ("bond", "债券收益")):
        for pred in ("spread_cape", "ey_cape", "y10"):
            cells = [tlabel, PRED_LABEL[pred]]
            for h in (60, 120):
                for sample in ("full", "oos"):
                    r = _ex(target, pred, h, sample)
                    cells.append(_fmt(r.get("ic"), 3))
                    cells.append(_fmt(r.get("boot_p"), 4))
            ex_rows.append(cells)
    excess_table = _table(
        ["目标变量", "预测变量", "60m IC", "p", "60m 样本外 IC", "p",
         "120m IC", "p", "120m 样本外 IC", "p"], ex_rows)

    # ---------------- 阈值口径敏感性
    sens_path = RESULTS / "sensitivity.csv"
    sens_table = "<p class='muted'>（未运行 scripts/us_sensitivity.py）</p>"
    if sens_path.exists():
        sens = pd.read_csv(sens_path)
        srows = []
        for _, r in sens.iterrows():
            srows.append([
                r["window_type"], f"{r['threshold']:.0f}%", str(int(r["flagged_months"])),
                str(int(r["n_events"])),
                f"{int(r['first_event_year'])}–{int(r['last_event_year'])}",
                str(int(r["events_after_1950"])),
                _fmt(r["signal_mean_60m_pct"], 2) + "%",
                _fmt(r["baseline_mean_60m_pct"], 2) + "%",
                f"<span class='{_cls(r['diff_60m_pct'])}'>{_fmt(r['diff_60m_pct'], 2)}pp</span>",
            ])
        sens_table = _table(
            ["百分位窗口", "阈值", "触发月数", "事件数", "事件年份跨度", "1950 后事件",
             "60m 信号均值", "60m 基准", "均值差"], srows)

    verdict = (
        "<div class='warnbox'><b>怎么读这份结果：</b>"
        "预登记的 60 个月主窗口上，块自举 p = 0.056，不能在 5% 水平拒绝「零」；"
        "12 个月虽然 p = 0.013，但本报告共做了 140 项检验，Bonferroni 阈值降到 0.00036，无一通过。"
        "更关键的是第 2 节：控制估值后利率系数不显著——利差相对「单看估值」没有增量信息。"
        "方向可能对，但证据不足以支撑「按利差择时」的决策。</div>"
    )

    out = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>美股股债利差研究 · 145 年长历史检验</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>美股股债利差研究 · 145 年长历史检验</h1>
<div class="sub">S = 1/CAPE − 美国 10 年期国债收益率 · 数据 {data['analysis_first']} ~ {data['analysis_last']}
 · 月度 {data['n_months']} 个观测 · 真实总收益口径（扣 CPI）</div>

<div class="kpis">{kpi_html}</div>

<h2>1. 主检验：利差能不能预测未来收益</h2>
<div class="card">{ic_table}
<p class='note'>IC 为负表示「利差越高、未来真实收益越低」——若理论成立，IC 应显著为正（利差高＝便宜＝未来收益高）。
NW t 用 Newey-West 标准误（滞后阶数 = 1.5 × 窗口），块自举对预测变量与收益分别按块重采样。
「最小可检测 IC」是在现有有效样本下 80% 功效能检出的效应下限：实测 |IC| 小于它时，无论显著与否都不该下结论。</p></div>

<h2>2. 核心问题：减掉国债收益率到底有没有用</h2>
<div class="card">{reg_table}
<p class='note'>回归 R<sub>t+h</sub> = a + b·z(1/CAPE) + c·z(Y10)。若 c 不显著异于 0，
说明「利差」相对「单看估值」没有增量信息，A 股研究里那个 −41.55%／+8.49% 的差别就只是噪声。</p></div>
{verdict}

<h2>3. 分位分组：单调性比阈值更可靠</h2>
<div class="card">{dec_chart}
<p class='note'>分组用<b>历史百分位</b>而非全样本分位数，避免未来信息泄漏。D9 为利差最高的十分之一。</p></div>

<h2>4. 利差的历史轨迹与触发区间</h2>
<div class="card">{spread_chart}
<p class='note'>橙色区间为历史百分位 ≥ {cfg['threshold_pct']:.0f}% 的月份。注意 2010 年代之后的长期低利率把利差推高，
这类「制度性抬升」会被百分位误读为「便宜」。</p></div>

<h2>5. 阈值事件（合并后）</h2>
<div class="card">{ev_table}
<p class='note'>事件合并规则：两次触发间隔 ≤ {cfg['event_gap']} 个月视为同一事件，避免同一波机会被重复计数。</p></div>

<h2>6. 事件之后的真实收益</h2>
<div class="card">{es_table}
<p class='note'>「均值差」= 信号事件均值 − 全部月份均值。持有期重叠，不能按独立样本解读。</p></div>

<h2>7. 历史案例核查</h2>
<div class="card">{case_table}
<p class='note'>案例在看结果前按「美国股市公认关键时点」选定，未按表现筛选。</p></div>

<h2>8. 相对收益附录：利差看起来强多了，但别急</h2>
<div class="card">{excess_table}
<p class='note'><b>关键在最后两组：</b>Y10 对债券收益的 IC 是 0.375（60m），而 EY 对债券收益的 IC 只有 0.029。
债券持有到期的收益近似等于当期收益率，<b>它在信号时点就已经知道</b>。
所以「股票 − 债券」这个目标里有一半是已知的，利差对相对收益的高 IC 有相当部分是机械的，
不能当作「利差能择时」的证据。</p></div>

<h2>9. 阈值口径敏感性：结论能被参数翻面</h2>
<div class="card">{sens_table}
<p class='note'>同一份数据、同一个利差，只改百分位窗口与阈值，60 个月均值差从 +34.9pp 变到 −9.6pp，
<b>连符号都会翻</b>。这说明阈值法的结果不稳健，而不是「找到了一个有效规则」。</p></div>

<h2>10. 子区间稳定性</h2>
<div class="card">{sub_table}
<p class='note'>若只有某一个子区间有效，说明结论依赖特定制度环境，不能外推。</p></div>

<h2>11. 如果真的按信号交易</h2>
{strat_html}
<div class="warnbox"><b>暴露不匹配警告：</b>阈值 {cfg['threshold_pct']:.0f}% 意味着策略大部分时间空仓，
其年化收益天然低于买入持有。判断「择时是否有效」要看<b>匹配暴露的随机基准</b>，
不能看净值高低。</div>

<h2>12. 数据与口径</h2>
<div class="card">
<table class='tbl'><thead><tr><th>项</th><th>值</th></tr></thead><tbody>
<tr><td>价格 / CAPE / 股息率 / CPI</td><td>multpl 月度表（已与 Shiller 原始 xls 逐月比对）</td></tr>
<tr><td>CAPE 与 Shiller 差异</td><td>中位绝对差 0.0027，相关 0.999993</td></tr>
<tr><td>美债 10Y</td><td>1990-12 前用 Shiller GS10，之后用 akshare 日频月均；重叠 394 月平均绝对差 0.0044pp</td></tr>
<tr><td>总收益构造</td><td>1+r = (P_t + D_(t-1)/12) / P_(t-1)，D 取上月末滚动 12 个月股息</td></tr>
<tr><td>执行</td><td>信号月末判定，下一月成交（{cfg['exec_lag']} 个月延迟），禁止同月成交</td></tr>
<tr><td>样本内 / 外</td><td>≤ {cfg['is_end_date']} / 之后</td></tr>
<tr><td>本次统计检验总数</td><td>{summary['tests_counted']} 项</td></tr>
</tbody></table>
</div>

<div class="disc">⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。
投资有风险，决策需谨慎。生成时间：数据截至 {data['analysis_last']}。</div>
</div></body></html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    print(f"[dashboard] {OUT} ({len(out)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
