"""构建扩样股票池（可复现、带偏差声明）。

为什么需要它
------------
Step 7.5 原研究用的是 6 只手挑标的（茅台/平安/宁德/五粮液/长电/平安银行），
`docs/EXTERNAL_VALIDITY_2026-10-17.md` 已判定：外推检验 22 只里 15 只为 0、7 只为负、
0 只为正，且「价格 → 参与率」相关性 r=-0.772 完全由茅台单点支撑。
6 只样本无法支撑任何总体结论 → 必须扩样。

本脚本只做一件事：把「用哪些标的」变成一个**有据可查、可重跑、偏差已声明**的事实。

偏差声明（必须与结果一起汇报，不得省略）
----------------------------------------
1. **幸存者偏差（最关键）**：成分股取的是**抓取当日**的指数成分，不是 2017 年的成分。
   用它研究 2017-2024，等于只研究「活到今天且仍被纳入指数」的公司 →
   收益类指标系统**偏乐观**，退市/被剔除的失败样本根本不在池子里。
   因此本样本**不得**用于「策略能赚钱」类结论，只能用于：
     - 参与率 / 事件密度 / 零中位数这类**结构性问题**的分布检验；
     - 「原 6 只样本是否典型」的对照。
2. **抽样非随机**：按代码升序等距抽取（每 k 只取 1），不是随机抽样。
   等距抽样的好处是无随机种子争议、可逐字复现；代价是代码段与行业相关
   （600xxx/000xxx/300xxx 分段），行业分布不完全均衡。
3. **区间内新股**：2017 年后上市的成分股在早期窗口无数据，会被 walk-forward
   的预热期自然截断；数据质量闸门也会拦下 qfq 负价标的。存活数量要如实报告。

用法
----
    python data/step75/build_universe.py --index 000300 --n 50 \
        --out data/step75/universe/csi300_50.json

输出 JSON 结构：
    {"meta": {...来源/规则/偏差...}, "stocks": [{"code","name","industry"}]}
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import akshare as ak

# 名称里带这些字样的标的不进研究池：ST/*ST 有退市与涨跌幅特殊规则，
# 会污染收益分布；B 股、北交所流动性与主板上不可比。
_EXCLUDE_KEYWORDS = ("ST", "退", "B股")
_BOARD_BY_PREFIX = (
    ("688", "科创板"),
    ("300", "创业板"),
    ("301", "创业板"),
    ("600", "沪主板"),
    ("601", "沪主板"),
    ("603", "沪主板"),
    ("605", "沪主板"),
    ("000", "深主板"),
    ("001", "深主板"),
    ("002", "深主板"),
)


def _tencent_symbol(code: str) -> str:
    """6 位数字代码 → 腾讯行情接口要求的 sh/sz 前缀代码。"""
    return ("sh" if code[0] == "6" else "sz") + code


def _board_of(code: str) -> str:
    for prefix, board in _BOARD_BY_PREFIX:
        if code.startswith(prefix):
            return board
    return "其他"


def fetch_constituents(index_code: str) -> tuple[list[dict], str]:
    """取指数成分股。主源中证指数官网，备源新浪。返回 (列表, 实际来源)。"""
    errors = []
    try:
        df = ak.index_stock_cons_csindex(symbol=index_code)
        rows = [
            {"code": str(r["成分券代码"]).zfill(6), "name": str(r["成分券名称"])}
            for _, r in df.iterrows()
        ]
        return rows, "csindex:index_stock_cons_csindex"
    except Exception as exc:                                    # noqa: BLE001
        errors.append(f"csindex: {type(exc).__name__}: {exc}")

    try:
        df = ak.index_stock_cons(symbol=index_code)
        rows = [
            {"code": str(r["品种代码"]).zfill(6), "name": str(r["品种名称"])}
            for _, r in df.iterrows()
        ]
        return rows, "sina:index_stock_cons"
    except Exception as exc:                                    # noqa: BLE001
        errors.append(f"sina: {type(exc).__name__}: {exc}")

    raise RuntimeError("成分股接口全部失败 → " + " | ".join(errors))


def select_evenly(rows: list[dict], n: int) -> list[dict]:
    """代码升序等距抽取 n 只。

    等距而非随机：无种子争议、可逐字复现。若总数 <= n 则全取。

    步长取 (N-1)/(n-1) 而不是 N/n：后者取到的是 0, k, 2k, ...，末端够不到最后一个元素
    （300 取 50 时末位只到索引 294，最后 5 只永远进不了样本）。用 (N-1)/(n-1)
    才能保证首尾都在样本里，行业分布不被代码分段切掉一角。
    """
    ordered = sorted(rows, key=lambda r: r["code"])
    if n <= 0:
        raise ValueError(f"n 必须为正，实际 {n}")
    if len(ordered) <= n:
        return ordered

    picked, seen = [], set()
    span = len(ordered) - 1
    denom = n - 1 if n > 1 else 1
    for i in range(n):
        idx = int(round(i * span / denom))
        idx = min(idx, span)
        if idx in seen:
            continue
        seen.add(idx)
        picked.append(ordered[idx])
    return picked


def build(index_code: str, n: int) -> dict:
    raw, source = fetch_constituents(index_code)
    total_raw = len(raw)

    kept, dropped = [], []
    for r in raw:
        name = r["name"]
        code = r["code"]
        if any(k in name for k in _EXCLUDE_KEYWORDS):
            dropped.append({**r, "reason": "name_filter"})
            continue
        board = _board_of(code)
        if board == "其他":
            dropped.append({**r, "reason": f"unsupported_board:{code}"})
            continue
        kept.append({**r, "board": board, "symbol": code if code[0] == "6" else code})

    picked = select_evenly(kept, n)
    stocks = [
        {
            "code": f"{r['code']}.{'SH' if r['code'][0] == '6' else 'SZ'}",
            "tencent_symbol": _tencent_symbol(r["code"]),
            "name": r["name"],
            "board": r["board"],
            # 行业字段留空：指数成分接口不提供行业分类，**不臆造**。
            "industry": "",
        }
        for r in picked
    ]

    meta = {
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "index_code": index_code,
        "source": source,
        "sampling_rule": (
            f"代码升序等距抽取，每 {len(kept) / float(len(stocks)):.2f} 只取 1，目标 n={n}"
        ),
        "n_raw": total_raw,
        "n_after_filter": len(kept),
        "n_selected": len(stocks),
        "n_dropped": len(dropped),
        "dropped_sample": dropped[:10],
        "bias_survivorship": (
            "成分股为抓取当日快照，非 2017 年历史成分。收益类指标系统偏乐观，"
            "缺失退市/被剔除的失败样本。仅可用于参与率/事件密度/零中位数等结构性问题，"
            "不得用于『策略能赚钱』类结论。"
        ),
        "bias_sampling": "等距抽样非随机，代码分段与行业相关，行业分布不完全均衡。",
    }
    return {"meta": meta, "stocks": stocks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建可复现的扩样股票池")
    ap.add_argument("--index", default="000300", help="指数代码，默认 000300 沪深300")
    ap.add_argument("--n", type=int, default=50, help="抽取数量，默认 50")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    args = ap.parse_args(argv)

    payload = build(args.index, args.n)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    m = payload["meta"]
    print(
        f"[OK] {m['n_selected']}/{m['n_after_filter']} 只（原始 {m['n_raw']}）"
        f" 来源={m['source']} → {out}"
    )
    print(f"[BIAS] {m['bias_survivorship']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
