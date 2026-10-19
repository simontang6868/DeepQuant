"""把「加性前复权」行情换成「乘性前复权」行情。

为什么需要这个脚本（2026-10-19 发现）
------------------------------------
腾讯 / 东财的 `qfq` 用的是**加性**口径：`qfq_t = raw_t - 未来累计分红_t`。
分红累计一旦超过历史价位，`qfq_t` 就会趋零甚至变负。实测：

    300750.SZ 2018-06-11  qfq close = -2.07   （宁德时代上市首日）
    600809.SH 2018-11-26  qfq close = -0.46   （山西汾酒）
    000568.SZ 2016 段     qfq close 最低 -12.98

价位过零后，**日收益率的分母失去意义**（0.06 → 2.15 = +3483%），
任何用这条序列算出来的窗口收益、回撤、夏普都是噪声。而本项目的
`validate_daily_extended` 只把负价判 error（会拦掉），把 >15% 跳变判 warning
（**不会拦**），所以「价位很小但仍为正」的样本会静默进入回测。

修法（数学上等价、且不引入新假设）
--------------------------------
后复权是乘性的：`hfq_t = raw_t * F_t`，`F_t` 只在除权日跳变，
所以 `hfq` 的收益率**就是**正确的前复权收益率。只需把水平值缩放回真实价位：

    scale = raw_anchor / hfq_anchor          （同一交易日）
    qfq_mult_t = hfq_t * scale

结果：末值与真实收盘价一致（可读、可用于「一手买不买得起」判断），
历史段按乘性缩放（永不为负），日收益率与 hfq 完全一致。

数据来源
--------
- `hfq`：`web.ifzq.gtimg.cn/appstock/app/fqkline/get`（腾讯，逐段回翻，640 根/次）
- `raw`：`web.ifzq.gtimg.cn/appstock/app/kline/kline`（腾讯不复权，只用来取锚点）

不做
----
- 不填充任何缺失行情；取不到就记 `no_quote` / `fail`，不插值
- 不改任何价格（只乘一个常数），不做平滑、不做去极值
- 不覆盖已生成的修复文件（`--force` 才覆盖）

运行
----
    PYTHONPATH=backend/src python scripts/repair_qfq_mult.py --only A --limit 3
    PYTHONPATH=backend/src python scripts/repair_qfq_mult.py            # 全量 312 + 7
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "qfq_mult"
MANIFEST = ROOT / "docs" / "QFQ_MULT_MANIFEST_2026-10-19.json"
LISTED_MANIFEST = ROOT / "docs" / "STAGE0_LISTED_QUOTES_2026-10-19.json"
DELIST_MANIFEST = ROOT / "docs" / "DELIST_UNIVERSE_2026-10-19.json"
STEP75_DIR = ROOT / "data" / "step75"

START = "2016-01-01"
END = "2026-10-16"
PAGE = 640
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TX_FQ = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,{beg},{end},{n},hfq"
TX_RAW = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={sym},day,{beg},{end},{n}"


def _get(url: str, tries: int = 4) -> dict:
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 + 1.5 * i)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def tx_symbol(code: str) -> str:
    """'000001.SZ' -> 'sz000001'；'600809.SH' -> 'sh600809'。"""
    num, mkt = code.split(".")
    return f"{mkt.lower()}{num}"


def fetch_hfq(code: str) -> list[list[str]]:
    """回翻式分页取全段 hfq（每次 640 根，按首根日期往前挪）。"""
    sym = tx_symbol(code)
    rows: dict[str, list[str]] = {}
    end = END
    for _ in range(12):  # 2600 根 / 640 ≈ 5 次，12 次足够
        payload = _get(TX_FQ.format(sym=sym, beg=START, end=end, n=PAGE))
        data = payload.get("data", {}).get(sym)
        batch = data.get("hfqday") if isinstance(data, dict) else None
        if not batch:
            break
        for r in batch:
            rows[r[0]] = r
        first = batch[0][0]
        if first <= START:
            break
        end = (datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return [rows[d] for d in sorted(rows)]


def fetch_raw_anchor(code: str) -> tuple[str, float] | None:
    """取不复权序列的最后一根（锚点），返回 (date, close)。"""
    sym = tx_symbol(code)
    payload = _get(TX_RAW.format(sym=sym, beg=START, end=END, n=PAGE))
    data = payload.get("data", {}).get(sym)
    batch = data.get("day") if isinstance(data, dict) else None
    if not batch:
        return None
    last = batch[-1]
    return last[0], float(last[2])


def repair_one(code: str, sub: str, force: bool = False) -> dict:
    out = OUT_DIR / sub / f"{code}.qfq.csv"
    rec: dict = {"code": code, "group": sub, "file": str(out.relative_to(ROOT)).replace("\\", "/")}
    if out.exists() and out.stat().st_size > 500 and not force:
        df_rows = out.read_text(encoding="utf-8").strip().splitlines()[1:]
        rec.update(status="cached", n_rows=len(df_rows))
        return rec

    try:
        hfq = fetch_hfq(code)
        anchor = fetch_raw_anchor(code)
    except Exception as exc:  # noqa: BLE001
        rec.update(status="fetch_failed", reason=f"{type(exc).__name__}: {exc}"[:160])
        return rec

    if not hfq:
        rec.update(status="no_quote", reason="腾讯 hfq 无数据")
        return rec
    if anchor is None:
        rec.update(status="no_anchor", reason="腾讯不复权无数据，无法定标")
        return rec

    anchor_date, anchor_close = anchor
    hfq_map = {r[0]: r for r in hfq}
    if anchor_date not in hfq_map:
        rec.update(status="anchor_mismatch", reason=f"锚点日 {anchor_date} 不在 hfq 序列内")
        return rec
    hfq_anchor_close = float(hfq_map[anchor_date][2])
    if hfq_anchor_close <= 0:
        rec.update(status="bad_anchor", reason=f"hfq 锚点收盘 {hfq_anchor_close}")
        return rec

    scale = anchor_close / hfq_anchor_close
    lines = ["date,open,close,high,low,volume"]
    for r in hfq:
        try:
            o, c, h, low, v = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        except (IndexError, ValueError):
            continue
        lines.append(f"{r[0]},{o*scale:.4f},{c*scale:.4f},{h*scale:.4f},{low*scale:.4f},{v:.0f}")

    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines) + "\n"
    out.write_text(body, encoding="utf-8")
    rec.update(
        status="ok", n_rows=len(lines) - 1, span=[hfq[0][0], hfq[-1][0]],
        anchor_date=anchor_date, anchor_raw_close=round(anchor_close, 4),
        hfq_anchor_close=round(hfq_anchor_close, 4), scale=round(scale, 6),
        sha256=hashlib.sha256(body.encode()).hexdigest()[:16],
    )
    return rec


def collect_members(include_step75: bool) -> list[tuple[str, str]]:
    members: list[tuple[str, str]] = []
    listed = json.loads(LISTED_MANIFEST.read_text(encoding="utf-8"))
    for s in listed["symbols"]:
        if s["status"] in ("downloaded", "cached"):
            members.append((s["code"], "listed"))
    delist = json.loads(DELIST_MANIFEST.read_text(encoding="utf-8"))
    for s in delist["symbols"]:
        if s.get("delist_date") and s["delist_date"] >= "2017-01-01" and s["status"] in ("downloaded", "cached", "usable"):
            members.append((f"{s['code']}.{s['market'].upper()}", "delist"))
    if include_step75:
        for p in sorted(STEP75_DIR.glob("*.qfq.csv")):
            members.append((p.name.replace(".qfq.csv", ""), "step75"))
    # 去重（step75 与 listed 可能重叠）
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for code, sub in members:
        if code in seen:
            continue
        seen.add(code)
        uniq.append((code, sub))
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["A", "B", "step75"], default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--listed-manifest", default="",
                    help="在市组行情清单：留空=50 只抽样那份；v1.2 传 298 只全量那份")
    ap.add_argument("--manifest", default="", help="修复清单输出路径（留空=默认）")
    ap.add_argument("--codes", default="",
                    help="只处理指定的标的，逗号分隔（如 601699.SH,002165.SZ）；"
                         "给定此项时忽略清单与 --only，直接走 ad-hoc 通道，sub 一律记为 listed")
    args = ap.parse_args()

    # 换池：v1.2 的 A 组是 csi300 全量 298 只，清单不同
    global LISTED_MANIFEST, MANIFEST
    if args.listed_manifest:
        LISTED_MANIFEST = Path(args.listed_manifest)
    if args.manifest:
        MANIFEST = Path(args.manifest)

    if args.codes:
        members = [(c.strip(), "listed") for c in args.codes.split(",") if c.strip()]
        print(f"[POOL] ad-hoc 指定 {len(members)} 只：{[m[0] for m in members]}", flush=True)
    else:
        members = collect_members(include_step75=True)
    if args.only == "A" and not args.codes:
        members = [m for m in members if m[1] == "listed"]
    elif args.only == "B":
        members = [m for m in members if m[1] == "delist"]
    elif args.only == "step75":
        members = [m for m in members if m[1] == "step75"]
    if args.limit:
        members = members[: args.limit]
    print(f"[POOL] {len(members)} 只待修复", flush=True)

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(repair_one, c, s, args.force): (c, s) for c, s in members}
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            records.append(rec)
            if i % 20 == 0 or rec.get("status") not in ("ok", "cached"):
                print(f"[{i:>3}/{len(members)}] {rec['code']} {rec.get('status')} "
                      f"rows={rec.get('n_rows')} scale={rec.get('scale')}", flush=True)

    records.sort(key=lambda r: (r["group"], r["code"]))
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "把加性前复权（qfq_t = raw_t - 未来累计分红_t）换成乘性前复权（hfq_t × raw_anchor/hfq_anchor）",
        "recipe": "qfq_mult_t = hfq_t * (raw_anchor / hfq_anchor)，anchor 取两组共同的最新交易日",
        "source": {"hfq": "web.ifzq.gtimg.cn fqkline/get ... ,hfq", "raw": "web.ifzq.gtimg.cn kline/kline（仅取锚点）"},
        "range": [START, END],
        "counts": {"requested": len(records), "by_status": by_status},
        "symbols": records,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[DONE] {by_status} -> {MANIFEST.name}", flush=True)


if __name__ == "__main__":
    main()
