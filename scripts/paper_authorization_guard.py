#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仿真报单授权校验器（PA-GUARD-1，预登记 1e48091a）。

**纯判定**：无网络、无下单调用、不读凭证、不创建/修改任何授权文件。
每次报单前调用；任一校验失败即 ``DENY``（停机），不重试、不降级。

文件缺失/不可读/非 JSON/哈希缺失/时间不可解析 ⇒ ``DENY:NO_AUTH``，
不是「默认允许」也不是 INFO。输出只有原因码与布尔表，**不含凭证值与文件内容**。

用法：
  python scripts/paper_authorization_guard.py --compute-hash --file PATH
  python scripts/paper_authorization_guard.py --check --file PATH \
      --action sim_rebalance_order --notional 4000 --cumulative 0 \
      --available 100000 --now 2026-12-01T09:35:00+08:00
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_auth_v1"
ONLY_CHANNEL = "gm-paper"
#: 声明 ⑤ 的四类动作，冻结为枚举；未列一律拒绝
FROZEN_SCOPES = frozenset({
    "query_cash", "query_positions", "query_execution_reports",
    "sim_rebalance_order",
})
HEX64 = re.compile(r"^[0-9a-f]{64}$")

ALLOW, DENY = "ALLOW", "DENY"
NO_AUTH = "NO_AUTH"


def canonical_bytes(body: dict[str, Any]) -> bytes:
    """规范化字节（预登记第三节口径，冻结，不得随意改分隔符）。"""
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def compute_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def _result(decision: str, reason: str | None = None,
            checks: dict[str, bool] | None = None) -> dict[str, Any]:
    return {"decision": decision, "reason": reason,
            "checks": checks or {}, "schema_version": SCHEMA_VERSION}


def parse_now(value: str) -> dt.datetime:
    """时间必须带时区；不带时区一律失败关闭，不按本地时区猜。"""
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("naive datetime")
    return t


def check(*, doc: dict[str, Any] | None, now: str, action: str,
          notional: float, cumulative: float, available: float) -> dict[str, Any]:
    """六项校验。``doc`` 为已解析的授权文件对象；None 表示文件缺失/不可解析。"""
    # ---- 失败关闭：结构与环境 ----
    if not isinstance(doc, dict):
        return _result(DENY, NO_AUTH)
    for key in ("schema_version", "channel", "max_notional", "not_after",
                "scopes", "account_ref", "statement_ref", "sha256"):
        if key not in doc:
            return _result(DENY, NO_AUTH)
    if not isinstance(doc.get("sha256"), str) or not HEX64.match(doc["sha256"]):
        return _result(DENY, NO_AUTH)
    try:
        now_t = parse_now(now)
        until_t = parse_now(str(doc["not_after"]))
    except (ValueError, TypeError, AttributeError):
        return _result(DENY, NO_AUTH)
    try:
        cap = float(doc["max_notional"])
        n = float(notional)
        cum = float(cumulative)
        avail = float(available)
    except (ValueError, TypeError):
        return _result(DENY, NO_AUTH)
    if not (cap > 0 and avail > 0 and n > 0 and cum >= 0):
        return _result(DENY, NO_AUTH)

    scopes = doc.get("scopes")
    if not isinstance(scopes, (list, tuple, set)) or not scopes:
        return _result(DENY, NO_AUTH)

    # ---- 六项 ----
    c1 = doc["schema_version"] == SCHEMA_VERSION
    c2 = doc["channel"] == ONLY_CHANNEL
    c3 = now_t <= until_t
    c4 = (action in FROZEN_SCOPES and action in scopes
          and all(s in FROZEN_SCOPES for s in scopes))
    c5 = (cap <= avail and n <= cap and cum + n <= cap)
    body = {k: v for k, v in doc.items() if k != "sha256"}
    c6 = hmac.compare_digest(compute_hash(body), doc["sha256"])

    checks = {"schema": c1, "channel": c2, "expiry": c3,
              "scope": c4, "notional": c5, "hash": c6}
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        # 判据优先级固定，便于对账与变异定位
        order = ("schema", "channel", "expiry", "scope", "notional", "hash")
        first = next(k for k in order if k in failed)
        return _result(DENY, first.upper(), checks)
    return _result(ALLOW, None, checks)


def load_doc(path: Path) -> dict[str, Any] | None:
    """读文件；任何异常一律返回 None（失败关闭）。不打印内容。"""
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            return None
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="仿真报单授权校验（只读，不发单）")
    ap.add_argument("--file", required=True, help="授权文件路径")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--compute-hash", action="store_true",
                      help="按冻结口径算 sha256，供本人填写文件时使用")
    mode.add_argument("--check", action="store_true", help="执行六项校验")
    ap.add_argument("--action", default="")
    ap.add_argument("--notional", type=float, default=0.0)
    ap.add_argument("--cumulative", type=float, default=0.0)
    ap.add_argument("--available", type=float, default=0.0)
    ap.add_argument("--now", default="")
    args = ap.parse_args(argv)

    doc = load_doc(Path(args.file))
    if args.compute_hash:
        if doc is None:
            print("[guard] ✗ 文件缺失或不可解析 ⇒ NO_AUTH", file=sys.stderr)
            return 2
        body = {k: v for k, v in doc.items() if k != "sha256"}
        print(compute_hash(body))
        return 0

    if not args.now:
        print("[guard] ✗ --check 必须显式注入 --now（不读系统时钟）",
              file=sys.stderr)
        return 2
    rec = check(doc=doc, now=args.now, action=args.action,
                notional=args.notional, cumulative=args.cumulative,
                available=args.available)
    print(f"[guard] {rec['decision']}"
          f"{'｜原因 ' + rec['reason'] if rec['reason'] else ''}"
          f"｜六项 {sum(rec['checks'].values())}/6 通过｜未发单")
    return 0 if rec["decision"] == ALLOW else 1


if __name__ == "__main__":
    sys.exit(main())
