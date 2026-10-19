"""把原始抓取文件整理成研究用的清洗后数据，并生成可追溯清单。

输入（raw，只读不改）：
- ``csi300_monthly_raw.csv``：date, price, pe, pb, div_raw, quote_date
- ``cn10y_raw.csv``：date, yield_10y

输出：
- ``data/equity_bond_spread/csi300_monthly.csv``：date, price, pe, pb, div_yield
- ``data/equity_bond_spread/cn10y.csv``：date, yield_10y
- ``data/equity_bond_spread/manifest.json``：来源、抓取方式、字段定义、SHA-256

原则：
- 原始数据与清洗后数据分开保存，两者都算 SHA-256；
- 缺失值保留为空，**不做插值**，不用未来数据填补过去；
- 采样日若为非交易日，行情源会回填上一交易日的数据，此时
  ``quote_date != 采样日``，脚本以 ``quote_date`` 作为真实数据日期。
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from deepquant.research.equity_bond_spread.data import sha256_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "equity_bond_spread"
RAW_DIR = DATA_DIR / "raw"

PROVENANCE = {
    "equity": {
        "field": "沪深300 指数（000300.SH）月频快照",
        "source": "腾讯自选股行情接口（westock-data CLI: quote sh000300 --date YYYY-MM-DD）",
        "method": "对每个月最后一个自然日逐点请求；非交易日由行情源回填上一交易日数据",
        "fields": {
            "price": "收盘价（价格指数口径，不含分红再投资）",
            "pe": "行情源返回的市盈率字段，口径未经指数公司官方确认",
            "pb": "行情源返回的市净率字段",
            "div_yield": "行情源返回的股息率 TTM（百分数）",
        },
        "caveats": [
            "价格指数口径，不是全收益指数",
            "PE 口径未获官方确认，可能与 PE-TTM 存在差异",
            "行情源对部分早期日期不返回 PE/PB，已置为空",
        ],
    },
    "bond": {
        "field": "中国 10 年期国债收益率",
        "source": "中国债券信息网 - 中债国债收益率曲线（yield.chinabond.com.cn）",
        "method": "按自然年分段请求（单次请求跨度需小于一年），取「中债国债收益率曲线」的 10 年期列",
        "fields": {"yield_10y": "10 年期国债到期收益率（百分数）"},
        "caveats": [
            "曲线数据起始于 2006-03-01，之前区间无数据",
            "收益率曲线为中央国债登记结算有限责任公司编制",
        ],
    },
}


def prepare(equity_raw: Path, bond_raw: Path) -> dict:
    """整理数据，返回 manifest 字典。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    eq_src = RAW_DIR / "csi300_monthly_raw.csv"
    bd_src = RAW_DIR / "cn10y_raw.csv"
    shutil.copy2(equity_raw, eq_src)
    shutil.copy2(bond_raw, bd_src)

    # --- 股票序列 ---
    raw_eq = pd.read_csv(eq_src)
    df = pd.DataFrame()
    # 以行情源返回的真实数据日期为准（非交易日会回填上一交易日）
    qd = pd.to_datetime(raw_eq.get("quote_date"), errors="coerce")
    sampled = pd.to_datetime(raw_eq["date"], errors="coerce")
    df["date"] = qd.fillna(sampled)
    for src, dst in (("price", "price"), ("pe", "pe"), ("pb", "pb"), ("div_raw", "div_yield")):
        v = pd.to_numeric(raw_eq.get(src), errors="coerce")
        # 行情源用 0 表示缺失
        v = v.mask(v == 0)
        df[dst] = v
    df = df.dropna(subset=["date"])
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    eq_out = DATA_DIR / "csi300_monthly.csv"
    df.to_csv(eq_out, index=False, date_format="%Y-%m-%d")

    # --- 国债序列 ---
    raw_bd = pd.read_csv(bd_src)
    bdf = pd.DataFrame()
    bdf["date"] = pd.to_datetime(raw_bd["date"], errors="coerce")
    bdf["yield_10y"] = pd.to_numeric(raw_bd["yield_10y"], errors="coerce")
    bdf = bdf.dropna(subset=["date"]).sort_values("date")
    bdf = bdf.drop_duplicates(subset=["date"], keep="last")
    bd_out = DATA_DIR / "cn10y.csv"
    bdf.to_csv(bd_out, index=False, date_format="%Y-%m-%d")

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provenance": PROVENANCE,
        "files": {
            "raw_equity": {
                "path": str(eq_src.relative_to(REPO_ROOT)),
                "sha256": sha256_file(eq_src),
                "rows": int(len(raw_eq)),
            },
            "raw_bond": {
                "path": str(bd_src.relative_to(REPO_ROOT)),
                "sha256": sha256_file(bd_src),
                "rows": int(len(raw_bd)),
            },
            "clean_equity": {
                "path": str(eq_out.relative_to(REPO_ROOT)),
                "sha256": sha256_file(eq_out),
                "rows": int(len(df)),
                "date_min": str(df["date"].min().date()),
                "date_max": str(df["date"].max().date()),
                "missing_rate": {
                    c: float(df[c].isna().mean()) for c in ("price", "pe", "pb", "div_yield")
                },
            },
            "clean_bond": {
                "path": str(bd_out.relative_to(REPO_ROOT)),
                "sha256": sha256_file(bd_out),
                "rows": int(len(bdf)),
                "date_min": str(bdf["date"].min().date()),
                "date_max": str(bdf["date"].max().date()),
                "missing_rate": {"yield_10y": float(bdf["yield_10y"].isna().mean())},
            },
        },
        "cleaning_rules": [
            "行情源用 0 表示的缺失值统一置为空",
            "非交易日采样以行情源返回的 quote_date 作为真实数据日期",
            "按日期升序去重，同一日期保留最后一条",
            "缺失值不做任何插值填充",
        ],
    }
    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="整理股债利差研究的原始数据")
    ap.add_argument("--equity-raw", required=True, type=Path)
    ap.add_argument("--bond-raw", required=True, type=Path)
    args = ap.parse_args()
    m = prepare(args.equity_raw, args.bond_raw)
    print(json.dumps(m, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
