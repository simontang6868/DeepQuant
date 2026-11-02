#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""行情源连通性探活（云主机部署前必跑）。

## 为什么单独一个脚本

部署到新机器上，最常见的问题不是代码跑不起来，而是**这台机器的网络到不了行情源**
（DNS、出网策略、区域限制、CDN 对该机房返回 403）。这类问题在真跑数据更新时
才会暴露，而那时候已经开始写文件了。

按 R2 第一节：**逐源记录结果；不通就上报，不自行换源、不改口径。**
所以本脚本只做三件事：发一个最小请求、记录结果、**不**做任何降级。

## 判定

每个源三个状态：
- ``OK``        请求成功且能解析出预期结构
- ``DEGRADED``  请求成功但结构不符合预期（比失败更危险：它会被下游当成"没数据"）
- ``FAIL``      网络/HTTP 失败

只有全部 ``OK`` 才算通过。``DEGRADED`` 必须人工看，不能自动放过——
"返回 200 但内容变了" 是接口变更的典型表现，自动放过会静默改用空数据。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; DeepQuant-preflight/1.0)",
      "Referer": "https://finance.sina.com.cn"}
TIMEOUT = 15


def _get(url: str, headers: dict | None = None) -> tuple[int, bytes, float]:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(200_000)
        return r.status, body, time.time() - t0


