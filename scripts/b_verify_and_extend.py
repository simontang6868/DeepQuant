"""B 线：D1/D2 真值复核 + 行情延伸下载 + 沪深300 基准。

只读本地既有数据；新数据写入**新目录**（data/step75_ext/、data/benchmark/），
不覆盖 data/step75/ 下任何既有文件。

用法（仓库根目录）：python scripts/b_verify_and_extend.py
输出：probe_out/b_verify_and_extend.json（本地产物，不入库；关键数字已抄进
docs/DATA_EXTENSION_REPORT_2026-10-19.md 与 docs/PRICE_JUMP_RECOUNT_B_2026-10-19.json）
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STEP75 = ROOT / "data" / "step75"
EXT = ROOT / "data" / "step75_ext"
BENCH = ROOT / "data" / "benchmark"
OUT = ROOT / "probe_out" / "b_verify_and_extend.json"
THRESHOLD = 0.15

SYMBOLS = {
    "600519.SH": "sh600519",
    "000001.SZ": "sz000001",
    "601318.SH": "sh601318",
    "300750.SZ": "sz300750",
    "000858.SZ": "sz000858",
    "600900.SH": "sh600900",
}


def jump_stats(close: pd.Series, threshold: float = THRESHOLD) -> dict:
    """与 checks_extended._check_price_jumps 修复后实现完全一致的口径。"""
    s = pd.to_numeric(close, errors="coerce").dropna()
    pct = s.pct_change().dropna().abs()
    jumps = pct[pct > threshold]
    return {
        "n_rows": int(len(s)),
        "n_jumps": int(len(jumps)),
        "max_pct": float(pct.max()) if len(pct) else None,
    }


def load_local(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def fetch_tx(sym: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_hist_tx(
        symbol=sym, start_date=start, end_date=end, adjust="qfq"
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


EXT.mkdir(parents=True, exist_ok=True)
result: dict = {"generated_at": pd.Timestamp.now().isoformat(timespec="seconds"), "items": {}}
errs: list[dict] = []

# ---------------------------------------------------------------- D2
try:
    df = load_local(STEP75 / "000001.SZ.qfq.csv")
    win = df[(df["date"] >= "2022-01-04") & (df["date"] <= "2024-12-31")].reset_index(drop=True)
    st = jump_stats(win["close"])
    result["items"]["D2_step74_725"] = {
        "symbol": "000001.SZ",
        "window": "2022-01-04~2024-12-31",
        "delta_rows_1": st["n_rows"] - 1,
        "claimed": 725,
        "recomputed": st,
        "match_rows_minus_1": st["n_rows"] - 1 == 725,
    }
except Exception:
    errs.append({"item": "D2", "error": traceback.format_exc(limit=2)})

# ---------------------------------------------------------------- D1
try:
    d = fetch_tx("sh600809", "20170101", "20241231")
    st = jump_stats(d["close"])
    result["items"]["D1_600809_41"] = {
        "symbol": "600809.SH",
        "window": f"{d['date'].min().date()}~{d['date'].max().date()}",
        "claimed_rows": 1943,
        "claimed_jumps": 41,
        "recomputed": st,
        "match_rows": st["n_rows"] == 1943,
        "match_jumps": st["n_jumps"] == 41,
    }
    d.to_csv(EXT / "600809.SH.qfq.csv", index=False)
except Exception:
    errs.append({"item": "D1", "error": traceback.format_exc(limit=2)})

# ---------------------------------------------------------------- 延伸下载 + 重叠一致性
result["items"]["extension"] = {}
for code, sym in SYMBOLS.items():
    try:
        new = fetch_tx(sym, "20170101", "20261019")
        old = load_local(STEP75 / f"{code}.qfq.csv")
        # 重叠窗口逐日取比值，检查是否只差一个常量复权因子
        merged = old[["date", "close"]].merge(
            new[["date", "close"]], on="date", suffixes=("_old", "_new")
        )
        ok = merged[(merged["close_old"] > 0) & (merged["close_new"] > 0)]
        ratio = (ok["close_new"] / ok["close_old"]) if len(ok) else pd.Series(dtype=float)
        result["items"]["extension"][code] = {
            "old_rows": int(len(old)),
            "old_range": f"{old['date'].min().date()}~{old['date'].max().date()}",
            "new_rows": int(len(new)),
            "new_range": f"{new['date'].min().date()}~{new['date'].max().date()}",
            "new_rows_after_20241231": int((new["date"] > "2024-12-31").sum()),
            "overlap_days": int(len(merged)),
            "ratio_min": float(ratio.min()) if len(ratio) else None,
            "ratio_max": float(ratio.max()) if len(ratio) else None,
            "ratio_is_constant": bool(len(ratio) and (ratio.max() - ratio.min()) < 1e-6),
            "old_vs_new_jumps": {
                "old": jump_stats(old["close"]),
                "new_same_window": jump_stats(
                    new[(new["date"] >= old["date"].min()) & (new["date"] <= old["date"].max())]["close"]
                ),
            },
        }
        new.to_csv(EXT / f"{code}.qfq.csv", index=False)
    except Exception:
        errs.append({"item": f"ext:{code}", "error": traceback.format_exc(limit=2)})

# ---------------------------------------------------------------- 沪深300 基准
BENCH.mkdir(parents=True, exist_ok=True)
try:
    import akshare as ak

    idx = ak.stock_zh_index_daily(symbol="sh000300")
    idx["date"] = pd.to_datetime(idx["date"])
    idx = idx.sort_values("date").reset_index(drop=True)
    idx.to_csv(BENCH / "hs300_daily.csv", index=False)
    result["items"]["benchmark_hs300"] = {
        "rows": int(len(idx)),
        "range": f"{idx['date'].min().date()}~{idx['date'].max().date()}",
        "columns": list(idx.columns),
        "rows_since_2017": int((idx["date"] >= "2017-01-01").sum()),
        "rows_since_2022": int((idx["date"] >= "2022-01-04").sum()),
    }
except Exception:
    errs.append({"item": "hs300", "error": traceback.format_exc(limit=2)})

result["errors"] = errs
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
