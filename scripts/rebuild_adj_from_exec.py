#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P1-4：用「真不复权价 + 分红送转事件」自算前复权（ADJ），不用任何第三方复权序列。

为什么不用现成的复权序列（见 docs/EXEC_LAYER_AND_ADJ_DIAGNOSTIC_2026-10-20.md）：
  第三方 hfq/qfq 序列口径不透明。实测腾讯 hfq 带加性残留、且与不复权的股本基准不一致，
  导致 qfq_mult 在 43/52 只上日收益被压制（β 中位 0.91，最低 0.59），
  另有 2 只隐含负股息、3 只残留未抹平的跳变。
  唯一口径可控的做法：输入两类原始事实——真不复权价、每股派息/送股/转增——自己算。

构造式（对每个除权事件 s）：
  事件前一日收盘 P(s-1)（真不复权）
  每股派息   D_s = PRETAX_BONUS_RMB / 10        # 东财该字段单位是「元/10股」（茅台定标验证）
  每股送转   b_s = (BONUS_IT_RATIO + BONUS_RATIO) / 10   # 「股/10股」
  f_s = (P(s-1) - D_s) / (P(s-1) * (1 + b_s))
  ADJ(t) = EXEC(t) * Π_{s : s > t} f_s

  —— 前复权锚定最新日：最新日 f 乘积为空 = 1，故 ADJ(最新) == EXEC(最新)，与既有口径一致。

用法：
  python scripts/rebuild_adj_from_exec.py                      # 全池，写 data/qfq_rebuilt/
  python scripts/rebuild_adj_from_exec.py --codes 600519.SH    # 单只
  python scripts/rebuild_adj_from_exec.py --offline            # 只吃缓存，不联网
  python scripts/rebuild_adj_from_exec.py --verify-only        # 只校验已有产物

验收判据（全部机械可执行；文档 §6.4）：
  A1 无负价/零价
  A2 无事件相邻两日：ADJ 日收益 == EXEC 日收益（差 < 1e-9）
  A3 无事件区间内 ADJ/EXEC 比值为常数（极差 < 1e-12）
  A4 主板 ADJ 单日 |收益| > 10.5% 的天数 = 0；创业板/科创板按 20% 判
  A5 隐含股息 >= 0（ADJ 年化 >= EXEC 年化）
  A6 回归 β（ADJ 日收益 vs EXEC 日收益）∈ [0.999, 1.001]
  A7 事件残差：每个除权日「实际跌幅 − 理论除权跌幅」不应系统性偏大（单位错的照妖镜）
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DIV_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DIV_REPORT = "RPT_SHAREBONUS_DET"
HEADER = ["date", "open", "close", "high", "low", "volume"]
PRICE_COLS = ["open", "close", "high", "low"]

# 板块涨跌停判定（A4 用）：
#   科创板 688：自 2019-07-22 开板起就是 ±20%
#   创业板 300/301：2020-08-24 起改为 ±20%，之前 ±10%
#   其余主板：±10%
# 另加 1.5pp 容差——涨停日实际就是 20.00%（或 20.01% 舍入），严格 >20% 会误判。
CHINEXT_20_START = dt.date(2020, 8, 24)
LIMIT_TOLERANCE = 0.015


def limit_for(code: str, date_str: str) -> float:
    num = to_secucode(code)
    if num.startswith("688"):
        return 0.20 + LIMIT_TOLERANCE
    if num.startswith("300") or num.startswith("301"):
        d = dt.date.fromisoformat(date_str)
        return (0.20 if d >= CHINEXT_20_START else 0.10) + LIMIT_TOLERANCE
    return 0.10 + LIMIT_TOLERANCE


def to_secucode(code: str) -> str:
    """000001.SZ -> 000001 ; 只取 6 位数字。"""
    return code.split(".")[0]


def is_wide_limit(code: str) -> bool:
    num = to_secucode(code)
    return num.startswith("688") or num.startswith("300") or num.startswith("301")


def _get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))