def probe(name: str, url: str, parse, headers: dict | None = None) -> dict:
    """发一次请求并解析。**不重试**：重试会掩盖间歇性故障，而间歇性故障在这里等于"某天会漏数据"。"""
    rec = {"name": name, "url": url, "status": "", "http": None,
           "latency_ms": None, "detail": "", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        if url.startswith("akshare:"):
            # 非 HTTP 源（由库自带请求）：仍要计量耗时，否则"慢到超时"看不出来。
            t0 = time.time()
            ok, detail = parse(b"")
            rec["latency_ms"] = round((time.time() - t0) * 1000)
        else:
            code, body, dt = _get(url, headers)
            rec["http"] = code
            rec["latency_ms"] = round(dt * 1000)
            ok, detail = parse(body)
        rec["status"] = "OK" if ok else "DEGRADED"
        rec["detail"] = detail
    except urllib.error.HTTPError as e:
        rec["status"] = "FAIL"
        rec["http"] = e.code
        rec["detail"] = f"HTTPError {e.code}: {e.reason}"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        rec["status"] = "FAIL"
        rec["detail"] = f"{type(e).__name__}: {e}"
    return rec


# ---------------------------------------------------------------- 解析器

def _parse_tencent_kline(body: bytes) -> tuple[bool, str]:
    """腾讯日线：``{"code":0,"data":{"sh600000":{"day":[["2026-10-30",...]]}}}``"""
    try:
        j = json.loads(body)
    except Exception:
        return False, f"非 JSON（前 80 字节：{body[:80]!r}）"
    if j.get("code") != 0:
        return False, f"code={j.get('code')} msg={j.get('msg')}"
    data = j.get("data") or {}
    if not data:
        return False, "data 为空"
    node = next(iter(data.values()))
    rows = node.get("day") or node.get("qfqday") or []
    if not rows:
        return False, "无 day/qfqday 序列"
    return True, f"{len(rows)} 根，最后 {rows[-1][0]}"


def _parse_sina_kline(body: bytes) -> tuple[bool, str]:
    """新浪日线：``[{"day":"2026-10-30","open":...}, ...]``"""
    try:
        arr = json.loads(body)
    except Exception:
        return False, f"非 JSON（前 80 字节：{body[:80]!r}）"
    if not isinstance(arr, list) or not arr:
        return False, f"不是非空数组：{type(arr).__name__}"
    last = arr[-1]
    if "day" not in last:
        return False, f"缺 day 字段，键={list(last)[:6]}"
    return True, f"{len(arr)} 根，最后 {last['day']}"


def _parse_tencent_quote(body: bytes) -> tuple[bool, str]:
    """腾讯实时快照（GBK）：简称在 ``~`` 分隔的第 1 段，日期在第 30 段。"""
    txt = body.decode("gbk", "ignore")
    if "~" not in txt:
        return False, f"不是 ~ 分隔格式（前 80 字节：{txt[:80]!r}）"
    f = txt.split("~")
    if len(f) < 31:
        return False, f"字段数 {len(f)} < 31，格式可能变了"
    return True, f"name={f[1]} code={f[2]} last={f[3]} date_ts={f[30]}"


def _parse_sina_calendar(_body: bytes) -> tuple[bool, str]:
    """交易日历走 akshare（它对新浪那份压缩格式做了解码）。"""
    try:
        import akshare as ak
    except Exception as e:
        return False, f"akshare 不可用：{e}"
    try:
        df = ak.tool_trade_date_hist_sina()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if df is None or len(df) == 0:
        return False, "空表"
    col = df.columns[0]
    return True, f"{len(df)} 个交易日，{df[col].iloc[0]} → {df[col].iloc[-1]}"


SOURCES = [
    # ⚠️ 起止日期必须显式给：`param=sh600000,day,,,640` 会返回
    #    {"code":1,"msg":"bad params"}——正是首次探活踩到的坑。生产脚本
    #    (scripts/update_exec_incremental.py) 也是显式传 start/end 的。
    ("tencent_kline", "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
                      "?param=sh600000,day,{start},{end},640", _parse_tencent_kline, None),
    ("sina_kline", "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
                   "?symbol=sh600000&scale=240&ma=no&datalen=5", _parse_sina_kline, None),
    ("tencent_quote", "http://qt.gtimg.cn/q=sh600000,sz000001", _parse_tencent_quote, None),
    ("sina_calendar", "akshare:tool_trade_date_hist_sina", _parse_sina_calendar, None),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="JSON 结果路径")
    ap.add_argument("--proxy-from-env", action="store_true",
                    help="使用环境代理（默认直连；本机实测代理会让腾讯返回 RemoteDisconnected）")
    args = ap.parse_args(argv)

    if not args.proxy_from_env:
        # 默认直连：把代理变量从进程环境里摘掉，而不是"尽量不用"。
        # 保留环境代理会让结果变成"这台机器的代理通不通"，而不是"这台机器通不通"。
        import os
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                  "ALL_PROXY", "all_proxy"):
            os.environ.pop(k, None)

    host = socket.gethostname()
    # 结果放 var/：deploy/ 是"部署工具"，不该积累运行产物（否则每次部署都多几个文件进 git）。
    out = pathlib.Path(args.out or
                       f"var/preflight_{host}_{time.strftime('%Y%m%dT%H%M%S')}.json")

    results = []
    print(f"[preflight] host={host}  proxy={'env' if args.proxy_from_env else 'direct'}")
    import datetime as _dt
    today = _dt.date.today()
    start = (today - _dt.timedelta(days=10)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    for name, url, parse, headers in SOURCES:
        rec = probe(name, url.format(start=start, end=end), parse, headers)
        results.append(rec)
        mark = {"OK": "OK  ", "DEGRADED": "WARN", "FAIL": "FAIL"}[rec["status"]]
        print(f"  [{mark}] {name:16s} {rec['latency_ms'] or '-'} ms  {rec['detail']}")

    failed = [r for r in results if r["status"] != "OK"]
    verdict = "PASS" if not failed else "BLOCKED"
    payload = {
        "host": host,
        "verdict": verdict,
        "proxy_mode": "env" if args.proxy_from_env else "direct",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sources": results,
        "_note": ("R2 第一节：不通就上报，不自行换源或改口径。"
                  "DEGRADED 表示能连通但结构变了，必须人工确认。"),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"[preflight] {verdict}｜已写入 {out}")
    if failed:
        print("[preflight] 未通过的源：" + ", ".join(f"{r['name']}={r['status']}" for r in failed))
        print("[preflight] 处置：报告给用户，等待指示。**不要**自动换源。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
