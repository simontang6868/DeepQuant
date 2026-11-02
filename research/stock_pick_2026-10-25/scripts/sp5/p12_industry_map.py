# -*- coding: utf-8 -*-
"""SP-5 / P1-2 前置：构建「股票 → 东财细分行业」映射。

为什么需要
----------
P1-1 的归因显示：2023-01 起窗口里，收益的 45.9% 来自「买低流动性档」这个 beta，
F3 筛选只贡献 1.4%。但如果低流动性档本身集中在少数行业，
那"低流动性溢价"可能只是"某个行业行情"的伪装——这是当前最大的未排除替代解释。

数据与局限（必须先声明）
----------------------
* 源：东方财富细分行业板块（`akshare.stock_board_industry_cons_em`），**当前时点**的分类；
* 无历史行业映射 → 用它做 2023-2026 的行业归属有**前视偏差**。缓解理由：行业归属相对稳定
  （钢铁股一直是钢铁），且我们只用它做**集中度与中性化**，不做择时；
* 该局限在任何引用中都必须出现，不得省略。

输出：sp5_out/industry_map.json（code → 行业名）、sp5_out/industry_meta.json
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import akshare as ak
import pandas as pd

BASE = pathlib.Path(__file__).resolve().parents[2]
OUT = BASE / "sp5_out"
CACHE = OUT / "industry_map.json"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    if CACHE.exists() and os.environ.get("SP5_REFRESH_IND") != "1":
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        print(f"[cache] 已有 {len(d['code2ind'])} 只 / {len(set(d['code2ind'].values()))} 行业，"
              f"跳过（SP5_REFRESH_IND=1 可强制刷新）")
        return 0

    boards = ak.stock_board_industry_name_em()
    names = boards["板块名称"].tolist()
    print(f"[boards] {len(names)} 个细分行业")

    code2ind: dict[str, str] = {}
    fail = []
    for i, nm in enumerate(names):
        for attempt in range(3):
            try:
                c = ak.stock_board_industry_cons_em(symbol=nm)
                for code in c["代码"].astype(str).str.zfill(6):
                    # 一只股票可能出现在多个板块（东财允许），保留首个（按板块排名）
                    code2ind.setdefault(code, nm)
                break
            except Exception as e:
                if attempt == 2:
                    fail.append({"board": nm, "err": f"{type(e).__name__}: {str(e)[:80]}"})
                else:
                    time.sleep(1.5)
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(names)}｜已映射 {len(code2ind)} 只｜失败 {len(fail)}")
        time.sleep(0.25)

    print(f"[done] 映射 {len(code2ind)} 只，覆盖 {len(set(code2ind.values()))} 个行业，失败板块 {len(fail)}")

    # 覆盖率：对照面板里出现过的股票
    panel = pd.read_parquet(BASE / "data" / "panel_monthly.parquet", columns=["code", "date"])
    panel = panel[panel["date"] >= "20230131"]
    codes = set(panel["code"].unique())
    hit = codes & set(code2ind)
    print(f"[coverage] 2023 起面板 {len(codes)} 只，命中 {len(hit)}（{len(hit)/len(codes)*100:.2f}%）")

    CACHE.write_text(json.dumps({"code2ind": code2ind, "fail": fail,
                                 "boards": len(names),
                                 "coverage": {"panel_codes": len(codes), "hit": len(hit)},
                                 "as_of": time.strftime("%Y-%m-%d"),
                                 "source": "eastmoney 细分行业板块（当前时点，无历史映射）"},
                                ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[save] {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
