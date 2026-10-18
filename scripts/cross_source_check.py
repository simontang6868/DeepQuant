"""第二数据源核验：用乐咕（legulegu）指数估值序列交叉验证西股（westock）PE 口径。

**目的**：现有研究全部依赖单一 PE 数据源。若结论是「某个数据源口径的产物」，
研究就没有外部效度。本脚本用一份**独立来源**的沪深300 PE-TTM 重做同一套计算，
检查两件事：

1. 两份 PE 的一致性（相关性、水平差、极端期偏离）。
2. **事件集合是否随数据源变化** —— 这是更关键的检验：
   如果换源后信号事件完全不同，说明阈值附近的结果不稳健。

同时用乐咕附带的指数收盘价对价格序列做独立校验。

本脚本刻意**不复用** ``equity_bond_spread.indicators`` 的百分位实现，
用一份独立写法重算，避免「同一 bug 复制两遍」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/equity_bond_spread")
OUT = DATA / "cross_source"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# 独立实现（不复用研究模块）
# --------------------------------------------------------------------------- #
def expanding_percentile(values: pd.Series, min_obs: int = 36) -> pd.Series:
    """扩展窗口百分位：仅使用「截至当前时点」已发生的数据。

    这是独立实现：逐个时点把历史值累积进列表，再算 ``(hist <= x).mean()``。
    与向量化的研究模块实现无关，因而可用于交叉验证。
    """
    hist: list[float] = []
    out: list[float] = []
    for x in values.to_numpy(dtype="float64"):
        if np.isnan(x):
            out.append(np.nan)
            continue
        hist.append(float(x))
        arr = np.asarray(hist)
        out.append(np.nan if arr.size < min_obs else float((arr <= x).mean() * 100.0))
    return pd.Series(out, index=values.index, dtype="float64")


def merge_runs(flag: pd.Series, min_gap_obs: int = 4) -> list[tuple[int, int]]:
    """把连续为真的观测合并为事件区间（间隔 ≤ min_gap_obs 视为同一事件）。"""
    arr = flag.fillna(0).astype(int).to_numpy()
    pos = np.flatnonzero(arr == 1)
    if pos.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = prev = int(pos[0])
    for q in pos[1:]:
        q = int(q)
        if q - prev <= min_gap_obs:
            prev = q
        else:
            runs.append((start, prev))
            start = prev = q
    runs.append((start, prev))
    return runs


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # 用数据层做国债匹配（保持与研究一致的匹配规则），指标计算仍为独立实现
    from deepquant.research.equity_bond_spread.data import build_panel

    panel = build_panel(DATA / "csi300_monthly.csv", DATA / "cn10y.csv").frame.copy()
    panel = panel.sort_index()
    legu_raw = pd.read_csv(DATA / "raw" / "legu_index_pe_monthly.csv")
    legu_raw.columns = [str(c).strip().lstrip("\ufeff") for c in legu_raw.columns]
    legu_raw["日期"] = pd.to_datetime(legu_raw["日期"], errors="coerce")
    legu_raw = legu_raw.dropna(subset=["日期"]).set_index("日期").sort_index()

    legu = pd.DataFrame(
        {
            "price": pd.to_numeric(legu_raw["指数"], errors="coerce"),
            "pe_ttm": pd.to_numeric(legu_raw["滚动市盈率"], errors="coerce"),
        }
    )
    result: dict = {
        "sources": {
            "westock": {
                "file": "csi300_monthly.csv",
                "sha256": sha256(DATA / "csi300_monthly.csv"),
                "pe_field": "pe（口径未在源文件中标注，视为 PE-TTM 的近似）",
            },
            "legulegu": {
                "file": "raw/legu_index_pe_monthly.csv",
                "sha256": sha256(DATA / "raw" / "legu_index_pe_monthly.csv"),
                "pe_field": "滚动市盈率（明确为 TTM）",
                "coverage": [str(legu.index.min().date()), str(legu.index.max().date())],
                "rows": int(len(legu)),
            },
        }
    }

    # --- 1. 价格交叉验证 ---
    px = pd.DataFrame({"westock": panel["price"], "legulegu": legu["price"]}).dropna()
    if len(px):
        rel = (px["legulegu"] / px["westock"] - 1.0).abs()
        result["price_cross_check"] = {
            "n_overlap": int(len(px)),
            "corr": float(px["westock"].corr(px["legulegu"])),
            "mean_abs_rel_diff": float(rel.mean()),
            "max_abs_rel_diff": float(rel.max()),
            "max_diff_date": str(rel.idxmax().date()) if len(rel) else None,
            "verdict": "两份价格序列一致" if rel.mean() < 0.005 else "两份价格序列存在系统性差异",
        }

    # --- 2. PE 交叉验证 ---
    pe = pd.DataFrame({"westock": panel["pe"], "legulegu_ttm": legu["pe_ttm"]}).dropna()
    result["pe_cross_check"] = {}
    if len(pe):
        diff = pe["westock"] - pe["legulegu_ttm"]
        ratio = pe["westock"] / pe["legulegu_ttm"]
        result["pe_cross_check"] = {
            "n_overlap": int(len(pe)),
            "corr": float(pe["westock"].corr(pe["legulegu_ttm"])),
            "mean_diff": float(diff.mean()),
            "mean_abs_diff": float(diff.abs().mean()),
            "max_abs_diff": float(diff.abs().max()),
            "max_abs_diff_date": str(diff.abs().idxmax().date()),
            "ratio_median": float(ratio.median()),
            "ratio_min": float(ratio.min()),
            "ratio_max": float(ratio.max()),
            "pe_extremes": {
                "westock_min": [float(pe["westock"].min()), str(pe["westock"].idxmin().date())],
                "westock_max": [float(pe["westock"].max()), str(pe["westock"].idxmax().date())],
                "legulegu_min": [
                    float(pe["legulegu_ttm"].min()),
                    str(pe["legulegu_ttm"].idxmin().date()),
                ],
                "legulegu_max": [
                    float(pe["legulegu_ttm"].max()),
                    str(pe["legulegu_ttm"].idxmax().date()),
                ],
            },
        }

    # --- 3. 事件集合稳健性（核心检验） ---
    frames = {
        "westock": panel.assign(pe_use=panel["pe"]),
        "legulegu": panel.assign(pe_use=legu["pe_ttm"].reindex(panel.index)),
    }
    robustness: dict = {}
    for src, df in frames.items():
        ey = 100.0 / df["pe_use"].where(df["pe_use"] > 0)
        spread = ey - df["bond_10y"]
        pct = expanding_percentile(spread, min_obs=36)
        valid = pct.dropna()
        entry = {
            "first_pct_date": str(valid.index.min().date()) if len(valid) else None,
            "n_valid_pct": int(len(valid)),
            "pct_max": float(valid.max()) if len(valid) else None,
            "pct_max_date": str(valid.idxmax().date()) if len(valid) else None,
            "spread_min": float(spread.min()),
            "spread_min_date": str(spread.idxmin().date()),
            "spread_max": float(spread.max()),
            "spread_max_date": str(spread.idxmax().date()),
            "events": {},
        }
        for th in (80, 85, 90, 95):
            runs = merge_runs(pct >= th, min_gap_obs=4)
            entry["events"][str(th)] = {
                "n": len(runs),
                "starts": [str(pct.index[s].date()) for s, _ in runs],
                "ends": [str(pct.index[e].date()) for _, e in runs],
            }
        robustness[src] = entry
    result["event_robustness"] = robustness

    # --- 4. 事件集合差异汇总 ---
    diffs = {}
    for th in ("80", "85", "90", "95"):
        a = set(robustness["westock"]["events"][th]["starts"])
        b = set(robustness["legulegu"]["events"][th]["starts"])
        diffs[th] = {
            "identical": a == b,
            "only_westock": sorted(a - b),
            "only_legulegu": sorted(b - a),
            "n_westock": len(a),
            "n_legulegu": len(b),
        }
    result["event_set_difference"] = diffs

    # --- 5. 触发余量（阈值附近有多脆弱） ---
    margin: list[dict] = []
    for src, df in frames.items():
        ey = 100.0 / df["pe_use"].where(df["pe_use"] > 0)
        pct = expanding_percentile(ey - df["bond_10y"], min_obs=36)
        for s, e in merge_runs(pct >= 90.0, min_gap_obs=4):
            margin.append(
                {
                    "source": src,
                    "start": str(pct.index[s].date()),
                    "end": str(pct.index[e].date()),
                    "months": int(e - s + 1),
                    "pct_at_trigger": float(pct.iloc[s]),
                    "margin_pp": float(pct.iloc[s] - 90.0),
                }
            )
    result["trigger_margin"] = margin

    path = OUT / "cross_source_check.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 价格交叉验证 ===")
    print(json.dumps(result.get("price_cross_check", {}), ensure_ascii=False, indent=2))
    print("\n=== PE 交叉验证 ===")
    print(json.dumps(result.get("pe_cross_check", {}), ensure_ascii=False, indent=2))
    print("\n=== 事件集合稳健性（换数据源）===")
    for th, d in diffs.items():
        tag = "一致" if d["identical"] else "不一致"
        print(f"  阈值 {th}%: {tag}（westock {d['n_westock']} 个 / legulegu {d['n_legulegu']} 个）")
        if not d["identical"]:
            print(f"    仅 westock: {d['only_westock']}")
            print(f"    仅 legulegu: {d['only_legulegu']}")
    print("\n=== 90% 阈值触发余量 ===")
    for m in margin:
        print(
            f"  [{m['source']:9s}] {m['start']} ~ {m['end']} 持续 {m['months']:2d} 月  "
            f"百分位 {m['pct_at_trigger']:.2f}%  余量 {m['margin_pp']:+.2f}pp"
        )
    print(f"\n已写出：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
