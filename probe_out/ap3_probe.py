"""A-P3 数据可行性探测：只做「能不能拿到 / 拿到什么形状」的实测，不做任何推断。"""
import json
import time
import traceback
from pathlib import Path

import akshare as ak
import pandas as pd

OUT = Path(__file__).resolve().parent / "a_p3_probe.json"
records = []


def probe(name, fn, **kw):
    t0 = time.time()
    rec = {"name": name, "kwargs": {k: str(v) for k, v in kw.items()}}
    try:
        df = fn(**kw)
        rec["ok"] = True
        rec["rows"] = int(len(df)) if hasattr(df, "__len__") else None
        rec["cols"] = [str(c) for c in getattr(df, "columns", [])]
        rec["elapsed_s"] = round(time.time() - t0, 2)
        if isinstance(df, pd.DataFrame) and len(df):
            # 找日期类列，给出真实覆盖范围
            for c in df.columns:
                if "日期" in str(c) or "date" in str(c).lower():
                    s = pd.to_datetime(df[c], errors="coerce").dropna()
                    if len(s):
                        rec["date_col"] = str(c)
                        rec["date_min"] = str(s.min().date())
                        rec["date_max"] = str(s.max().date())
                        break
            rec["sample"] = df.head(2).astype(str).to_dict("records")
    except Exception as exc:  # noqa: BLE001 - 探测脚本要把失败原因原样留下
        rec["ok"] = False
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["trace_tail"] = traceback.format_exc().splitlines()[-1][:200]
    records.append(rec)
    print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)
    return rec


# ---------- ① 个股日线：能否延到最近 ----------
probe("stock_zh_a_hist_em_qfq", ak.stock_zh_a_hist, symbol="600519", period="daily",
      start_date="20250101", end_date="20261019", adjust="qfq")
probe("stock_zh_a_hist_tx_qfq", ak.stock_zh_a_hist_tx, symbol="sh600519",
      start_date="20250101", end_date="20261019", adjust="qfq")

# ---------- ② 指数基准 ----------
for idx in ("000300", "000905", "000852"):
    probe(f"index_zh_a_hist_{idx}", ak.index_zh_a_hist, symbol=idx, period="daily",
          start_date="20180101", end_date="20261019")

# ---------- ③ PIT 成分股 ----------
probe("index_stock_cons_csindex_000300", ak.index_stock_cons_csindex, symbol="000300")
probe("index_stock_cons_sina_000300", ak.index_stock_cons, symbol="000300")
probe("index_stock_cons_weight_csindex_000300", ak.index_stock_cons_weight_csindex, symbol="000300")

# ---------- ④ 财务 / 基本面 ----------
probe("stock_financial_abstract_600519", ak.stock_financial_abstract, symbol="600519")
probe("stock_financial_analysis_indicator_600519", ak.stock_financial_analysis_indicator,
      symbol="600519", start_year="2023")

OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved", OUT)
