"""SP-5 / P0-2 步骤 1：深市历史 ST 标记构建（数据可行性验证）

目的
----
SP-4 审计把「历史 ST 状态」列为未解决项（`SP4_SURVIVOR_BIAS_AUDIT.md` §5），
理由是「项目无 ST 日表」。本脚本核实**到底能不能拿到**，并把能拿到的部分落盘。

数据源
------
深交所官网「市场数据 → 股票数据 → 名称变更 → 简称变更」
    https://www.szse.cn/www/market/stock/changename/index.html
经 akshare `stock_info_sz_change_name(symbol="简称变更")` 读取。
返回列：变更日期 / 证券代码 / 证券简称 / 变更前简称 / 变更后简称。

覆盖范围（脚本会实测，不预设）
--------------------------
* **深市可覆盖**（000/001/002/003/300/301/302）；
* **沪市不可覆盖**（akshare 无对应接口，`stock_info_change_name` 只给名称列表不给日期）；
* 从未改名的股票不出现在表内 —— 对「是否 ST」无害（从未改名 ⇒ 从未 ST）。

输出
----
data/st_sz_monthly.parquet : date, code, name, is_st （仅深市，月末口径）
sp5_out/sp5_st_coverage.json : 覆盖率与口径说明

判定口径（写死，跑后不改）
----------------------
is_st(code, month_end) = 「变更日期 <= month_end 的最后一条记录的变更后简称」含 'ST'。
该月早于首条记录时，用首条记录的「变更前简称」。

⚠️ 这是**替代口径（proxy-free，真值来自交易所公告）**，不是代理指标；
但**仅深市**，沪市仍属「无法验证」，引用时必须写明市场范围。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[2]          # research/stock_pick_2026-10-25
DATA = BASE / "data"
OUT = BASE / "sp5_out"
OUT.mkdir(parents=True, exist_ok=True)

SZ_PREFIX = ("000", "001", "002", "003", "300", "301", "302")
SH_PREFIX = ("600", "601", "603", "605", "688", "689")


def fetch_sz() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_info_sz_change_name(symbol="简称变更")
    df = df.rename(
        columns={
            "变更日期": "date",
            "证券代码": "code",
            "证券简称": "name_now",
            "变更前简称": "name_before",
            "变更后简称": "name_after",
        }
    )
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"])
    for c in ("name_now", "name_before", "name_after"):
        df[c] = df[c].astype(str).str.strip()
    return df.sort_values(["code", "date"], kind="mergesort").reset_index(drop=True)


def build_monthly(chg: pd.DataFrame, month_ends: list[pd.Timestamp]) -> pd.DataFrame:
    """把「变更事件」展开成「月末状态」。纯区间赋值，无前视。"""
    rows = []
    for code, g in chg.groupby("code", sort=False):
        dates = g["date"].tolist()
        after = g["name_after"].tolist()
        first_before = g["name_before"].iloc[0]
        j = -1
        for me in month_ends:
            while j + 1 < len(dates) and dates[j + 1] <= me:
                j += 1
            name = after[j] if j >= 0 else first_before
            rows.append((me, code, name))
    out = pd.DataFrame(rows, columns=["date", "code", "name"])
    out["is_st"] = out["name"].str.contains("ST", case=True, regex=False)
    return out


def main() -> int:
    chg = fetch_sz()
    print(f"[fetch] 深市简称变更 {len(chg)} 行，"
          f"{chg['code'].nunique()} 只，{chg['date'].min().date()} ~ {chg['date'].max().date()}")

    panel = pd.read_parquet(DATA / "panel_monthly.parquet", columns=["date", "code"])
    month_ends = sorted(pd.to_datetime(panel["date"].unique()))
    panel_codes = set(panel["code"].unique())

    st = build_monthly(chg, month_ends)
    st = st[st["code"].isin(panel_codes)].copy()
    st.to_parquet(DATA / "st_sz_monthly.parquet", index=False)

    # 覆盖率：按市场拆分面板 code-month
    pc = panel.copy()
    pc["mkt"] = pc["code"].str[:3].map(
        lambda p: "SZ" if p in SZ_PREFIX else ("SH" if p in SH_PREFIX else "OTHER")
    )
    cov = {}
    for mkt, g in pc.groupby("mkt"):
        n_cm = len(g)
        n_code = g["code"].nunique()
        hit_code = len(set(g["code"]) & set(st["code"]))
        cov[mkt] = {
            "code_months": int(n_cm),
            "codes": int(n_code),
            "codes_with_name_history": int(hit_code),
            "code_share_with_history": round(hit_code / max(n_code, 1), 4),
        }

    sz = st[st["code"].str[:3].isin(SZ_PREFIX)]
    st_rate_by_year = (
        sz.assign(yr=sz["date"].dt.year).groupby("yr")["is_st"].mean().round(4).to_dict()
    )

    res = {
        "source": "szse.cn 名称变更(简称变更) via akshare stock_info_sz_change_name",
        "raw_rows": int(len(chg)),
        "raw_codes": int(chg["code"].nunique()),
        "date_range": [str(chg["date"].min().date()), str(chg["date"].max().date())],
        "rows_containing_ST": int(
            chg[["name_before", "name_after"]].apply(
                lambda s: s.str.contains("ST", regex=False)
            ).any(axis=1).sum()
        ),
        "month_end_count": len(month_ends),
        "month_end_range": [str(month_ends[0].date()), str(month_ends[-1].date())],
        "coverage_by_market": cov,
        "sz_st_share_by_year": {str(k): v for k, v in st_rate_by_year.items()},
        "limitation": [
            "仅深市（000/001/002/003/300/301/302）；沪市无带日期的历史简称变更接口",
            "从未改名的股票不在表内 —— 对 is_st 无害（从未改名 ⇒ 从未 ST）",
            "月末口径；月内进出 ST 不建模",
            "未区分 *ST / ST / SST，统一按含 'ST' 处理（与『排除 ST』的投资含义一致）",
        ],
        "verdict": "SZ_ONLY_AVAILABLE",
    }
    (OUT / "sp5_st_coverage.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
