"""掘金仿真账户 → 按日期分文件的**只读**快照适配器（ECR-P0-SNAPSHOT-1）。

状态：代码就绪；凭证 ``DQ_GM_TOKEN`` / ``DQ_GM_ACCOUNT_ID`` 由用户本人在官网
生成，云端经 ``/opt/quant/env``（600、deepquant）由 systemd EnvironmentFile 注入。

⭐ 三条硬边界
-------------
1. **绝不发单**：只调用 ``set_token`` / ``set_endpoint`` / ``account`` /
   ``login`` / ``get_cash`` / ``get_positions``。源码里没有任何 ``order_*``。
2. **不猜协议**：Cash 没有 ``total`` 字段，``total = available + frozen``
   是显式算术；``Position.symbol`` 形如 ``SHSE.600990`` 需转 6 位代码。
3. **失败关闭**：缺凭证、非交易日、SDK 不可用、字段不认识、账户读不出来，
   一律非零退出且**不写文件**。

⭐ ECR-P0-SNAPSHOT-1 修复（跨日快照停滞）
----------------------------------------
旧实现把所有日期写进**同一个** ``account_snapshot.json``，且只要「文件存在且
内容不同」就 ``EXIT_CONFLICT=4``；因为 ``asof`` 写在内容里，跨日必然不同 ⇒
第二天起永远拒绝覆盖，SHADOW 退回 ``SELF_CHECK_ONLY``，G2 永远 0/20。

新布局（R7 2a/2b/2d/2e）：
  * 每日写入 ``account_snapshot_YYYYMMDD_rev<N>.json``；
  * ``latest.json`` 是**原子写入的指针**（``os.replace``，**不用符号链接**）；
  * SHADOW **按 day 直接解析目录**，不依赖 latest.json；
  * 冲突判定只比较**账户内容**（asof/source/cash/positions），不含采集时刻等
    元数据 ⇒ 同日重跑且账户未变 = 幂等，账户变了 = 拒绝并告警；
  * 仅显式 ``--supersede <原因>`` 才生成 rev2，并记录原因；
  * ``--migrate`` 把旧单文件**复制**成 ``..._20261109_rev1.json``，原文件
    **冻结不动**（不 mv、不删），重复执行幂等。

⚠️ 快照时点（R7 2f）：确认前**保持现有 17:10 调度**。元数据记录
``collected_at`` 与 ``settlement_verified="unknown"``；掘金沪深 18:00 结算，
将来允许报单前必须重评时点，方案交用户确认。

用法：
    python scripts/paper_account_adapter.py --probe
    python scripts/paper_account_adapter.py --date 20261110           # 写当日 rev1
    python scripts/paper_account_adapter.py --date 20261110 --no-write
    python scripts/paper_account_adapter.py --date 20261110 --supersede "结算后重取"
    python scripts/paper_account_adapter.py --migrate                  # 一次性迁移
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FWD = ROOT / "research" / "stock_pick_2026-10-25" / "forward"
OUT = FWD / "account_snapshot.json"          # 旧单文件：ECR 后**冻结**，只读留痕
CALENDAR = FWD / "trade_calendar.json"
SOURCE = "gm-paper"
SCHEMA_VERSION = "paper_snapshot_v2"
SNAPSHOT_GLOB = "account_snapshot_{day}_rev*.json"
LATEST_NAME = "latest.json"

EXIT_OK = 0
EXIT_FAIL = 2       # 失败关闭
EXIT_CONFLICT = 4   # 同日已有不同账户内容且未 --supersede

ENV_TOKEN = "DQ_GM_TOKEN"
ENV_ACCOUNT = "DQ_GM_ACCOUNT_ID"
ENV_ENDPOINT = "DQ_GM_ENDPOINT"
DEFAULT_ENDPOINT = "api.myquant.cn:9000"

CASH_AVAILABLE = "available"
CASH_FROZEN = "frozen"
POS_SYMBOL = "symbol"
POS_VOLUME = "volume"
POS_AVAILABLE = "available"
POS_VWAP = "vwap"


class AdapterError(RuntimeError):
    """快照不可用。**不写文件、非零退出**。"""


def read_credentials(env: dict[str, str] | None = None) -> dict[str, str]:
    e = os.environ if env is None else env
    missing = [k for k in (ENV_TOKEN, ENV_ACCOUNT) if not (e.get(k) or "").strip()]
    if missing:
        raise AdapterError(f"缺少环境变量：{', '.join(missing)}（不打印值）")
    return {"token": e[ENV_TOKEN].strip(),
            "account_id": e[ENV_ACCOUNT].strip(),
            "endpoint": (e.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT).strip()}


def is_trading_day(day: str, cal: Path = CALENDAR) -> bool:
    """由**独立交易日历**判定。日历不可用 ⇒ 失败关闭，不按星期几猜。"""
    try:
        days = {str(x) for x in json.loads(cal.read_text(encoding="utf-8"))["dates"]}
    except Exception as exc:
        raise AdapterError(f"交易日历不可用：{type(exc).__name__}") from exc
    if not days:
        raise AdapterError("交易日历为空：拒绝按星期几猜交易日")
    return day in days


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        if name not in obj:
            raise AdapterError(f"缺字段 {name}（实际字段：{sorted(obj)}）")
        return obj[name]
    if not hasattr(obj, name):
        names = sorted(n for n in dir(obj) if not n.startswith("_"))
        raise AdapterError(f"缺字段 {name}（实际字段：{names}）")
    return getattr(obj, name)


def normalize_symbol(sym: Any) -> str:
    s = str(sym).strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    if not (s.isdigit() and len(s) == 6):
        raise AdapterError(f"无法识别的证券代码格式：{sym!r}")
    return s


def cash_of(obj: Any) -> dict[str, float]:
    available = float(_field(obj, CASH_AVAILABLE))
    frozen = float(_field(obj, CASH_FROZEN))
    if available < 0 or frozen < 0:
        raise AdapterError(f"资金为负：available={available} frozen={frozen}")
    return {"available": available, "frozen": frozen,
            "total": available + frozen}


def positions_of(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in (items or []):
        code = normalize_symbol(_field(p, POS_SYMBOL))
        qty = int(_field(p, POS_VOLUME))
        avail = int(_field(p, POS_AVAILABLE))
        if qty <= 0:
            continue
        if avail < 0 or avail > qty:
            raise AdapterError(f"可卖数量异常：{code} available={avail} quantity={qty}")
        out.append({"code": code, "quantity": qty, "available": avail,
                    "avg_cost": float(_field(p, POS_VWAP))})
    return out


def read_account(gm: Any, creds: dict[str, str]) -> tuple[Any, Any]:
    """只做登录与查询。**没有任何下单调用**。"""
    gm.set_token(creds["token"])
    gm.set_endpoint(creds["endpoint"])
    a = gm.account(account_id=creds["account_id"], account_alias="")
    gm.login(a)
    cash = gm.get_cash()
    getter = getattr(gm, "get_positions", None) or getattr(gm, "get_position", None)
    if getter is None:
        raise AdapterError("SDK 既没有 get_positions 也没有 get_position")
    poses = getter()
    if cash is None:
        raise AdapterError("get_cash() 返回空——登录可能失败，拒绝产出快照")
    return cash, poses


def build_snapshot(day: str, cash: Any, poses: Any,
                   *, collected_at: str | None = None) -> dict[str, Any]:
    """R7 2f：元数据带采集时刻与结算核验状态（确认调度前恒为 unknown）。"""
    return {"schema_version": SCHEMA_VERSION,
            "asof": day, "source": SOURCE,
            "cash": cash_of(cash), "positions": positions_of(poses),
            "collected_at": collected_at or
                dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "settlement_verified": "unknown"}


def account_body(rec: dict[str, Any]) -> dict[str, Any]:
    """冲突/一致性比较**只看账户内容**，不含 rev / 采集时刻等元数据。"""
    return {k: rec[k] for k in ("asof", "source", "cash", "positions") if k in rec}


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_snapshot(rec: dict[str, Any], path: Path | None = None,
                   write: bool = True) -> int:
    """旧单文件语义，**仅为向后兼容既有测试保留**；运行路径不再调用本函数。

    ECR 后 ``main()`` 一律走 :func:`write_daily_snapshot`。
    """
    path = path or OUT
    body = json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not write:
        return EXIT_OK
    if path.is_file() and path.read_text(encoding="utf-8") != body:
        return EXIT_CONFLICT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return EXIT_OK


def _atomic_write(path: Path, body: str) -> None:
    """原子写入：同目录临时文件 + os.replace。**不用符号链接**。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        # mkstemp 默认 600；若迁移由 root 执行而日更链/SHADOW 以 deepquant
        # 运行，600 会让次日读不到 rev 文件（「没检出≠没问题」）。
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def daily_snapshot_paths(day: str, directory: Path = FWD) -> list[tuple[int, Path]]:
    """按 rev 号升序返回当日全部快照文件。"""
    out: list[tuple[int, Path]] = []
    for p in directory.glob(SNAPSHOT_GLOB.format(day=day)):
        m = re.search(r"_rev(\d+)\.json$", p.name)
        if m and p.is_file():
            out.append((int(m.group(1)), p))
    return sorted(out)


