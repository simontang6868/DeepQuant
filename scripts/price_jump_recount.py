"""修复后重算：`price_jump` 的真实跳变数，并拆分「复权噪声 / 疑似真实」。

背景
----
`_check_price_jumps` 在修复前收到的是单列 DataFrame（`checks_extended.py` 里
`df_sorted[["close"]]`），布尔掩码在同形 DataFrame 上做比较 → `len(jumps)`
退化为「行数 − 1」，于是**每只标的都报一条** `count = n_rows − 1` 的告警，
连平盘序列也报。修复见 `docs/RESEARCH_FIXES_REPORT.md` §2。

本脚本**只读**：不跑回测、不改任何落盘结果、不触碰 `gate1_out/`。做四件事：

1. 用修复后的 `validate_daily_extended` 逐只跑，拿真实 `count`；
2. 用**独立实现**（pandas 原生算子，不经 `checks_extended`）重算，
   两者必须逐只相等 —— 否则说明修复本身有问题，脚本以非零码退出；
3. 把每个超阈值跳变按「能否是真实行情」三分类：
   - `nonpositive_endpoint`：相邻两日 close 至少一端 ≤ 0 → 减法复权噪声，
     该日价格本身不可信，`pct_change` 无经济含义；
   - `beyond_limit`：两端都 > 0，但变动 > 21% → 超出 A 股单日涨跌幅上限
     （科创/创业板 20%），纯正价格序列不可能出现 → 复权/数据异常；
   - `within_limit`：两端都 > 0 且 ≤ 21% → **可能**是真实涨跌停或连板。
     仅凭阈值无法判定，故同时给出这批跳变的**最低端点价**：减法复权会把
     价格压到 1 元以下，此时 19% 的「涨跌停」只是分红利差，经济含义为零。
4. 核对历史文档里引用过的跳变数（成立 / 口径不同 / 不在本池无法核对）。

用法::

    python scripts/price_jump_recount.py
    python scripts/price_jump_recount.py --data-root <目录> --out <json>

输出默认写到 `probe_out/price_jump_recount.json`。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "backend" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from deepquant.data.validation.checks_extended import validate_daily_extended  # noqa: E402

THRESHOLD = 0.15
# A 股单日涨跌幅上限 20%（创业板/科创板），留 1 个百分点容差给复权误差。
LIMIT_TOLERANCE = 0.21


def independent_counts(close: pd.Series) -> tuple[int, float]:
    """完全独立的第二实现：不调用被测函数，只用 pandas 原生算子。"""
    pct = close.pct_change().dropna().abs()
    return int((pct > THRESHOLD).sum()), float(pct.max())


def classify(close: pd.Series) -> dict[str, int]:
    """把超阈值跳变按「能否是真实行情」三分类。"""
    pct = close.pct_change()
    prev = close.shift(1)
    mask = pct.abs() > THRESHOLD
    out = {"nonpositive_endpoint": 0, "beyond_limit": 0, "within_limit": 0}
    for i in close.index[mask.fillna(False)]:
        a, b, r = prev.loc[i], close.loc[i], pct.loc[i]
        if a <= 0 or b <= 0:
            out["nonpositive_endpoint"] += 1
        elif abs(r) > LIMIT_TOLERANCE:
            out["beyond_limit"] += 1
        else:
            out["within_limit"] += 1
    return out


def within_limit_price_floor(close: pd.Series) -> float | None:
    """`within_limit` 那批跳变的**最低端点价**（仅作提示，不参与判定）。"""
    pct = close.pct_change()
    prev = close.shift(1)
    mask = (pct.abs() > THRESHOLD) & (pct.abs() <= LIMIT_TOLERANCE)
    mask &= (prev > 0) & (close > 0)
    idx = close.index[mask.fillna(False)]
    if len(idx) == 0:
        return None
    return float(min(prev.loc[idx].min(), close.loc[idx].min()))


def symbol_of(path: Path) -> str:
    """`600809.SH.qfq.csv` → `600809.SH`。"""
    return ".".join(path.name.split(".")[:2])


def historical_claims(by_symbol: dict[str, dict]) -> list[dict]:
    """历史文档里引用过的跳变数，逐条核对（只核对能用本数据集核对的）。"""
    def one(doc: str, text: str, sym: str, expected: int | None, note: str) -> dict:
        row = by_symbol.get(sym)
        if row is None:
            return {
                "doc": doc, "claim": text, "symbol": sym,
                "status": "not_in_pool", "recomputed": None,
                "note": f"{sym} 不在本次数据集内，无法核对；不声称成立与否",
            }
        got = row["independent_count"]
        return {
            "doc": doc, "claim": text, "symbol": sym,
            "historical_value": expected, "recomputed": got,
            "status": "holds" if expected == got else "does_not_hold",
            "note": note,
        }

    return [
        one("docs/EXPANSION_2026-10-17.md:123",
            "泸州老窖 count 报 1942、真实 41", "000568.SZ", 41,
            "报的 1942 恰为 1943 行 − 1，与缺陷机制吻合；41 与本轮独立重算逐位相同"),
        one("docs/B15_8Y_30GROUPS_2026-10-18.md:44",
            "茅台 count = 行数 − 1（1,942/1,943 行）", "600519.SH", None,
            "该标的属历史 30 组实验宇宙，不在本数据集内"),
        one("docs/B15_8Y_30GROUPS_2026-10-18.md:42",
            "宁德 1,593/1,594 行跳变、最大 3683.33%", "300750.SZ", None,
            "该标的属历史 30 组实验宇宙，不在本数据集内"),
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="重算 price_jump 真实计数并拆分（只读，不跑回测）"
    )
    p.add_argument("--data-root", default=r"D:\DeepQuant power by txm\b50_v3",
                   help="行情目录（*.qfq.csv）")
    p.add_argument("--out", default=str(_REPO / "probe_out" / "price_jump_recount.json"),
                   help="输出 JSON 路径")
    args = p.parse_args(argv)

    root = Path(args.data_root)
    files = sorted(root.glob("*.qfq.csv"))
    if not files:
        print(f"[ERROR] 目录内无 *.qfq.csv：{root}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for f in files:
        df = pd.read_csv(f, parse_dates=["date"])
        close = df["close"].astype("float64")

        report = validate_daily_extended(df, price_jump_threshold=THRESHOLD)
        issues = [i for i in report.issues if i.code == "price_jump"]
        reported = int(issues[0].count) if issues else 0

        n_jumps, max_pct = independent_counts(close)
        parts = classify(close)
        if sum(parts.values()) != n_jumps:  # 分类必须覆盖全部跳变，否则口径有漏
            print(f"[ERROR] 分类不守恒：{symbol_of(f)} {parts} vs {n_jumps}",
                  file=sys.stderr)
            return 1

        four = df[["open", "close", "high", "low"]]
        rows.append({
            "symbol": symbol_of(f),
            "n_rows": int(len(df)),
            "reported_count": reported,
            "independent_count": n_jumps,
            "match": reported == n_jumps,
            "max_abs_pct_change": round(max_pct, 6),
            "jump_breakdown": parts,
            "within_limit_price_floor": (
                round(within_limit_price_floor(close), 4)
                if parts["within_limit"] else None
            ),
            "legacy_count_would_be": len(df) - 1,
            "n_nonpositive_close": int((close <= 0).sum()),
            "n_nonpositive_any_ohlc": int((four <= 0).any(axis=1).sum()),
            "reported_message": issues[0].message if issues else "",
        })

    total = len(rows)
    bad = [r for r in rows if not r["match"]]
    with_jumps = [r for r in rows if r["independent_count"] > 0]
    keys = ("nonpositive_endpoint", "beyond_limit", "within_limit")
    agg = {k: sum(r["jump_breakdown"][k] for r in rows) for k in keys}

    out = {
        "generated_by": "scripts/price_jump_recount.py",
        "threshold": THRESHOLD,
        "limit_tolerance": LIMIT_TOLERANCE,
        "data_root": str(root),
        "n_symbols": total,
        "n_match_between_two_implementations": total - len(bad),
        "n_mismatch": len(bad),
        "n_symbols_with_true_jumps": len(with_jumps),
        "n_symbols_without_jumps": total - len(with_jumps),
        "total_jumps": sum(r["independent_count"] for r in rows),
        "jump_breakdown_total": agg,
        "legacy_total_would_be": sum(r["legacy_count_would_be"] for r in rows),
        "symbols": rows,
        "historical_claims": historical_claims({r["symbol"]: r for r in rows}),
    }

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[双实现] 一致 {total - len(bad)}/{total}")
    print(f"[分布] 有真实跳变 {len(with_jumps)}/{total}；无跳变 {total - len(with_jumps)}/{total}")
    print(f"[合计] 真实跳变 {out['total_jumps']} 次；修复前会报 {out['legacy_total_would_be']} 次")
    print(f"[拆分] 复权噪声 {agg['nonpositive_endpoint']} ｜ 超涨跌幅上限 "
          f"{agg['beyond_limit']} ｜ 疑似真实 {agg['within_limit']}")
    print(f"[落盘] {target}")
    print()
    print(f"{'symbol':<12}{'rows':>7}{'jumps':>7}{'noise':>7}{'beyond':>8}{'within':>8}{'max%':>10}")
    for r in sorted(rows, key=lambda x: -x["independent_count"])[:10]:
        b = r["jump_breakdown"]
        print(f"{r['symbol']:<12}{r['n_rows']:>7}{r['independent_count']:>7}"
              f"{b['nonpositive_endpoint']:>7}{b['beyond_limit']:>8}{b['within_limit']:>8}"
              f"{r['max_abs_pct_change'] * 100:>9.1f}%")
    print()
    for c in out["historical_claims"]:
        print(f"- [{c['status']}] {c['doc']} → {c['claim']}（重算 {c['recomputed']}）")

    if bad:
        print("\n[FAIL] 双实现不一致：", [b["symbol"] for b in bad], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