def fetch_dividends(code: str, cache_dir: str, offline: bool = False) -> list[dict]:
    """取分红送转事件。缓存到 cache_dir，--offline 时不联网。"""
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"{code}.div.json")
    if offline:
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        raise FileNotFoundError(f"offline 模式缺少缓存: {cache}")
    params = {
        "sortColumns": "EX_DIVIDEND_DATE",
        "sortTypes": "-1",
        "pageSize": "200",
        "pageNumber": "1",
        "reportName": DIV_REPORT,
        "columns": "SECURITY_CODE,EX_DIVIDEND_DATE,PRETAX_BONUS_RMB,BONUS_IT_RATIO,BONUS_RATIO,"
        "EQUITY_RECORD_DATE,REPORT_DATE,ASSIGN_PROGRESS,IMPL_PLAN_PROFILE",
        "filter": f'(SECURITY_CODE="{to_secucode(code)}")',
    }
    url = DIV_URL + "?" + urllib.parse.urlencode(params)
    payload = _get_json(url)
    rows = ((payload.get("result") or {}).get("data")) or []
    with open(cache, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    return rows


def parse_plan(plan: str) -> dict | None:
    """解析方案文本，例如：
        '10送3.00派3.20元(含税,扣税后2.58元)'  -> 送3 派3.20
        '10转2.00派0.70元(...)'                 -> 转2 派0.70
        '10送3.00转2.00派5.00元(含税)'          -> 送3 转2 派5
        '10送2股转1股派2.00元(...)'             -> 送2 转1 派2
        '10转7.00' / '10送5.00'                 -> 无派息
    单位一律是「元/10股」「股/10股」。返回 {'div10','bonus10','transfer10'}；无法解析返回 None。
    """
    if not plan or "10" not in plan:
        return None
    t = str(plan).replace(" ", "")
    out = {"div10": 0.0, "bonus10": 0.0, "transfer10": 0.0}
    m = re.search(r"送\s*([\d.]+)\s*股?", t)
    if m:
        out["bonus10"] = float(m.group(1))
    m = re.search(r"转(?:增)?\s*([\d.]+)\s*股?", t)
    if m:
        out["transfer10"] = float(m.group(1))
    m = re.search(r"派\s*([\d.]+)\s*元", t)
    if m:
        out["div10"] = float(m.group(1))
    if "配" in t:
        return {"unsupported": "rights_issue", "plan": plan}
    if out["div10"] == 0.0 and out["bonus10"] == 0.0 and out["transfer10"] == 0.0:
        return None
    return out


def normalize_events(raw_rows: list[dict], start: str, end: str) -> tuple[list[dict], list[dict]]:
    """把东财行规整成事件列表；返回 (可用事件, 被丢弃的行及原因)。

    单位约定（本轮实测定标，见诊断文档）：
      - 数值字段 `PRETAX_BONUS_RMB` 是「元/10股」（茅台 10派308.76 验证）
      - 但 `BONUS_IT_RATIO` 与 `BONUS_RATIO` 在【送股】时是同一个值（600570 均为 3），
        相加会翻倍 -> 一律以方案文本 `IMPL_PLAN_PROFILE` 为准，数值字段仅作兜底。
    """
    events, dropped = [], []
    for r in raw_rows:
        ex = str(r.get("EX_DIVIDEND_DATE") or "")[:10]
        if not ex or len(ex) != 10:
            dropped.append({"row": _slim(r), "reason": "no_ex_date"})
            continue
        prog = str(r.get("ASSIGN_PROGRESS") or "")
        if "实施" not in prog:
            dropped.append({"row": _slim(r), "reason": f"not_implemented:{prog}"})
            continue
        if ex < start or ex > end:
            dropped.append({"row": _slim(r), "reason": "out_of_range"})
            continue

        plan = r.get("IMPL_PLAN_PROFILE")
        parsed = parse_plan(plan)
        if parsed and parsed.get("unsupported"):
            dropped.append({"row": _slim(r), "reason": parsed["unsupported"]})
            continue
        if parsed:
            div10 = parsed["div10"]
            share10 = parsed["bonus10"] + parsed["transfer10"]
            src = "plan"
        else:
            # 兜底：送股时两个字段重复，取 max 而不是相加
            div10 = float(r.get("PRETAX_BONUS_RMB") or 0)
            share10 = max(float(r.get("BONUS_IT_RATIO") or 0), float(r.get("BONUS_RATIO") or 0))
            src = "fields"

        d = div10 / 10.0
        b = share10 / 10.0
        if d < 0 or b < 0:
            dropped.append({"row": _slim(r), "reason": "negative_component"})
            continue
        if d == 0 and b == 0:
            dropped.append({"row": _slim(r), "reason": "zero_event"})
            continue
        events.append(
            {
                "ex_date": ex,
                "div_per_share": round(d, 6),
                "share_ratio": round(b, 6),
                "plan": plan,
                "source": src,
                "report_date": str(r.get("REPORT_DATE") or "")[:10],
            }
        )
    events.sort(key=lambda e: e["ex_date"])
    return events, dropped


def _slim(r: dict) -> dict:
    return {k: r.get(k) for k in ("EX_DIVIDEND_DATE", "PRETAX_BONUS_RMB", "BONUS_IT_RATIO", "BONUS_RATIO", "IMPL_PLAN_PROFILE")}


def read_exec(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return []
    hdr = [h.strip().lower() for h in rows[0]]
    idx = {h: hdr.index(h) for h in HEADER if h in hdr}
    out = []
    for r in rows[1:]:
        if not r:
            continue
        rec = {"date": r[idx["date"]]}
        for c in PRICE_COLS:
            rec[c] = float(r[idx[c]])
        rec["volume"] = r[idx["volume"]] if "volume" in idx else ""
        out.append(rec)
    out.sort(key=lambda x: x["date"])
    return out


def build_adj(exec_rows: list[dict], events: list[dict], code: str, strict: bool = True) -> tuple[list[dict], list[dict]]:
    """按事件在时间轴上倒序累乘因子，得到 ADJ。返回 (adj_rows, event_diagnostics)。"""
    dates = [r["date"] for r in exec_rows]
    date_pos = {d: i for i, d in enumerate(dates)}

    # 事件对齐到交易日：取第一个 >= ex_date 的交易日（当日即除权日；若停牌则顺延）
    aligned = []
    diags = []
    for ev in events:
        pos = None
        for i, d in enumerate(dates):
            if d >= ev["ex_date"]:
                pos = i
                break
        if pos is None or pos == 0:
            diags.append({**ev, "skipped": "no_prev_bar"})
            continue
        p_prev = exec_rows[pos - 1]["close"]
        d_ps = ev["div_per_share"]
        b = ev["share_ratio"]
        theory = (p_prev - d_ps) / (1.0 + b)
        f = theory / p_prev
        actual_ret = exec_rows[pos]["close"] / p_prev - 1.0
        theory_ret = theory / p_prev - 1.0
        diag = {
            **ev,
            "trading_date": dates[pos],
            "prev_close": round(p_prev, 4),
            "theory_ex_price": round(theory, 4),
            "actual_close": exec_rows[pos]["close"],
            "actual_ret": round(actual_ret, 6),
            "theory_ret": round(theory_ret, 6),
            "residual_ret": round(actual_ret - theory_ret, 6),
            "factor": round(f, 10),
        }
        if not (f > 0):
            diag["skipped"] = "nonpositive_factor"
            diags.append(diag)
            if strict:
                raise ValueError(f"{code}: 除权因子非正 @{ev['ex_date']} prev={p_prev} D={d_ps} b={b}")
            continue
        aligned.append((pos, f))
        diags.append(diag)

    # 倒序累乘：从最新日往前，遇到事件日就乘上该事件的因子
    factors = [1.0] * len(exec_rows)
    cur = 1.0
    ev_at = {pos: f for pos, f in aligned}
    for i in range(len(exec_rows) - 1, -1, -1):
        factors[i] = cur
        if i in ev_at:
            cur *= ev_at[i]

    adj_rows = []
    for i, r in enumerate(exec_rows):
        k = factors[i]
        rec = {"date": r["date"], "volume": r["volume"]}
        for c in PRICE_COLS:
            rec[c] = r[c] * k
        adj_rows.append(rec)
    return adj_rows, diags


def write_qfq(path: str, rows: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r["date"]] + [f"{r[c]:.6f}" for c in PRICE_COLS] + [r["volume"]])
    os.replace(tmp, path)


def validate(code: str, exec_rows: list[dict], adj_rows: list[dict], events: list[dict], diags: list[dict]) -> dict:
    """A1~A7 判据。exec_rows 与 adj_rows 必须同日期同序。"""
    res: dict = {"code": code, "fails": [], "n": len(exec_rows), "n_events": len(events)}
    dates = [r["date"] for r in exec_rows]

    # A1 价格为正
    bad = sum(1 for r in adj_rows if min(r[c] for c in PRICE_COLS) <= 0)
    res["nonpositive"] = bad
    if bad:
        res["fails"].append("A1_nonpositive")

    ev_dates = {d["trading_date"] for d in diags if d.get("trading_date") and not d.get("skipped")}

    # A2 / A3：逐日比较（A2 只在**非除权日**上判——除权日两序列本就应当不同）
    max_a2 = 0.0
    max_a3 = 0.0
    ratio_runs: dict[str, list[float]] = {}
    run_id = None
    xs_ne, ys_ne = [], []   # 非除权日样本（用于 A6）
    xs_all, ys_all = [], []  # 全样本（仅作参考，除权日会压 β）
    for i in range(1, len(dates)):
        a, b_ = exec_rows[i - 1], exec_rows[i]
        if a["close"] <= 0 or b_["close"] <= 0:
            continue
        re_ = b_["close"] / a["close"] - 1.0
        ra = adj_rows[i]["close"] / adj_rows[i - 1]["close"] - 1.0
        xs_all.append(re_)
        ys_all.append(ra)
        if dates[i] in ev_dates:
            run_id = dates[i]  # 事件日之后开新区间
            continue
        xs_ne.append(re_)
        ys_ne.append(ra)
        if run_id is None:
            run_id = "_head"
        ratio_runs.setdefault(run_id, []).append(adj_rows[i]["close"] / b_["close"])
        max_a2 = max(max_a2, abs(ra - re_))
    res["max_abs_ret_diff"] = max_a2
    if max_a2 > 1e-9:
        res["fails"].append(f"A2_ret_diff:{max_a2:.2e}")
    for k, vals in ratio_runs.items():
        if len(vals) > 1:
            spread = max(vals) - min(vals)
            max_a3 = max(max_a3, spread)
    res["max_ratio_spread"] = max_a3
    if max_a3 > 1e-12:
        res["fails"].append(f"A3_ratio_drift:{max_a3:.2e}")

    # A6 回归 β —— **只取非除权日**。除权日 EXEC 有大跳、ADJ 无跳，混进去必然把 β 压低，
    #    那是判据自身的偏差，不是数据缺陷（本轮先踩了这个坑，见诊断文档 §6.4 修订）。
    def _beta(xsz, ysz):
        n = len(xsz)
        if n < 10:
            return float("nan")
        mx = sum(xsz) / n
        my = sum(ysz) / n
        sxx = sum((x - mx) ** 2 for x in xsz)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xsz, ysz))
        return sxy / sxx if sxx else float("nan")

    res["beta"] = round(_beta(xs_ne, ys_ne), 9)
    res["beta_all"] = round(_beta(xs_all, ys_all), 6)  # 参考值，不作判据
    if not (0.999 <= res["beta"] <= 1.001):
        res["fails"].append(f"A6_beta:{res['beta']:.6f}")

    # A4 涨跌停（按板块与日期取限幅，含 1.5pp 容差）
    #     两类例外（都不计失败，但完整记录）：
    #     ① 【停牌复牌缺口】前一根 bar 与本根间隔 > 4 个自然日：复牌首日可能超限。
    #     ② 【新股上市初期】序列前 5 个交易日：科创板/创业板 IPO 前 5 日无涨跌幅限制，
    #        主板首日亦无限制。这类标的（事件数常仅 1~2 个）会被误判。
    #     ③ 【零分红事件】ADJ ≡ EXEC，根本没有做调整——此时 A4 检验的对象不存在，不适用。
    #        （否则会把 ST/退市整理期的真实价格行为当成复权缺陷，实测 101 例失败中 100 例属此）
    IPO_FREE_BARS = 5
    over = 0
    over_days = []
    resume_gaps = []
    ipo_window = []
    if not diags:
        res["notes"] = (res.get("notes") or []) + ["A4_not_applicable:no_events"]
        res["limit_over_days"] = 0
        res["resume_gap_days"] = 0
        res["ipo_window_days"] = 0
    else:
        for i in range(1, len(dates)):
            a, b_ = adj_rows[i - 1]["close"], adj_rows[i]["close"]
            if a <= 0:
                continue
            ret = abs(b_ / a - 1.0)
            if ret <= limit_for(code, dates[i]):
                continue
            item = {"date": dates[i], "ret": round(ret, 6), "is_event": dates[i] in ev_dates}
            if i <= IPO_FREE_BARS:
                ipo_window.append(item)
                continue
            gap = (dt.date.fromisoformat(dates[i]) - dt.date.fromisoformat(dates[i - 1])).days
            item["gap_days"] = gap
            if gap > 4:
                resume_gaps.append(item)
            else:
                over += 1
                over_days.append(item)
        res["limit_over_days"] = over
        res["resume_gap_days"] = len(resume_gaps)
        res["ipo_window_days"] = len(ipo_window)
        if resume_gaps:
            res["resume_gap_detail"] = resume_gaps[:10]
        if ipo_window:
            res["ipo_window_detail"] = ipo_window[:10]
        if over:
            res["limit_over_detail"] = over_days[:10]
            res["fails"].append(f"A4_limit:{over}")

    # A5 隐含股息 >= 0
    if exec_rows[0]["close"] > 0:
        pa = exec_rows[-1]["close"] / exec_rows[0]["close"] - 1.0
        ta = adj_rows[-1]["close"] / adj_rows[0]["close"] - 1.0
        yrs = (dt.date.fromisoformat(exec_rows[-1]["date"]) - dt.date.fromisoformat(exec_rows[0]["date"])).days / 365.25
        res["price_ret"] = round(pa, 6)
        res["adj_ret"] = round(ta, 6)
        if yrs > 0:
            ann_p = (1 + pa) ** (1 / yrs) - 1 if pa > -1 else -1.0
            ann_t = (1 + ta) ** (1 / yrs) - 1 if ta > -1 else -1.0
            res["div_pp"] = round((ann_t - ann_p) * 100, 3)
            if res["div_pp"] < 0:
                res["fails"].append(f"A5_negative_div:{res['div_pp']}")

    # A7 事件残差（单位错的照妖镜）
    #     目的只有一个：抓【单位错】（把"元/10股"当"元/股"，偏差量级 10~100%）。
    #     因此按**量级**判，不按统计显著性判：
    #       - 除权日当天的"填权/贴权"是真实市场规律（平安银行 14 个除息日一致偏高 1.3%，
    #         t=3.4 显著，但这是市场行为，不是数据错）；
    #       - 单位错造成的偏差是 (9×D)/P 的量级——3% 股息率的股票也会有 ~18%。
    #     故阈值取 |符号均值| > 5%，t 值只作信息记录。
    BIAS_MIN_EVENTS = 8
    BIAS_MAGNITUDE = 0.05
    signed = [d["residual_ret"] for d in diags if d.get("residual_ret") is not None]
    if signed:
        absr = sorted(abs(x) for x in signed)
        mean_s = sum(signed) / len(signed)
        res["event_resid_mean_signed"] = round(mean_s, 6)
        res["event_resid_median_abs"] = round(absr[len(absr) // 2], 6)
        res["event_resid_max_abs"] = round(absr[-1], 6)
        res["event_resid_n"] = len(signed)
        if len(signed) >= 2:
            mu = mean_s
            sd = (sum((x - mu) ** 2 for x in signed) / (len(signed) - 1)) ** 0.5
            if sd > 0:
                res["event_resid_t"] = round(mu / (sd / len(signed) ** 0.5), 4)  # 仅信息
        if len(signed) < BIAS_MIN_EVENTS:
            res["notes"] = (res.get("notes") or []) + [
                f"bias_test_skipped:n={len(signed)}<{BIAS_MIN_EVENTS}"
            ]
        elif abs(mean_s) > BIAS_MAGNITUDE:
            res["fails"].append(f"A7_resid_bias:mean={mean_s:.4f}")

    res["ok"] = not res["fails"]
    return res


def process_one(code: str, args) -> dict:
    rec: dict = {"code": code, "ok": False, "fails": []}
    exec_path = os.path.join(args.exec_dir, f"{code}.exec.csv")
    if not os.path.exists(exec_path):
        rec["error"] = "no_exec_csv"
        return rec
    exec_rows = read_exec(exec_path)
    if len(exec_rows) < 30:
        rec["error"] = f"too_few_rows:{len(exec_rows)}"
        return rec
    try:
        raw = fetch_dividends(code, args.div_cache, offline=args.offline)
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"div_fetch:{type(exc).__name__}: {exc}"
        return rec
    events, dropped = normalize_events(raw, exec_rows[0]["date"], exec_rows[-1]["date"])
    try:
        adj_rows, diags = build_adj(exec_rows, events, code)
    except ValueError as exc:
        rec["error"] = f"build:{exc}"
        return rec
    write_qfq(os.path.join(args.out, f"{code}.qfq.csv"), adj_rows)
    v = validate(code, exec_rows, adj_rows, events, diags)
    v["dropped_events"] = len(dropped)
    v["events"] = diags
    return v


def load_codes(args) -> list[str]:
    if args.codes:
        return [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    with open(args.pool_manifest, encoding="utf-8") as f:
        m = json.load(f)
    return sorted(s["code"] for s in m.get("symbols", []) if s.get("code"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用真不复权价 + 分红事件自算前复权（P1-4）")
    ap.add_argument("--codes", default=None)
    ap.add_argument("--pool-manifest", default="data/pool_manifest.json")
    ap.add_argument("--exec-dir", default="data/exec")
    ap.add_argument("--div-cache", default="data/exec/_div_cache")
    ap.add_argument("--out", default="data/qfq_rebuilt")
    ap.add_argument("--offline", action="store_true", help="只用分红缓存，不联网")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    codes = load_codes(args)
    print(f"[rebuild] {len(codes)} 只 -> {args.out}")

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(process_one, c, args): c for c in codes}
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            if rec.get("error"):
                print(f"  [ERR ] {rec['code']:<12} {rec['error']}")
            else:
                mark = "OK  " if rec["ok"] else "FAIL"
                extra = (" " + ",".join(rec["fails"])) if rec["fails"] else ""
                print(
                    f"  [{mark}] {rec['code']:<12} ev={rec['n_events']:<3} "
                    f"beta={rec.get('beta','-'):<9} div={rec.get('div_pp','-'):>7}pp "
                    f"resid_bias={rec.get('event_resid_mean_signed','-'):<9}{extra}"
                )
            time.sleep(0.05)

    results.sort(key=lambda r: r["code"])
    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "out_dir": args.out,
        "n": len(results),
        "n_ok": len(ok),
        "n_fail": len(bad),
        "fail_codes": [{"code": r["code"], "fails": r.get("fails"), "error": r.get("error")} for r in bad],
        "elapsed_sec": round(time.time() - t0, 1),
        "results": results,
    }
    rp = args.report or os.path.join(args.out, "_rebuild_report.json")
    with open(rp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[rebuild] ok={len(ok)} fail={len(bad)}  elapsed={report['elapsed_sec']}s")
    print(f"[rebuild] report -> {rp}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