def write_daily_snapshot(rec: dict[str, Any], *, directory: Path = FWD,
                         supersede_reason: str | None = None
                         ) -> tuple[int, Path | None, str]:
    """R7 2a/2d/2e：跨日推进写新文件；同日内容不同拒绝；仅 supersede 生成 rev2。"""
    day = str(rec["asof"])
    try:
        existing = daily_snapshot_paths(day, directory)
        base = canonical(account_body(rec))
        if existing:
            bodies = []
            for n, p in existing:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return EXIT_FAIL, None, f"unparsable_rev{n}"
                bodies.append((n, p, canonical(account_body(d))))
            if all(b == base for _, _, b in bodies):
                # 同日重跑且账户未变 ⇒ 幂等，零写入
                return EXIT_OK, existing[-1][1], "idempotent"
            if not supersede_reason:
                print("[paper] ✗ 同日快照账户内容不同，拒绝覆盖；"
                      "需显式 --supersede <原因>（已告警）", file=sys.stderr)
                return EXIT_CONFLICT, None, "conflict"
            rev = max(n for n, _, _ in bodies) + 1
        else:
            rev = 1
        meta = dict(rec)
        meta["rev"] = rev
        if supersede_reason:
            meta["supersedes_rev"] = rev - 1
            meta["supersede_reason"] = supersede_reason
        body = json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        path = directory / f"account_snapshot_{day}_rev{rev}.json"
        _atomic_write(path, body)
        latest = {"day": day, "rev": rev, "path": path.name,
                  "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                  "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        _atomic_write(directory / LATEST_NAME,
                      json.dumps(latest, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return EXIT_OK, path, "written" if rev == 1 else "superseded"
    except OSError as exc:
        print(f"[paper] ✗ 快照写入失败：{type(exc).__name__}", file=sys.stderr)
        return EXIT_FAIL, None, "write_error"


def migrate_legacy_snapshot(*, directory: Path = FWD,
                            legacy: Path | None = None) -> dict[str, Any]:
    """R7 2b：把旧单文件**复制**成 20261109_rev1；原文件冻结不动；重复执行幂等。"""
    legacy = legacy or OUT
    if not legacy.is_file():
        return {"status": "no_legacy", "legacy_sha256": None}
    raw = legacy.read_bytes()
    legacy_sha = hashlib.sha256(raw).hexdigest()
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return {"status": "legacy_unparsable", "error": type(exc).__name__}
    day = str(d.get("asof", ""))
    if not (day.isdigit() and len(day) == 8):
        return {"status": "legacy_bad_asof", "asof": day}
    target = directory / f"account_snapshot_{day}_rev1.json"
    if target.is_file():
        try:
            cur = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"status": "migration_conflict", "error": type(exc).__name__}
        same = canonical(account_body(cur)) == canonical(account_body(d))
        return {"status": "already_migrated" if same else "migration_conflict",
                "day": day, "path": str(target),
                "legacy_sha256": legacy_sha,
                "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "legacy_unchanged": True}
    meta = dict(d)
    meta["rev"] = 1
    meta.setdefault("schema_version", SCHEMA_VERSION)
    meta.setdefault("collected_at", "unknown(legacy)")
    meta.setdefault("settlement_verified", "unknown")
    body = json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    _atomic_write(target, body)
    return {"status": "migrated", "day": day, "path": str(target),
            "legacy_sha256": legacy_sha,
            "target_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "legacy_unchanged": True}


def load_gmtrade() -> Any:
    try:
        import gmtrade.api as gm  # type: ignore
        return gm
    except ImportError as exc:
        raise AdapterError("gmtrade 未安装（pip install gmtrade）；不产出快照") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="掘金仿真账户只读快照（不发单）")
    ap.add_argument("--date", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="登录后只打印 Cash/Position 的**字段名**（不打印值）")
    ap.add_argument("--migrate", action="store_true",
                    help="一次性迁移旧单文件快照（不需要凭证，不联网）")
    ap.add_argument("--supersede", default=None, metavar="REASON",
                    help="同日账户内容变化时，显式生成 rev2 并记录原因")
    ap.add_argument("--snapshot-dir", default=None,
                    help="快照目录（默认 forward/；测试/演练可注入）")
    args = ap.parse_args(argv)
    directory = Path(args.snapshot_dir) if args.snapshot_dir else FWD

    # 迁移不需要凭证、不联网、不校验交易日（旧文件 asof 自证）
    if args.migrate:
        res = migrate_legacy_snapshot(directory=directory)
        print("[migrate] " + json.dumps(res, ensure_ascii=False))
        return EXIT_OK if res["status"] in ("migrated", "already_migrated", "no_legacy") \
            else EXIT_FAIL

    try:
        if not is_trading_day(args.date):
            raise AdapterError(f"{args.date} 不是交易日历里的交易日，拒绝产出快照")
        creds = read_credentials()
        gm = load_gmtrade()
        cash, poses = read_account(gm, creds)

        if args.probe:
            cnames = sorted(n for n in dir(cash) if not n.startswith("_")) \
                if not isinstance(cash, dict) else sorted(cash)
            plist = list(poses or [])
            pnames = (sorted(n for n in dir(plist[0]) if not n.startswith("_"))
                      if plist and not isinstance(plist[0], dict)
                      else sorted(plist[0]) if plist else [])
            print(f"[probe] Cash 字段：{cnames}")
            print(f"[probe] Position 字段：{pnames}")
            print("[probe] 只打印字段名，未打印任何值；未写快照")
            return EXIT_OK

        rec = build_snapshot(args.date, cash, poses)
        if args.no_write:
            print(f"[paper] {args.date} no-write（零副作用）；未发任何委托")
            return EXIT_OK
        rc, path, why = write_daily_snapshot(rec, directory=directory,
                                             supersede_reason=args.supersede)
        if rc != EXIT_OK:
            return rc
        c, n = rec["cash"], len(rec["positions"])
        print(f"[paper] {args.date} rev={why} file={path.name if path else '-'} "
              f"source={SOURCE} cash_avail={c['available']:.2f} "
              f"cash_frozen={c['frozen']:.2f} total={c['total']:.2f} "
              f"positions={n}｜未发任何委托")
        return EXIT_OK
    except AdapterError as exc:
        print(f"[paper] ✗ {exc}", file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:
        print(f"[paper] ✗ 未预期异常 {type(exc).__name__}；不产出快照", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
