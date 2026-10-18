"""抓取并整理美股长历史股债利差研究数据。

数据来源
--------
1. multpl.com 月度表（主力，当前更新至 2026-10）
   - ``shiller-pe``                → CAPE（周期调整市盈率）
   - ``s-p-500-historical-prices`` → 标普 500 月度均价
   - ``s-p-500-dividend-yield``    → 滚动 12 个月股息率
   - ``cpi``                       → 美国 CPI 指数
   - ``s-p-500-earnings``          → 滚动 12 个月盈利（用于 PE1）
2. Shiller 原始数据集 ``ie_data.xls``（*只用于交叉校验*，本身只更新到 2023-09）
3. akshare ``bond_zh_us_rate``（美国 10 年期国债收益率日频，1990-12 起）

输出
----
- ``data/equity_bond_spread_us/raw/``：原始 HTML / XLS / CSV，全部算 SHA-256
- ``data/equity_bond_spread_us/panel_us.csv``：清洗后面板
- ``data/equity_bond_spread_us/manifest_us.json``：来源、字段定义、缺口、SHA-256
- ``data/equity_bond_spread_us/validation_us.json``：跨源一致性核验结果

原则
----
- 原始文件与清洗后文件分开保存，两者都算哈希；
- 缺失保留为空，**不插值**；
- 拼接点显式记录，不隐藏；
- 抓取失败直接报错退出，不写半成品。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from deepquant.research.equity_bond_spread_us.data import sha256_file, month_end  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "equity_bond_spread_us"
RAW_DIR = DATA_DIR / "raw"

MULT_PL_TABLES = {
    "cape": ("shiller-pe", "CAPE（周期调整市盈率，10 年通胀调整盈利口径）"),
    "price": ("s-p-500-historical-prices", "标普 500 指数月度均价（点数）"),
    "div_yield": ("s-p-500-dividend-yield", "滚动 12 个月股息率（%）"),
    "cpi": ("cpi", "美国 CPI 指数（1982-84=100 口径，multpl 转载）"),
    "earnings": ("s-p-500-earnings", "滚动 12 个月盈利（指数点数）"),
}

SHILLER_XLS_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
SHILLER_TOTAL_BYTES = 1628672  # 已知完整大小，用于断点续传校验

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
)}

UA = "Mozilla/5.0 (compatible; DeepQuant research data fetch)"


def _get(url: str, timeout: int = 120, retries: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return fh.read()
        except Exception as exc:  # noqa: BLE001 - 网络异常需重试后报错
            last = exc
            time.sleep(2 + 2 * i)
    raise RuntimeError(f"下载失败 {url}: {last}")


def parse_multpl(raw_html: str) -> pd.DataFrame:
    """解析 multpl 月度表 → DataFrame[month, value, asof_day]。"""
    text = html_lib.unescape(raw_html)
    recs = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        cells = [
            re.sub(r"<[^>]+>", "", c).replace("\u2002", "").replace("\n", " ").strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        ]
        if len(cells) != 2:
            continue
        m = re.match(r"^([A-Za-z]{3,4})\.? (\d{1,2}), (\d{4})$", cells[0])
        if not m:
            continue
        mon = m.group(1)[:3].title()
        if mon not in MONTHS:
            continue
        value = cells[1].replace(",", "").replace("%", "").replace("†", "").strip()
        try:
            val = float(value)
        except ValueError:
            continue
        recs.append((int(m.group(3)), MONTHS[mon], int(m.group(2)), val))
    df = pd.DataFrame(recs, columns=["year", "month", "day", "value"])
    if df.empty:
        return pd.DataFrame(columns=["month", "value", "asof_day"])
    df = df.drop_duplicates(["year", "month"], keep="first")
    df["month"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1)) + pd.offsets.MonthEnd(0)
    return df[["month", "value", "day"]].rename(columns={"day": "asof_day"}).sort_values("month")


def download_shiller(dest: Path, force: bool = False) -> None:
    """断点续传下载 Shiller xls（该服务器很慢，直连常被截断）。

    关键点：**边下边写**。单次请求超时或中途断开时，已收到的字节要落盘，
    否则每次都从零开始，永远下不完（第一次实现就踩了这个坑：300s 超时
    结束时什么都没保存，6 次尝试全部白跑）。
    """
    if dest.exists() and not force and dest.stat().st_size == SHILLER_TOTAL_BYTES:
        return
    if force and dest.exists():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(8):
        have = dest.stat().st_size if dest.exists() else 0
        if have >= SHILLER_TOTAL_BYTES:
            break
        req = urllib.request.Request(
            SHILLER_XLS_URL, headers={"User-Agent": UA, "Range": f"bytes={have}-"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as fh, open(dest, "ab") as out:
                while True:
                    chunk = fh.read(32768)
                    if not chunk:
                        break
                    out.write(chunk)
                    if dest.stat().st_size >= SHILLER_TOTAL_BYTES:
                        break
        except Exception as exc:  # noqa: BLE001 - 该源经常中途断开，续传即可
            got = dest.stat().st_size if dest.exists() else 0
            print(f"  续传第 {attempt + 1} 次中断（{type(exc).__name__}），已保存 {got} 字节")
            time.sleep(2)
    size = dest.stat().st_size if dest.exists() else 0
    if size < SHILLER_TOTAL_BYTES:
        raise RuntimeError(f"Shiller xls 不完整：{size}/{SHILLER_TOTAL_BYTES}")


def parse_shiller(path: Path) -> pd.DataFrame:
    """解析 Shiller xls 的 Data 表（仅取用于校验的列）。"""
    import xlrd

    wb = xlrd.open_workbook(str(path))
    sh = wb.sheet_by_name("Data")
    rows = []
    for r in range(8, sh.nrows):
        d = sh.cell_value(r, 0)
        if not isinstance(d, float):
            continue

        def num(c: int) -> float:
            v = sh.cell_value(r, c)
            return float(v) if isinstance(v, (int, float)) else np.nan

        month = int(round((d - int(d)) * 100 + 0.5)) or 1
        year = int(d) if month > 0 else int(d) - 1
        rows.append(
            {
                "month": pd.Timestamp(year=year, month=max(1, month), day=1) + pd.offsets.MonthEnd(0),
                "s_price": num(1),
                "s_dividend": num(2),
                "s_earnings": num(3),
                "s_cpi": num(4),
                "s_gs10": num(6),
                "s_cape": num(12),
            }
        )
    return pd.DataFrame(rows).set_index("month").sort_index()


def fetch_us10y_daily(dest: Path) -> pd.DataFrame:
    """akshare 美国 10 年期国债收益率日频。"""
    import akshare as ak

    df = ak.bond_zh_us_rate(start_date="19600101")
    df["日期"] = pd.to_datetime(df["日期"])
    df = df[["日期", "美国国债收益率10年"]].rename(
        columns={"日期": "date", "美国国债收益率10年": "us10y"}
    )
    df.to_csv(dest, index=False, encoding="utf-8")
    return df.dropna()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="强制重新下载所有原始文件")
    ap.add_argument("--offline", action="store_true", help="只用本地已有原始文件")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------------------------------------------------------------- multpl
    multpl: dict[str, pd.DataFrame] = {}
    asof_last: dict[str, int] = {}
    for key, (slug, _desc) in MULT_PL_TABLES.items():
        raw_path = RAW_DIR / f"multpl_{slug}.html"
        if args.offline and raw_path.exists():
            raw = raw_path.read_text(encoding="utf-8", errors="ignore")
            print(f"[multpl] {key} 使用本地原始文件")
        else:
            url = f"https://www.multpl.com/{slug}/table/by-month"
            print(f"[multpl] {key} ← {url}")
            raw_bytes = _get(url)
            raw_path.write_bytes(raw_bytes)
            raw = raw_bytes.decode("utf-8", errors="ignore")
        df = parse_multpl(raw)
        if df.empty:
            raise RuntimeError(f"multpl 表 {slug} 解析为空，拒绝继续")
        multpl[key] = df.set_index("month")["value"].rename(key)
        asof_last[key] = int(df.asof_day.iloc[-1])
        print(f"          {len(df)} 行，{df.month.min().date()} ~ {df.month.max().date()}，"
              f"最新 asof 日={asof_last[key]}")

    # ---------------------------------------------------------------- Shiller
    shiller_path = RAW_DIR / "shiller_ie_data.xls"
    if args.offline and shiller_path.exists():
        print("[shiller] 使用本地原始文件")
    else:
        print("[shiller] 下载 ie_data.xls（断点续传）")
        download_shiller(shiller_path, force=args.force)
    shiller = parse_shiller(shiller_path)
    print(f"          {len(shiller)} 行，{shiller.index.min().date()} ~ {shiller.index.max().date()}")

    # ---------------------------------------------------------------- 美债 10Y
    us10y_path = RAW_DIR / "akshare_us10y_daily.csv"
    if args.offline and us10y_path.exists():
        us = pd.read_csv(us10y_path, parse_dates=["date"])
        print("[us10y] 使用本地原始文件")
    else:
        print("[us10y] akshare bond_zh_us_rate 抓取")
        us = fetch_us10y_daily(us10y_path)
    us_m = us.set_index("date")["us10y"].resample("ME").mean().dropna()
    print(f"          {len(us)} 日频行 → {len(us_m)} 个月，{us_m.index.min().date()} ~ {us_m.index.max().date()}")

    # ---------------------------------------------------------------- 拼接债
    first_ak = us_m.index.min()
    y10 = shiller["s_gs10"].copy()
    y10 = y10[y10.index < first_ak]
    y10 = pd.concat([y10, us_m.rename("s_gs10")]).sort_index()
    y10_source = pd.Series(
        np.where(y10.index < first_ak, "shiller_gs10", "akshare_us10y_monthly"),
        index=y10.index,
        name="y10_source",
    )

    # ---------------------------------------------------------------- 面板
    panel = pd.concat(
        [
            multpl["price"].rename("price"),
            multpl["cape"].rename("cape"),
            multpl["div_yield"].rename("div_yield"),
            multpl["cpi"].rename("cpi"),
            multpl["earnings"].rename("earnings"),
            y10.rename("y10"),
            y10_source,
        ],
        axis=1,
    ).sort_index()

    # 最新月份可能是未走完的当月（asof 日不是月末）→ 标记
    panel["partial_month"] = False
    last_month = panel.index.max()
    now = pd.Timestamp.now("UTC").tz_localize(None)
    if (last_month.year, last_month.month) == (now.year, now.month):
        if asof_last.get("price", 1) < last_month.days_in_month:
            panel.loc[last_month, "partial_month"] = True

    # 只保留四项核心数据齐全的月份（不插值）
    core = ["price", "cape", "div_yield", "cpi", "y10"]
    complete = panel.dropna(subset=core)
    panel = panel.loc[complete.index.min() :]

    panel_out = panel.reset_index().rename(columns={"index": "month"})
    panel_out["month"] = pd.to_datetime(panel_out["month"]).dt.strftime("%Y-%m-%d")
    panel_path = DATA_DIR / "panel_us.csv"
    panel_out.to_csv(panel_path, index=False, encoding="utf-8", lineterminator="\n")
    print(f"[panel] {len(panel_out)} 月，{panel_out.month.iloc[0]} ~ {panel_out.month.iloc[-1]}")

    # ---------------------------------------------------------------- 校验
    idx = panel.index
    overlap = idx.intersection(shiller.index)
    val = {"overlap_months": int(len(overlap))}

    def _cmp(mine: pd.Series, theirs: pd.Series, label: str) -> None:
        j = pd.concat([mine.rename("a"), theirs.rename("b")], axis=1, sort=True).loc[overlap].dropna()
        d = (j.a - j.b).abs()
        val[label] = {
            "n": int(len(j)),
            "max_abs_diff": round(float(d.max()), 6),
            "median_abs_diff": round(float(d.median()), 6),
            "corr": round(float(j.a.corr(j.b)), 6),
            "worst_month": str(d.idxmax().date()) if len(d) and d.notna().any() else None,
        }

    _cmp(panel["cape"], shiller["s_cape"], "cape_vs_shiller")
    _cmp(panel["price"], shiller["s_price"], "price_vs_shiller")
    _cmp(panel["cpi"], shiller["s_cpi"], "cpi_vs_shiller")
    _cmp(panel["div_yield"], shiller["s_dividend"] / shiller["s_price"] * 100, "divyield_vs_shiller")

    both = pd.concat([shiller["s_gs10"].rename("shiller"), us_m.rename("akshare")], axis=1, sort=True).dropna()
    val["y10_splice_check"] = {
        "n": int(len(both)),
        "mean_abs_diff": round(float((both.shiller - both.akshare).abs().mean()), 6),
        "max_abs_diff": round(float((both.shiller - both.akshare).abs().max()), 6),
        "corr": round(float(both.shiller.corr(both.akshare)), 6),
        "splice_month": str(first_ak.date()),
        "months_source": {
            "shiller_gs10": int((panel["y10_source"] == "shiller_gs10").sum()),
            "akshare_us10y_monthly": int((panel["y10_source"] == "akshare_us10y_monthly").sum()),
        },
    }

    expected = pd.date_range(idx.min(), idx.max(), freq="ME")
    val["missing_months"] = [str(d.date()) for d in expected.difference(idx)]
    val["n_months"] = int(len(idx))
    val["first_month"] = str(idx.min().date())
    val["last_month"] = str(idx.max().date())
    val["partial_month_flagged"] = bool(panel["partial_month"].any())
    val["earnings_last_month"] = str(multpl["earnings"].index.max().date())
    val["cape_range"] = [round(float(panel["cape"].min()), 2), round(float(panel["cape"].max()), 2)]
    val["y10_range"] = [round(float(panel["y10"].min()), 2), round(float(panel["y10"].max()), 2)]

    (DATA_DIR / "validation_us.json").write_text(
        json.dumps(val, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("[validation]")
    for k in ("cape_vs_shiller", "price_vs_shiller", "cpi_vs_shiller", "divyield_vs_shiller"):
        v = val[k]
        print(f"  {k}: n={v['n']} max|diff|={v['max_abs_diff']} corr={v['corr']}")
    print(f"  y10 拼接核验: n={val['y10_splice_check']['n']} "
          f"mean|diff|={val['y10_splice_check']['mean_abs_diff']} "
          f"corr={val['y10_splice_check']['corr']}")

    # ---------------------------------------------------------------- 清单
    manifest = {
        "generated_at_utc": started,
        "base_dir": str(DATA_DIR.relative_to(REPO_ROOT)).replace("\\", "/"),
        "sources": [
            {
                "name": "multpl.com 月度表",
                "url": "https://www.multpl.com/<slug>/table/by-month",
                "slugs": {k: v[0] for k, v in MULT_PL_TABLES.items()},
                "used_for": [v[1] for v in MULT_PL_TABLES.values()],
                "caveats": [
                    "二手聚合站，非指数公司原始发布；已与 Shiller 原始数据集逐月比对（见 validation_us.json）",
                    "CAPE 口径沿用 Shiller 方法（10 年通胀调整平均盈利），但历史值随盈利修订而变化，存在回溯修订风险",
                ],
            },
            {
                "name": "Robert Shiller, Irrational Exuberance 数据集",
                "url": SHILLER_XLS_URL,
                "used_for": "仅用于交叉校验（该文件当前只更新至 2023-09）",
                "caveats": ["GS10 在 1953 年前为历史估算序列，不是逐日交易的 CMT 收益率"],
            },
            {
                "name": "akshare bond_zh_us_rate（美国 10 年期国债收益率）",
                "url": "https://akshare.akfamily.xyz/",
                "used_for": "1990-12 起的月度均值，用于拼接长历史国债收益率",
                "caveats": ["日频原始数据，取月度算术平均；来源为公开聚合接口"],
            },
        ],
        "field_definitions": {
            "price": "标普 500 指数月度均价（点数）",
            "cape": "周期调整市盈率（10 年通胀调整平均盈利）",
            "div_yield": "滚动 12 个月股息率（%）",
            "cpi": "美国 CPI 指数",
            "earnings": "滚动 12 个月盈利（指数点数）",
            "y10": "美国 10 年期国债收益率（%），1990-12 前为 Shiller GS10",
            "y10_source": "y10 的取自来源",
            "partial_month": "该月尚未走完（asof 日不是月末），分析时应剔除",
        },
        "splice": {
            "bond": {
                "before": "1990-12：Shiller GS10（月度）",
                "from": "1990-12：akshare 美国 10Y 日频的月度均值",
                "validation": val["y10_splice_check"],
            }
        },
        "files": {},
        "row_count": int(len(panel)),
        "date_range": [str(idx.min().date()), str(idx.max().date())],
        "missing_months": val["missing_months"],
        "no_interpolation": True,
    }
    for p in sorted(list(RAW_DIR.glob("*")) + [panel_path]):
        manifest["files"][str(p.relative_to(DATA_DIR)).replace("\\", "/")] = {
            "sha256": sha256_file(p),
            "bytes": p.stat().st_size,
        }
    (DATA_DIR / "manifest_us.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[manifest] {len(manifest['files'])} 个文件已记哈希 → {DATA_DIR / 'manifest_us.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
