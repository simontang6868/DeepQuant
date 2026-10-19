"""把退市组的「异常单日收益」分成真实现象与脏数据。

为什么必须分
------------
上一轮审计发现：研究区间内，退市组 **73/249 只**出现单日 |收益率| > 21%，
在市组只有 2/294。若这些异常是脏数据，则 B 组中位 −2.99% 里混着噪声，
阶段 0 的 Δmedian = −1.67pp 就不能直接归因于幸存者偏差。

但退市股确实会暴涨暴跌：
- **退市整理期**首日不设涨跌幅限制，后续 10%；
- 摘牌前常有 80%~90% 的单日跌幅，这是真实成交；
- 长期停牌后复牌当天也不受限。

所以「超限」本身不是脏数据的证据。本脚本用三个可判别的特征来分：

1. **位置**：是否发生在该标的最后 N 个交易日（退市整理期窗口）内；
2. **可逆性**：真实的暴跌不会第二天原样弹回；脏数据常表现为「一日尖峰后立刻还原」，
   即 r_t ≈ −r_{t+1}（价格回到原位）；
3. **价位**：是否发生在价格极低（< 0.5 元）区间——低价股的最小变动单位 0.01 元
   带来的相对跳变本来就能超过 10%，属刻度效应而非错误。

输出分档：
- ``tail_window``：落在最后 15 个交易日内 → 大概率真实的退市整理期行情；
- ``spike_revert``：一日内跳变且次日基本还原 → 脏数据指纹；
- ``low_price_tick``：价格 < 0.5 元 → 刻度效应，不可当作异常；
- ``other``：其余，需人工看。

只读，不修改任何行情文件。

用法
----
    python scripts/audit_delist_price_spikes.py
    python scripts/audit_delist_price_spikes.py --tail 15
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

STUDY_START = pd.Timestamp("2017-01-01")
STUDY_END = pd.Timestamp("2024-12-31")
LIMIT = 0.21
TAIL_N = 15
LOW_PRICE = 0.5


def load_close(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    s = df[["date", "close"]].copy()
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s["close"] = pd.to_numeric(s["close"], errors="coerce")
    s = s.dropna(subset=["date"]).sort_values("date")
    return s.set_index("date")["close"]


def classify(code: str, s: pd.Series, tail: int) -> dict:
    s = s[(s.index >= STUDY_START) & (s.index <= STUDY_END)]
    if len(s) < 3:
        return {"code": code, "class": "too_short", "n": int(len(s))}

    r = s.pct_change().replace([np.inf, -np.inf], np.nan)
    hits = r[r.abs() > LIMIT].dropna()
    if len(hits) == 0:
        return {"code": code, "class": "clean", "n": int(len(s))}

    last_ts = s.index[-1]
    tail_start = s.index[max(0, len(s) - tail)]
    buckets = Counter()
    detail = []
    for ts, v in hits.items():
        pos = s.index.get_loc(ts)
        nxt = r.iloc[pos + 1] if pos + 1 < len(r) else np.nan
        px_prev = float(s.iloc[pos - 1]) if pos > 0 else float("nan")
        revert = (not np.isnan(nxt)) and abs(v + nxt) < 0.05 and abs(v) > 0.3
        if ts >= tail_start:
            buckets["tail_window"] += 1
            cls = "tail_window"
        elif revert:
            buckets["spike_revert"] += 1
            cls = "spike_revert"
        elif px_prev < LOW_PRICE:
            buckets["low_price_tick"] += 1
            cls = "low_price_tick"
        else:
            buckets["other"] += 1
            cls = "other"
        detail.append({"date": str(ts.date()), "ret": round(float(v), 6),
                       "prev_close": round(px_prev, 4), "cls": cls,
                       "days_to_end": int((last_ts - ts).days)})

    # 主导类别 = 命中最多的一档
    dom = buckets.most_common(1)[0][0]
    return {
        "code": code,
        "class": dom,
        "n": int(len(s)),
        "n_over": int(len(hits)),
        "buckets": dict(buckets),
        "max_abs_ret": round(float(hits.abs().max()), 6),
        "detail": sorted(detail, key=lambda x: -abs(x["ret"]))[:5],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="退市组异常收益分档")
    ap.add_argument("--layout", default="data/qfq_mult/delist")
    ap.add_argument("--tail", type=int, default=TAIL_N)
    ap.add_argument("--out", default="docs/DELIST_PRICE_SPIKES_2026-10-19.json")
    args = ap.parse_args()

    root = ROOT / args.layout
    files = sorted(root.glob("*.qfq.csv"))

    rows = []
    for p in files:
        code = p.name.split(".")[0] + "." + p.name.split(".")[1]
        try:
            rows.append(classify(code, load_close(p), args.tail))
        except Exception as exc:  # noqa: BLE001
            rows.append({"code": code, "class": "read_failed", "reason": str(exc)[:120]})

    cnt = Counter(r["class"] for r in rows)
    flagged = [r for r in rows if r["class"] in ("spike_revert", "other")]

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": "区分退市组极端单日收益是真实退市整理期行情，还是 hfq 拼接脏数据",
        "layout": args.layout,
        "criteria": {"price_limit": LIMIT, "tail_window": args.tail,
                     "low_price": LOW_PRICE},
        "counts": {"n_symbols": len(rows), **dict(cnt)},
        "flagged_for_review": sorted(flagged, key=lambda r: -r.get("max_abs_ret", 0)),
        "symbols": rows,
    }

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    print(f"[SCAN] {len(rows)} 只退市股，研究区间 2017-2024")
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"  {k:16s} {v:4d} 只")
    print(f"\n[需人工复核] spike_revert / other 共 {len(flagged)} 只")
    for r in payload["flagged_for_review"][:20]:
        print(f"  {r['code']:12s} {r['class']:14s} 超限 {r.get('n_over')} 天  "
              f"最大 {r.get('max_abs_ret'):+.2%}  {r.get('buckets')}")
    print(f"\n[OUT] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
