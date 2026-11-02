"""SP-5 / P0-2 步骤 2：低流动性档内 ST 暴露度（描述性，不做收益）

回答一个问题：**SP-4 的候选池里到底混进了多少 ST？**
若占比很低，则「排除 ST」这一敏感性对结论影响有限（可降级）；
若占比高，则 P0 必须重跑含 ST 过滤的完整回测。

口径（与 SP-4 冻结配置一致，不新增参数）
--------------------------------
* 价格 1~50 元；`amt20 > 500 万`；`amt20` 全市场分位 < 20%（低流动性档）；
* `f3pct <= 0.70`（入池条件）；
* ST 标记来自 `data/st_sz_monthly.parquet`（**仅深市**）；
* 因此本脚本只能报「深市子样本」的 ST 占比，**沪市部分无法计算**。

输出：sp5_out/sp5_st_exposure.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "data"
OUT = BASE / "sp5_out"
SZ_PREFIX = ("000", "001", "002", "003", "300", "301", "302")


def main() -> int:
    p = pd.read_parquet(
        DATA / "panel_monthly.parquet",
        columns=["date", "code", "amt20", "close", "f3", "ok_buy"],
    )
    p["mkt"] = np.where(p["code"].str[:3].isin(SZ_PREFIX), "SZ", "SH")

    g = p.groupby("date", sort=True)
    p["liqpct"] = g["amt20"].rank(pct=True, method="average")
    p["f3pct"] = g["f3"].rank(pct=True, method="average")

    band = (
        p["close"].between(1.0, 50.0)
        & (p["amt20"] > 5e6)
        & (p["liqpct"] < 0.20)
    )
    pool = band & (p["f3pct"] <= 0.70)

    st = pd.read_parquet(DATA / "st_sz_monthly.parquet")
    # 面板 date 是 'YYYYMMDD' 字符串，统一成同一表示再合并（否则 str/datetime 无法对齐）
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    p["date"] = p["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    n_before = len(p)
    p = p.merge(st[["date", "code", "is_st"]], on=["date", "code"], how="left")
    assert len(p) == n_before, "merge 后行数变化 → 键不唯一"

    rows = []
    for dt, gg in p[pool].groupby("date", sort=True):
        sz = gg[gg["mkt"] == "SZ"]
        rows.append(
            {
                "date": str(pd.Timestamp(dt).date()),
                "n_pool": int(len(gg)),
                "n_pool_sz": int(len(sz)),
                "n_pool_sz_st": int(sz["is_st"].sum()),
                "st_share_of_sz": round(float(sz["is_st"].mean()), 4) if len(sz) else None,
                "n_band": int(band.loc[gg.index].sum()),
            }
        )
    df = pd.DataFrame(rows)
    df["yr"] = df["date"].str[:4]

    by_year = (
        df.groupby("yr")
        .agg(
            months=("date", "size"),
            pool_med=("n_pool", "median"),
            pool_sz_med=("n_pool_sz", "median"),
            st_med=("n_pool_sz_st", "median"),
            st_share_med=("st_share_of_sz", "median"),
        )
        .round(4)
        .reset_index()
    )

    valid = df[df["n_pool_sz"] >= 20]
    # 「整月无 ST」与「ST 占比为 0」必须分开报告（避免把无数据当无风险）
    res = {
        "months_total": int(len(df)),
        "months_with_sz_pool_ge20": int(len(valid)),
        "pool_size_median": float(df["n_pool"].median()),
        "pool_sz_median": float(df["n_pool_sz"].median()),
        "st_count_median_in_sz_pool": float(df["n_pool_sz_st"].median()),
        "st_share_of_sz_pool_median": round(float(valid["st_share_of_sz"].median()), 4),
        "st_share_of_sz_pool_p90": round(float(valid["st_share_of_sz"].quantile(0.90)), 4),
        "st_share_of_sz_pool_max": round(float(valid["st_share_of_sz"].max()), 4),
        "months_with_zero_st_in_sz_pool": int((valid["n_pool_sz_st"] == 0).sum()),
        "sh_side": "无法计算（无沪市历史 ST 表）",
        "by_year": by_year.to_dict(orient="records"),
        "caveat": [
            "仅深市，不能外推到沪市（沪市占面板 43.9% 的 code-month）",
            "ST 占比是『档内池子』口径，不等于『随机抽 5 只』的实际持有占比",
            "本脚本不做收益，只回答暴露度",
        ],
    }
    (OUT / "sp5_st_exposure.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for k in (
        "months_total",
        "months_with_sz_pool_ge20",
        "pool_size_median",
        "st_count_median_in_sz_pool",
        "st_share_of_sz_pool_median",
        "st_share_of_sz_pool_p90",
        "st_share_of_sz_pool_max",
        "months_with_zero_st_in_sz_pool",
    ):
        print(f"{k:38s} = {res[k]}")
    print(by_year.tail(12).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
