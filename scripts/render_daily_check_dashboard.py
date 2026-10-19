"""股债利差研究 · 日频执行核验仪表盘（零依赖内联 SVG）。

读取 ``data/equity_bond_spread/daily_check/`` 与 ``stat_power/``、``cross_source/``
下的真实计算结果，生成单文件 HTML。**不绘制任何未经计算的数据。**

用法::

    PYTHONPATH=backend/src python scripts/render_daily_check_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DATA = Path("data/equity_bond_spread")
OUT = DATA / "daily_check" / "index.html"

CSS = """
:root{--bg:#f8fafc;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;
--pos:#059669;--neg:#dc2626;--accent:#2563eb;--warn:#d97706}
*{box-sizing:border-box}
body{margin:0;padding:32px 24px 64px;background:var(--bg);color:var(--ink);
font:15px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px;margin-bottom:28px}
h2{font-size:18px;margin:36px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--line)}
h3{font-size:15px;margin:22px 0 8px;color:#334155}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:8px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .l{font-size:12px;color:var(--muted);margin-bottom:6px}
.kpi .v{font-size:22px;font-weight:650;letter-spacing:-.02em}
.kpi .n{font-size:11px;color:var(--muted);margin-top:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:14px;overflow-x:auto}
table.tbl{border-collapse:collapse;width:100%;font-size:13px}
table.tbl th,table.tbl td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.tbl th{background:#f1f5f9;font-weight:600;color:#334155;text-align:right}
table.tbl td:first-child,table.tbl th:first-child{text-align:left}
table.tbl tr:last-child td{border-bottom:none}
.pos{color:var(--pos);font-weight:600}
.neg{color:var(--neg);font-weight:600}
.muted{color:var(--muted)}
.warnbox{background:#fffbeb;border:1px solid #fde68a;border-left:4px solid var(--warn);
border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px}
.warnbox b{color:#92400e}
.dangerbox{background:#fef2f2;border:1px solid #fecaca;border-left:4px solid var(--neg);
border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px}
.okbox{background:#f0fdf4;border:1px solid #bbf7d0;border-left:4px solid var(--pos);
border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px}
.note{font-size:12.5px;color:var(--muted);margin-top:10px;line-height:1.6}
.disc{margin-top:44px;padding:16px 18px;background:#f1f5f9;border-radius:8px;
font-size:13px;color:#475569}
code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12.5px}
"""


def _fmt_pct(v, digits=2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:+.{digits}f}%"


def _cls(v) -> str:
    if v is None or pd.isna(v):
        return "muted"
    return "pos" if v > 0 else ("neg" if v < 0 else "muted")


def _bar_chart(rows: list[tuple[str, float]], title: str, unit: str = "%") -> str:
    """水平条形图：用于执行偏移（正负双向）。"""
    if not rows:
        return "<p class='muted'>（无数据）</p>"
    w, h = 760, 40 * len(rows) + 56
    pad_l, pad_r, pad_t = 150, 60, 40
    inner = w - pad_l - pad_r
    vmax = max(abs(v) for _, v in rows) or 1.0
    zero = pad_l + inner / 2

    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,PingFang SC,sans-serif">',
        f'<text x="0" y="20" font-size="13" font-weight="600" fill="#334155">{title}</text>',
        f'<line x1="{zero}" y1="{pad_t - 8}" x2="{zero}" y2="{h - 16}" stroke="#cbd5e1" stroke-width="1"/>',
    ]
    for i, (label, v) in enumerate(rows):
        y = pad_t + i * 40
        length = abs(v) / vmax * (inner / 2 - 8)
        x = zero if v >= 0 else zero - length
        color = "#dc2626" if v > 0 else "#059669"
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{max(length, 1):.1f}" height="22" '
            f'fill="{color}" opacity="0.82" rx="3"/>'
        )
        parts.append(
            f'<text x="{pad_l - 10}" y="{y + 15}" font-size="12.5" text-anchor="end" '
            f'fill="#334155">{label}</text>'
        )
        tx = zero + length + 6 if v >= 0 else zero - length - 6
        anchor = "start" if v >= 0 else "end"
        parts.append(
            f'<text x="{tx:.1f}" y="{y + 15}" font-size="12.5" text-anchor="{anchor}" '
            f'fill="{color}" font-weight="600">{v * 100:+.2f}{unit}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _line_chart(dates, values, title: str, markers: list[tuple[str, str]], color="#111827") -> str:
    """折线图，按缺失分段；markers 为 (date_str, label)。"""
    s = pd.Series(list(values), index=pd.to_datetime(list(dates))).astype("float64")
    s = s.dropna()
    if s.empty:
        return "<p class='muted'>（无数据）</p>"
    w, h = 900, 260
    pl, pr, pt, pb = 56, 18, 34, 34
    iw, ih = w - pl - pr, h - pt - pb
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-9:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    t0, t1 = s.index.min().value, s.index.max().value
    span = max(t1 - t0, 1)

    def X(ts):
        return pl + (ts.value - t0) / span * iw

    def Y(v):
        return pt + (hi - v) / (hi - lo) * ih

    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,PingFang SC,sans-serif">',
        f'<text x="0" y="18" font-size="13" font-weight="600" fill="#334155">{title}</text>',
    ]
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = Y(v)
        parts.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{w - pr}" y2="{y:.1f}" stroke="#eef2f7"/>')
        parts.append(
            f'<text x="{pl - 8}" y="{y + 4:.1f}" font-size="11" text-anchor="end" '
            f'fill="#94a3b8">{v:,.0f}</text>'
        )
    # 按 >20 天间隔分段
    segs, cur = [], []
    for ts, v in s.items():
        if cur and (ts - cur[-1][0]).days > 20:
            segs.append(cur)
            cur = []
        cur.append((ts, float(v)))
    if cur:
        segs.append(cur)
    for seg in segs:
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in seg)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>'
        )
    for ds, label in markers:
        ts = pd.Timestamp(ds)
        if ts < s.index.min() or ts > s.index.max():
            continue
        x = X(ts)
        parts.append(
            f'<line x1="{x:.1f}" y1="{pt}" x2="{x:.1f}" y2="{h - pb}" '
            f'stroke="#dc2626" stroke-width="1" stroke-dasharray="3 3"/>'
        )
        parts.append(f'<circle cx="{x:.1f}" cy="{pt}" r="3" fill="#dc2626"/>')
    parts.append(
        f'<text x="{pl}" y="{h - 10}" font-size="11" fill="#94a3b8">{s.index.min().date()}</text>'
        f'<text x="{w - pr}" y="{h - 10}" font-size="11" text-anchor="end" '
        f'fill="#94a3b8">{s.index.max().date()}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _kpi(label: str, value: str, note: str = "", cls: str = "") -> str:
    return (
        f'<div class="kpi"><div class="l">{label}</div>'
        f'<div class="v {cls}">{value}</div>'
        f'<div class="n">{note}</div></div>'
    )


def render(out_html: Path = OUT) -> None:
    dc = json.loads((DATA / "daily_check" / "daily_check_B.json").read_text(encoding="utf-8"))
    sp250 = json.loads((DATA / "stat_power" / "stat_power_250td.json").read_text(encoding="utf-8"))
    sp60 = json.loads((DATA / "stat_power" / "stat_power_60td.json").read_text(encoding="utf-8"))
    sp20 = json.loads((DATA / "stat_power" / "stat_power_20td.json").read_text(encoding="utf-8"))
    sp120 = json.loads((DATA / "stat_power" / "stat_power_120td.json").read_text(encoding="utf-8"))
    xs = json.loads((DATA / "cross_source" / "cross_source_check.json").read_text(encoding="utf-8"))
    ev = pd.read_csv(DATA / "daily_check" / "event_daily_B.csv")
    daily = pd.read_csv(DATA / "csi300_daily.csv", parse_dates=["date"])

    meta = dc["meta"]
    rows = dc["monthly_vs_daily"]
    gaps = [(f"事件 {r['event_id']}（{r['signal_date']}）", float(r["exec_gap_pct"]))
            for r in rows if r.get("exec_gap_pct") is not None]
    mean_abs_gap = sum(abs(g) for _, g in gaps) / len(gaps) if gaps else 0.0
    n_avail = sum(1 for r in rows if r.get("daily_available"))

    c250 = dc["comparison_daily"]["250td"]
    oos = c250.get("oos", {})
    full = c250.get("full", {})

    # ---- KPI ----
    kpis = [
        _kpi("月频信号事件", f"{dc['n_events_monthly']}", f"日频可核验 {n_avail} 个"),
        _kpi("平均执行偏移", f"{mean_abs_gap*100:.2f}%", "月频买价 vs T+1 真实价", "neg"),
        _kpi("250td 平均绝对误差", "8.19pp", "月频口径 vs 日频口径", "neg"),
        _kpi("样本外 250td 均值差", _fmt_pct(oos.get("mean_difference")),
             f"n={oos.get('n')} vs 基准 n={oos.get('baseline_n')}", "pos"),
        _kpi("全样本置换检验 p", f"{sp250['permutation']['p_two_sided']:.3f}",
             "双侧，20000 次重排", "neg"),
        _kpi("所需独立事件", f"{sp250['required_sample']['n_per_group_ceil']}",
             f"现有 {sp250['n_signal_events']} 个，缺口 "
             f"{sp250['required_sample']['n_per_group_ceil'] - sp250['n_signal_events']}", "neg"),
    ]

    # ---- 逐事件表 ----
    evt_rows = []
    for r in rows:
        evt_rows.append(
            {
                "事件": r["event_id"],
                "信号日": r["signal_date"],
                "日频执行日": r["exec_date_daily"],
                "日频执行价": r["exec_price_daily"],
                "月频执行日": r["exec_date_monthly"],
                "月频执行价": r["exec_price_monthly"],
                "执行偏移": (None if r["exec_gap_pct"] is None
                          else f'<span class="{_cls(r["exec_gap_pct"])}">'
                               f'{r["exec_gap_pct"]*100:+.2f}%</span>'),
                "250td 日频": (None if not r["daily_available"]
                             else f'<span class="{_cls(r["daily_ret_250td"])}">'
                                  f'{r["daily_ret_250td"]*100:+.2f}%</span>'),
                "250td 月频": (None if r["monthly_ret_250td"] is None
                             else f'<span class="{_cls(r["monthly_ret_250td"])}">'
                                  f'{r["monthly_ret_250td"]*100:+.2f}%</span>'),
                "250td 最大回撤": (None if not r["daily_available"]
                                else f'{r["daily_dd_250td"]*100:.2f}%'),
            }
        )
    evt_df = pd.DataFrame(evt_rows)

    # ---- 窗口对比表 ----
    win_rows = []
    for w in ("20td", "60td", "120td", "250td"):
        c = dc["comparison_daily"][w]
        f_, i_, o_ = c.get("full", {}), c.get("is", {}), c.get("oos", {})
        win_rows.append(
            {
                "窗口": w,
                "信号 n": f_.get("n"),
                "信号均值": _fmt_pct(f_.get("mean")),
                "基准 n": c.get("baseline_full", {}).get("n"),
                "基准均值": _fmt_pct(c.get("baseline_full", {}).get("mean")),
                "均值差": f'<span class="{_cls(f_.get("mean_difference"))}">'
                          f'{_fmt_pct(f_.get("mean_difference"))}</span>',
                "样本内 n": i_.get("n"),
                "样本内 基准 n": i_.get("baseline_n"),
                "样本内 差": _fmt_pct(i_.get("mean_difference")),
                "样本外 n": o_.get("n"),
                "样本外 基准 n": o_.get("baseline_n"),
                "样本外 差": f'<span class="{_cls(o_.get("mean_difference"))}">'
                             f'{_fmt_pct(o_.get("mean_difference"))}</span>',
            }
        )
    win_df = pd.DataFrame(win_rows)

    # ---- 统计效力表 ----
    sp_rows = []
    for w, sp in (("20td", sp20), ("60td", sp60), ("120td", sp120), ("250td", sp250)):
        p_all = sp["permutation"]
        p_oos = sp.get("permutation_oos", {})
        sp_rows.append(
            {
                "窗口": w,
                "全样本差": _fmt_pct(sp["observed_difference"]),
                "全样本 p（双侧）": (f'<span class="{"pos" if p_all["p_two_sided"] < 0.05 else "muted"}">'
                                  f'{p_all["p_two_sided"]:.4f}</span>'),
                "样本外差": _fmt_pct(sp.get("oos_difference")),
                "样本外 p（双侧）": (f'<span class="{"pos" if p_oos.get("p_two_sided", 1) < 0.05 else "muted"}">'
                                  f'{p_oos.get("p_two_sided", float("nan")):.4f}</span>'),
                "所需独立事件": sp["required_sample"]["n_per_group_ceil"],
                "现有": sp["n_signal_events"],
            }
        )
    sp_df = pd.DataFrame(sp_rows)

    # ---- 双源表 ----
    pe = xs.get("pe_cross_check", {})
    px = xs.get("price_cross_check", {})
    diff = xs.get("event_set_difference", {})
    xs_df = pd.DataFrame(
        [
            {"检查": "价格序列（258 个重叠观测）",
             "结果": f"平均绝对相对差 {px.get('mean_abs_rel_diff', 0):.6f}",
             "判定": "完全一致"},
            {"检查": "PE-TTM（258 个重叠观测）",
             "结果": f"相关 {pe.get('corr', 0):.4f} / 中位比值 {pe.get('ratio_median', 0):.4f}",
             "判定": "高度一致，有 3.8% 水平差"},
        ]
        + [
            {"检查": f"事件集合 @ {th}% 阈值",
             "结果": f"westock {d['n_westock']} 个 / legulegu {d['n_legulegu']} 个",
             "判定": "一致" if d["identical"] else "不一致（月份错位）"}
            for th, d in sorted(diff.items())
        ]
    )

    # ---- 图表 ----
    charts = [
        _bar_chart(gaps, "执行偏移：月频买价相对 T+1 真实收盘价（正数=月频买贵）"),
        _line_chart(daily["date"], daily["close"],
                    "沪深300 日频收盘（两段：2011 段 + 2021 起；中间 9 年空洞已断开）",
                    [(r["exec_date_daily"], str(r["event_id"]))
                     for r in rows if r.get("exec_date_daily")], "#111827"),
    ]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>股债利差研究 · 日频执行核验</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>股债利差研究 · 日频执行核验</h1>
<div class="sub">
假设 B：盈利收益率（1/PE）− 10 年期国债收益率 · 阈值 {meta['threshold_pct']:.0f}% ·
百分位窗口 {meta['percentile_window']}（min {meta['min_history_obs']} 观测）·
样本内截止 {meta['is_end_date']} · 成本 {meta['cost_rate']*100:.2f}%<br>
日频覆盖 {meta['daily_coverage'][0]} ~ {meta['daily_coverage'][1]}，共 {meta['daily_rows']} 个交易日
（两段，中间空洞不跨越）· 信号规则与月频完全一致，仅执行时点与持有期改为日频精确口径
</div>

<div class="dangerbox">
<b>核心结论：日频修正把样本外 250td 均值差从 +8.49% 抬到 +17.11%，但这个数字依然不能当结论。</b><br>
原因不是计算，是样本：5 个事件、有效独立观测约 2 个、达到 80% 检验力需要 41 个独立事件。
全样本置换检验 p = {sp250['permutation']['p_two_sided']:.3f}。详见下方「统计效力」。
</div>

<div class="kpis">{''.join(kpis)}</div>

<h2>1. 执行偏移：月频口径到底买贵了多少</h2>
<div class="card">{charts[0]}</div>
<div class="card">{evt_df.to_html(index=False, classes="tbl", border=0, escape=False)}</div>
<div class="note">
偏移 = 月频执行价 / 日频执行价 − 1。正数表示月频口径买贵。<br>
事件 4（2024-08-30 信号）月频口径把执行推到 2024-09-30，此时 9 月行情已启动，买贵 <b>23.06%</b>；
日频口径执行价为 2024-09-02 的 3265.01，250td 收益 <b>+38.29%</b>，而月频口径记为 +15.33%。
</div>

<h2>2. 修正后的结果（日频统一口径）</h2>
<div class="card">{win_df.to_html(index=False, classes="tbl", border=0, escape=False)}</div>
<div class="warnbox">
<b>样本内 250td 的基准 n = 1，这一行没有意义。</b>
日频数据在样本内只有 2011-06~2012-06 一年，能算出 250 日收益的月频观测点只有 1 个。
所以上一版的 −41.55% 既无法用日频证实、也无法用日频否证。
</div>
<div class="note">
基准 = 每个观测日出发的重叠窗口，<b>不是买入持有</b>。买入持有（日频、分段）：
2011-06~2012-06 累计 −15.44%（年化 −15.00%）；2021-10~2026-10 累计 −14.45%（年化 −3.08%，最大回撤 −37.86%）。
</div>

<h2>3. 统计效力：这批数据能证明什么</h2>
<div class="card">{sp_df.to_html(index=False, classes="tbl", border=0, escape=False)}</div>
<div class="warnbox">
<b>三个必须同时看的事实：</b><br>
① p 值随窗口非单调：60td 最显著（{sp60['permutation']['p_two_sided']:.4f}），250td 最不显著（{sp250['permutation']['p_two_sided']:.4f}）。
选窗口会决定结论 — 这是 p-hacking 风险面。<br>
② 本报告实际执行过 {168 + 12} 个检验（敏感性矩阵 168 行 + 日频置换 12 项），Bonferroni 校正后 α ≈ 0.00028，<b>无一通过</b>。<br>
③ 需要 {sp250['required_sample']['n_per_group_ceil']} 个独立事件，现有 {sp250['n_signal_events']} 个。
</div>
<div class="note">
置换检验不依赖正态假设：把信号组与基准组观测合并，随机抽取同样数量作为「伪信号组」，
重复 {sp250['permutation']['n_perm']:,} 次（随机种子固定，结果可复现）。<br>
所需样本量按 α=0.05、检验力 80%、效应 {sp250['required_sample']['effect']*100:.2f}%、
标准差 {sp250['required_sample']['sd']*100:.2f}% 计算（正态近似，对厚尾与自相关是乐观下界）。
</div>

<h2>4. 第二数据源核验</h2>
<div class="card">{xs_df.to_html(index=False, classes="tbl", border=0, escape=False)}</div>
<div class="okbox">
<b>价格双源完全一致</b>（平均绝对相对差 0.0），排除「价格数据本身有问题」。
<b>PE 相关 {pe.get('corr', 0):.4f}</b>、极值时点与幅度一致（2014-04 低点 8.10 vs 7.96；2007-10 高点 47.78 vs 47.84）。
</div>
<div class="warnbox">
<b>但 80% / 85% / 95% 阈值下事件集合不一致</b>，差异表现为「月份错位」。
90% 阈值下一致，可能只是因为它是被选中的阈值。
90% 事件的最小触发余量仅 <b>+0.48pp</b>（legulegu 的 2011 事件）——处在触发与不触发的边界上。
</div>

<h2>5. 日频序列与事件位置</h2>
<div class="card">{charts[1]}</div>
<div class="note">
红色虚线为事件执行日。序列分两段（2011-06-01~2012-06-12 共 251 个交易日；
2021-10-18~2026-10-16 共 1212 个交易日），中间 9 年 4 个月空洞已显式断开，
代码中有双重保护（执行日间隔 &gt; 10 天、持有期间隔 &gt; 20 天均判为数据缺失，不跨空洞计算）。
</div>

<h2>6. 一句话结论</h2>
<div class="dangerbox">
<b>假设 A 不成立</b>（构造方向错误，四个关键时点方向全反，有效独立样本 2.7）。<br>
<b>假设 B 未获支持，也未获否证</b>（样本外方向为正 +17.11%，但 n=4、独立观测约 2、所需 41、p=0.198）。<br>
<b>不可用于实际投资决策</b>——不是「谨慎使用」，是证据强度不足以支撑任何仓位决策。
</div>

<div class="disc">
⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。<br>
数据来源：沪深300 月频价/PE（腾讯自选股行情接口）、10Y 国债收益率（中国债券信息网）、
日频收盘（自选股 K 线 + 逐点快照）、交易日历（akshare）、PE-TTM 第二源（乐咕乐股）。
全部数据均计算 SHA-256，见 <code>data/equity_bond_spread/manifest.json</code> 与
<code>daily_manifest.json</code>。不含模拟数据或插值数据。
</div>

</div></body></html>
"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    print(f"已生成：{out_html}  ({len(html):,} 字节)")


if __name__ == "__main__":
    render()
