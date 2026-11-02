#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SP-4 Amendment A2：实盘安全过滤的**当前状态快照**与三账本对比。

任务书 §5 Phase 1.5 原文：

    安全过滤（风控，不是 alpha 规则）：实盘剔除 ST/*ST/退市整理期/停牌/非授权板块，
    使用当前状态。同样走 amendment，并在前瞻中并列记录"带过滤 / 不带过滤"两本账，
    衡量其影响。

⚠️ 一个必须写在前面的澄清
--------------------------
Phase 0 与 PROGRESS 把 1.5 记成「沪市无历史 ST 真值（SH 0/2401）是硬缺口」。
**这是把「使用当前状态」误读成了「重建历史」。**

实盘风控要回答的是「今天这只票能不能买」，不是「2019 年 3 月它是不是 ST」。
历史 ST 表是**回测侧**需求。本脚本因此**不做**历史重建，只抓一次当前快照。
历史 ST 缺口依旧存在，本脚本不声称已解决它。

数据源为什么是腾讯而不是 akshare / 东财
--------------------------------------
2026-11-02 实测：`ak.stock_zh_a_spot_em()` 在代理与直连两种模式下均
`ConnectionError`（东财分页接口被拒）。而腾讯 `qt.gtimg.cn` 在本项目 1.4 增量更新里
已被验证可用，且**返回股票简称**——简称正是 ST / 退市标记的原始载体（交易所实施
风险警示或进入退市整理期时，直接改简称）。

⇒ 判定不依赖任何第三方「ST 名单」，直接读交易所挂在简称上的标记。

标签全部是纯函数
----------------
`board_of(code)` / `classify_name(name)` / `judge_no_quote(quote_ts, asof)` 三个函数
给定输入即可复算，不依赖顺序、不依赖网络重试、不依赖 pandas 的排序稳定性。
`tests/test_universe_filter.py` 逐条钉住它们。

用法
----
    python scripts/sp5/p17_universe_filter.py --fetch          # 抓快照并落盘
    python scripts/sp5/p17_universe_filter.py --report --asof 20261030   # 三账本对比
    python scripts/sp5/p17_universe_filter.py --fetch --report --asof 20261030

输出
----
`forward/universe_state/<YYYYMMDD>.json` —— 快照（全量逐只标签 + 元数据 + 自校验哈希）
`forward/universe_state/<YYYYMMDD>.csv`  —— 同上，长表（便于人查）
`forward/corrections.jsonl`             —— 同日重抓时追加一条（不静默替换）
`sp5_out/p17_universe_filter.json`      —— 三账本对比结果
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BASE = pathlib.Path(__file__).resolve().parents[2]          # research/stock_pick_2026-10-25
STATE_DIR = BASE / "forward" / "universe_state"
CORRECTIONS = BASE / "forward" / "corrections.jsonl"
OUT = BASE / "sp5_out"
QFQ = BASE.parent.parent / "data" / "qfq_all"

QUOTE_URL = "http://qt.gtimg.cn/q={symbols}"
BATCH = 60
TIMEOUT = 25

#: 连续竞价开始时刻。中文行情接口对「当日无成交」的股票返回 09:00:00 占位，
#: 集合竞价撮合在 09:15~09:25，连续竞价从 09:30 起。取 09:30:00 为界。
CONTINUOUS_START = "093000"

#: 收盘后判定。早于此时间抓取时，「停牌」判据不可靠（正常股票可能还没成交）。
CLOSE_READY = 150500
#: 交易日有效报价股票的最小占比。低于此值判「今天不是交易日」并拒绝落盘。
MIN_QUOTED_RATIO = 0.30


# ---------------------------------------------------------------------------
# 纯函数：板块
# ---------------------------------------------------------------------------
_BOARD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("600", "601", "603", "605"), "SH_MAIN"),
    (("688", "689"), "STAR"),
    (("000", "001", "002", "003"), "SZ_MAIN"),
    (("300", "301", "302"), "GEM"),
    (("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839",
      "870", "871", "872", "873", "874", "875", "876", "877", "878", "879",
      "920"), "BSE"),
    (("200", "201"), "SZ_B"),
    (("900",), "SH_B"),
)

#: D4 授权宇宙。创业板单列，因为**用户权限待确认**（D4 未决），不能直接归入可交易。
BOARD_D4_STRICT = frozenset({"SH_MAIN", "SZ_MAIN"})
BOARD_D4_WITH_GEM = frozenset({"SH_MAIN", "SZ_MAIN", "GEM"})

#: 明确的非股票代码（指数、ETF 等）不应出现在 qfq_all 里；出现即为异常，要告警。
BOARD_UNKNOWN = "UNKNOWN"


def board_of(code: str) -> str:
    """由 6 位代码前缀判定板块。**纯函数**。

    ⚠️ 不写 `code[0] in "48"` 这类宽规则：会把 400xxx（老三板）与 8 开头的
    非北交所代码一起吞掉，且一旦某天出现新前缀就静默误判。逐个前缀列出来，
    未知就返回 UNKNOWN 让调用方告警——「没检出」不能当成「没问题」。
    """
    c = str(code).strip()
    if len(c) != 6 or not c.isdigit():
        return BOARD_UNKNOWN
    for prefixes, board in _BOARD_RULES:
        if c[:3] in prefixes:
            return board
    return BOARD_UNKNOWN


# ---------------------------------------------------------------------------
# 纯函数：ST / 退市整理
# ---------------------------------------------------------------------------
def classify_name(name: str) -> dict:
    """由股票简称判定风险警示与退市整理。**纯函数**。

    两个标签**独立**，不是互斥：
    退市整理期股票可能是 `*STXX退`（既带风险警示又处于整理期）。
    用「异或」会把这类同时命中的股票漏掉一类。

    `is_st` 用大写 `ST` 子串判定：沪深两所的风险警示标识统一写作 `ST` / `*ST`
    （历史上还有 `SST` / `S*ST`，同样命中）。中文简称里不会自然出现拉丁字母
    `ST` 连写，误判风险极低；测试里仍放了反例钉住这一点。

    `is_delisting` 用 `退`：沪市整理期冠 `退市`，深市为 `XX退`。
    ⚠️ 它同时会命中**已退市**但仍然留在 `qfq_all` 里的老代码——两者语义不同
    （已退市 ≠ 正在退市整理期），报告里必须分开计数。
    """
    s = str(name or "")
    return {
        "is_st": "ST" in s.upper(),
        "is_delisting": "退" in s,
    }


# ---------------------------------------------------------------------------
# 纯函数：停牌 / 无报价
# ---------------------------------------------------------------------------
def judge_no_quote(quote_ts: str, asof: str) -> bool:
    """当日是否**没有**连续竞价成交。**纯函数**。

    两种都必须判 True，只判一种会漏：

    1. 时间戳的**日期**不是 `asof` —— 已退市/长期停牌的代码，腾讯会把最后一次
       报价的时间戳原样返回（实测 000004 国华退 返回 `20261102090000`，
       而某些更老的代码返回退市前日期）；
    2. 时间戳的**时刻** <= 09:30:00 —— 当日无连续竞价成交（停牌，或已退市占位）。

    ⚠️ `asof` 必须是**交易日**。若不是，全市场都会落在判据 2 上，
    于是「停牌」这一列会凭空变成 100%。调用方必须先过 `is_trading_day` 检查。
    """
    ts = str(quote_ts or "").strip()
    if len(ts) < 14 or not ts[:14].isdigit():
        return True
    if ts[:8] != str(asof):
        return True
    return ts[8:14] <= CONTINUOUS_START


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def to_symbol(code: str) -> str:
    """6 位代码 → 带市场前缀的行情符号。北交所用 `bj`。"""
    c = str(code).strip()
    b = board_of(c)
    if b in ("SH_MAIN", "STAR", "SH_B"):
        return "sh" + c
    if b == "BSE":
        return "bj" + c
    return "sz" + c


#: 显式绕过环境代理的 opener。2026-11-02 实测本机 `HTTP(S)_PROXY` 指向的代理对
#: `qt.gtimg.cn` 返回 `RemoteDisconnected`，而直连正常。行情抓取是只读的公开接口，
#: 直连优先；只有直连失败才退回带代理的默认 opener。
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    try:
        return _DIRECT_OPENER.open(req, timeout=TIMEOUT).read()
    except Exception:                                    # noqa: BLE001
        return urllib.request.urlopen(req, timeout=TIMEOUT).read()


def _parse_quote_line(line: str) -> dict | None:
    """解析腾讯单行返回。返回 None 表示这行不构成有效报价。"""
    if "~" not in line or "=" not in line:
        return None
    body = line.split("=", 1)[1].strip().strip('"')
    f = body.split("~")
    if len(f) < 32:
        return None
    code = f[2].strip()
    if len(code) != 6 or not code.isdigit():
        return None
    return {
        "code": code,
        "name": f[1].strip(),
        "last_price": _to_float(f[3]),
        "quote_ts": f[30].strip(),
    }


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def fetch_state(codes: list[str], asof: str | None = None,
                sleep_s: float = 0.05) -> dict:
    """批量拉取当前快照。返回 `{code: 原始报价}`。

    ⚠️ 失败策略：单批失败**重试一次**，仍失败就记录在 `failed_batches` 里，
    绝不把「没拉到」当成「不存在」。缺失的代码在后续判定里标 `no_data=True`，
    并在报告里显式计数——这类代码**不能**默认当成可交易。
    """
    got: dict[str, dict] = {}
    failed: list[dict] = []
    asof = asof or time.strftime("%Y%m%d")
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        url = QUOTE_URL.format(symbols=",".join(to_symbol(c) for c in batch))
        raw = None
        for attempt in (1, 2):
            try:
                raw = _http_get(url).decode("gbk", "ignore")
                break
            except Exception as exc:                      # noqa: BLE001
                if attempt == 2:
                    failed.append({"start": i, "n": len(batch), "error": repr(exc)[:200]})
        if raw is None:
            continue
        for line in raw.strip().split(";"):
            rec = _parse_quote_line(line)
            if rec is not None:
                if asof and len(rec["quote_ts"]) >= 8 and rec["quote_ts"][:8] != asof:
                    pass   # 保留原始时间戳，交由 judge_no_quote 判定
                got[rec["code"]] = rec
        if sleep_s:
            time.sleep(sleep_s)
    return {"quotes": got, "failed_batches": failed, "asof": asof}


# ---------------------------------------------------------------------------
# 快照构建 / 落盘
# ---------------------------------------------------------------------------
def build_rows(codes: list[str], quotes: dict[str, dict], asof: str) -> list[dict]:
    rows = []
    for c in codes:
        q = quotes.get(c)
        if q is None:
            # 拉不到 ≠ 可交易。默认不可交易，并显式标 no_data。
            rows.append({
                "code": c, "name": "", "last_price": None, "quote_ts": "",
                "board": board_of(c), "is_st": False, "is_delisting": False,
                "no_quote_today": True, "no_data": True,
            })
            continue
        labels = classify_name(q["name"])
        rows.append({
            "code": c,
            "name": q["name"],
            "last_price": q["last_price"],
            "quote_ts": q["quote_ts"],
            "board": board_of(c),
            "is_st": labels["is_st"],
            "is_delisting": labels["is_delisting"],
            "no_quote_today": judge_no_quote(q["quote_ts"], asof),
            "no_data": False,
        })
    return rows


def summarize(rows: list[dict], asof: str) -> dict:
    n = len(rows)
    boards = collections.Counter(r["board"] for r in rows)
    n_quoted = sum(1 for r in rows if not r["no_quote_today"])
    return {
        "asof": asof,
        "n_total": n,
        "n_quoted": n_quoted,
        "n_no_quote": n - n_quoted,
        "quoted_ratio": round(n_quoted / n, 6) if n else 0.0,
        "n_no_data": sum(1 for r in rows if r["no_data"]),
        "n_is_st": sum(1 for r in rows if r["is_st"]),
        "n_is_delisting": sum(1 for r in rows if r["is_delisting"]),
        "n_unknown_board": boards.get(BOARD_UNKNOWN, 0),
        "board_counts": dict(sorted(boards.items())),
    }


def is_trading_day(summary: dict) -> tuple[bool, str]:
    """快照是否可用作停牌判定。

    ⚠️ 这是本脚本最容易被绕过的一道闸门。休市日抓取时，全市场时间戳都是 09:00，
    若直接按 `judge_no_quote` 判定，会把 100% 的股票标成「停牌」，
    然后报告里出现「全市场停牌」这种荒谬结论却看不出是数据问题。
    """
    if summary["n_total"] == 0:
        return False, "快照为空"
    if summary["n_no_data"] / summary["n_total"] > 0.05:
        return False, f"缺失代码占比 {summary['n_no_data'] / summary['n_total']:.1%} > 5%"
    if summary["quoted_ratio"] < MIN_QUOTED_RATIO:
        return False, (f"有连续竞价成交的股票仅 {summary['quoted_ratio']:.1%}"
                       f"（下限 {MIN_QUOTED_RATIO:.0%}）⇒ 今天很可能不是交易日，"
                       "拒绝把整表当成「停牌」")
    return True, "ok"


def state_paths(asof: str) -> tuple[pathlib.Path, pathlib.Path]:
    return STATE_DIR / f"{asof}.json", STATE_DIR / f"{asof}.csv"


def _sha_of_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        {k: v for k, v in payload.items() if k != "sha256"},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _append_correction(rec: dict) -> None:
    CORRECTIONS.parent.mkdir(parents=True, exist_ok=True)
    with CORRECTIONS.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def write_state(rows: list[dict], summary: dict, meta: dict) -> pathlib.Path:
    """落盘快照。同日重抓**先备份旧件**再写，并追加更正日志（不静默替换）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    jp, cp = state_paths(summary["asof"])
    backups: list[str] = []
    if jp.exists():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for p in (jp, cp):
            if p.exists():
                b = p.with_name(p.name + f".bak.{stamp}")
                p.replace(b)
                backups.append(b.name)
        _append_correction({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "UNIVERSE_STATE_REWRITTEN",
            "asof": summary["asof"],
            "backups": backups,
            "reason": "同日重抓快照；旧件已备份，未删除",
        })
    payload = {**meta, **summary, "stocks": {r["code"]: r for r in rows}}
    payload["sha256"] = _sha_of_payload(payload)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8", newline="\n")
    pd.DataFrame(rows).to_csv(cp, index=False, encoding="utf-8-sig")
    return jp


def load_state(asof: str) -> dict | None:
    jp, _ = state_paths(asof)
    if not jp.exists():
        return None
    st = json.loads(jp.read_text(encoding="utf-8"))
    claimed = st.get("sha256")
    if claimed and claimed != _sha_of_payload(st):
        raise RuntimeError(
            f"{jp} 自校验失败：声明 {str(claimed)[:12]} vs 实算 {_sha_of_payload(st)[:12]}。"
            "快照被改动过就不能用——先确认是谁改的。")
    return st


# ---------------------------------------------------------------------------
# 三账本对比
# ---------------------------------------------------------------------------
def tradable_mask(rows: list[dict], with_gem: bool) -> dict[str, bool]:
    boards = BOARD_D4_WITH_GEM if with_gem else BOARD_D4_STRICT
    out = {}
    for r in rows:
        out[r["code"]] = (
            r["board"] in boards
            and not r["is_st"]
            and not r["is_delisting"]
            and not r["no_quote_today"]
        )
    return out


def report(asof: str, state: dict) -> dict:
    """在 asof 上算三账本。用 `p13_forward` 的 build_snapshot + pick（前瞻实际跑的那套）。"""
    import p13_forward as p13

    rows = list(state["stocks"].values())
    mask_strict = tradable_mask(rows, with_gem=False)
    mask_gem = tradable_mask(rows, with_gem=True)

    raw = p13.build_snapshot(asof)
    base = raw.attrs["base_filter"]

    def _pick_with(mask: dict[str, bool] | None):
        s = raw.copy()
        ok = base & raw["close"].between(1.0, p13.CLOSE_HI_MAIN)
        if mask is not None:
            allow = raw["code"].map(lambda c: bool(mask.get(c, False)))
            ok = ok & allow
        s["ok_buy"] = ok
        return p13.pick(s)

    pool_u, top_u, rand_u = _pick_with(None)
    pool_s, top_s, _ = _pick_with(mask_strict)
    pool_g, top_g, _ = _pick_with(mask_gem)

    def _detail(df):
        return [{"code": r["code"], "name": state["stocks"].get(r["code"], {}).get("name", ""),
                 "close": round(float(r["close"]), 4),
                 "amt20": round(float(r["amt20"]), 1),
                 "board": state["stocks"].get(r["code"], {}).get("board", "")}
                for _, r in df.iterrows()]

    def _inter(a, b):
        sa, sb = set(a), set(b)
        return len(sa & sb), (len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0)

    top_u_l = list(top_u["code"])
    res = {
        "asof": asof,
        "universe_state_asof": state["asof"],
        "baseline_ok_buy": int(base.sum()),
        "accounts": {
            "unfiltered": {"ok_buy": int((base & raw["close"].between(1.0, p13.CLOSE_HI_MAIN)).sum()),
                           "pool": len(pool_u), "targets": _detail(top_u),
                           "rand5": list(rand_u["code"])},
            "d4_strict": {"ok_buy": int((base & raw["close"].between(1.0, p13.CLOSE_HI_MAIN)
                                         & raw["code"].map(lambda c: bool(mask_strict.get(c, False)))).sum()),
                          "pool": len(pool_s), "targets": _detail(top_s)},
            "d4_with_gem": {"ok_buy": int((base & raw["close"].between(1.0, p13.CLOSE_HI_MAIN)
                                           & raw["code"].map(lambda c: bool(mask_gem.get(c, False)))).sum()),
                            "pool": len(pool_g), "targets": _detail(top_g)},
        },
        "overlap": {
            "unfiltered_vs_d4_strict_top5": _inter(top_u_l, list(top_s["code"]))[0],
            "unfiltered_vs_d4_with_gem_top5": _inter(top_u_l, list(top_g["code"]))[0],
            "unfiltered_vs_d4_strict_pool_jaccard": round(_inter(pool_u["code"], pool_s["code"])[1], 6),
            "unfiltered_vs_d4_with_gem_pool_jaccard": round(_inter(pool_u["code"], pool_g["code"])[1], 6),
        },
    }
    # 单项排除的贡献：逐个标签单独叠加到「基线 ok_buy」上，看各自砍掉多少
    contrib = {}
    for key, fn in (
        ("board_only_strict", lambda r: r["board"] in BOARD_D4_STRICT),
        ("board_only_with_gem", lambda r: r["board"] in BOARD_D4_WITH_GEM),
        ("not_st", lambda r: not r["is_st"]),
        ("not_delisting", lambda r: not r["is_delisting"]),
        ("has_quote", lambda r: not r["no_quote_today"]),
    ):
        allow = {r["code"]: fn(r) for r in rows}
        ok = base & raw["close"].between(1.0, p13.CLOSE_HI_MAIN) & raw["code"].map(
            lambda c: bool(allow.get(c, False)))
        contrib[key] = {"ok_buy": int(ok.sum()),
                        "dropped": int((base & raw["close"].between(1.0, p13.CLOSE_HI_MAIN)).sum() - ok.sum())}
    res["single_factor_contribution"] = contrib
    return res


# ---------------------------------------------------------------------------
def list_codes() -> list[str]:
    import glob
    return sorted({os.path.basename(p).split(".")[0]
                   for p in glob.glob(os.path.join(QFQ, "*.qfq.csv"))})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="抓取当前快照并落盘")
    ap.add_argument("--report", action="store_true", help="用快照算三账本")
    ap.add_argument("--asof", default=None, help="决策日 YYYYMMDD（report 用）；快照日默认今天")
    ap.add_argument("--force-intraday", action="store_true",
                    help="允许盘中抓取（会记 captured_intraday=true，停牌判据不可靠）")
    args = ap.parse_args(argv)

    if not (args.fetch or args.report):
        ap.print_help()
        return 2

    asof_state = time.strftime("%Y%m%d")

    if args.fetch:
        hhmmss = time.strftime("%H%M%S")
        intraday = hhmmss < str(CLOSE_READY)
        if intraday and not args.force_intraday:
            print(f"[abort] 当前 {hhmmss} 早于 {CLOSE_READY}，未收盘 ⇒ 「当日无成交」判据不可靠。"
                  "\n        收盘后重跑；确需盘中抓取请加 --force-intraday（快照会标记 captured_intraday）。")
            return 4
        codes = list_codes()
        print(f"[fetch] {len(codes):,} 只 ← {QUOTE_URL}")
        t0 = time.time()
        got = fetch_state(codes, asof=asof_state)
        print(f"[fetch] 命中 {len(got['quotes']):,} 只｜失败批次 {len(got['failed_batches'])}"
              f"｜耗时 {time.time() - t0:.1f}s")
        rows = build_rows(codes, got["quotes"], asof_state)
        summary = summarize(rows, asof_state)
        ok, why = is_trading_day(summary)
        print(f"[check] 有报价 {summary['n_quoted']:,}/{summary['n_total']:,}"
              f"（{summary['quoted_ratio']:.1%}）｜{why}")
        if not ok:
            print("[abort] 快照不可用作停牌判定，拒绝落盘。")
            return 5
        meta = {
            "fetch_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "captured_intraday": bool(intraday),
            "no_quote_reliable": not intraday,
            "source": "qt.gtimg.cn",
            "source_url_pattern": QUOTE_URL,
            "failed_batches": got["failed_batches"],
            "code_source": str(QFQ).replace("\\", "/"),
            "labels": {
                "is_st": "简称含 'ST'（含 *ST / SST / S*ST）",
                "is_delisting": "简称含 '退'（沪：退市XX；深：XX退）",
                "no_quote_today": f"当日最后成交时间日期 != asof，或时刻 <= {CONTINUOUS_START}",
                "board": "代码前缀，见 BOARD_RULES",
            },
        }
        jp = write_state(rows, summary, meta)
        print(f"[state] {jp}  sha256={_sha_of_payload(json.loads(jp.read_text(encoding='utf-8')))[:16]}")
        for k in ("n_is_st", "n_is_delisting", "n_no_quote", "n_no_data", "n_unknown_board"):
            print(f"        {k:18s} {summary[k]:>6,}")
        print(f"        board_counts       {summary['board_counts']}")

    if args.report:
        if not args.asof:
            print("[abort] --report 需要 --asof（决策日）")
            return 2
        state = load_state(asof_state)
        if state is None:
            print(f"[abort] 缺快照 {state_paths(asof_state)[0]}，先跑 --fetch")
            return 6
        res = report(args.asof, state)
        OUT.mkdir(parents=True, exist_ok=True)
        op = OUT / "p17_universe_filter.json"
        op.write_text(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
        print(json.dumps({k: v for k, v in res.items() if k != "accounts"},
                         ensure_ascii=False, indent=2))
        for name, acc in res["accounts"].items():
            print(f"[{name}] pool {acc['pool']}｜ok_buy {acc['ok_buy']}｜"
                  f"top5 {[t['code'] for t in acc['targets']]}")
        print(f"[out] {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
