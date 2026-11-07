"""掘金仿真账户 → ``forward/account_snapshot.json`` 的**只读**适配器。

状态：``CODE_READY_NO_CREDENTIALS`` —— 代码就绪，但没有 token / 账户 ID，
因为那两样必须由用户本人在官网注册、登录后生成（token 等同用户 ID+密码）。

⭐ 三条硬边界
-------------
1. **绝不发单**：只调用 ``set_token`` / ``set_endpoint`` / ``account`` /
   ``login`` / ``get_cash`` / ``get_positions``（或 ``get_position``）。
   源码里没有任何 ``order_*`` 调用，静态测试守着这条。
2. **不猜协议**：Cash / Position 的字段名取自掘金官方 Python SDK 文档。
   ``Cash`` **没有 ``total`` 字段**，所以 ``total = available + frozen``
   是显式算术，不是把 ``nav`` 塞进去（nav 含持仓市值，会让
   ``Cash.__post_init__`` 的「分项对不上」直接抛错）。
   ``Position.symbol`` 形如 ``SHSE.600990``，必须显式转成 6 位代码。
3. **失败关闭**：缺凭证、非交易日、SDK 不可用、字段不认识、账户状态
   读不出来，一律非零退出并**不写文件**——绝不产出一个"看起来有账户"
   的假快照。``--probe`` 可在真账号上只打印字段名（不打印值）供校准。

凭证只从环境变量读，不进命令行、不进日志、不进仓库：
``DQ_GM_TOKEN`` / ``DQ_GM_ACCOUNT_ID`` / ``DQ_GM_ENDPOINT``（可选）。
云端建议由 ``/opt/quant/env``（600、属主 deepquant）经 EnvironmentFile 注入，
**不要**复用别的任务的凭证文件。

用法：
    python scripts/paper_account_adapter.py --probe          # 字段名校准
    python scripts/paper_account_adapter.py --date 20261109  # 写当日快照
    python scripts/paper_account_adapter.py --date 20261109 --no-write
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FWD = ROOT / "research" / "stock_pick_2026-10-25" / "forward"
OUT = FWD / "account_snapshot.json"
CALENDAR = FWD / "trade_calendar.json"
SOURCE = "gm-paper"

EXIT_OK = 0
EXIT_FAIL = 2       # 失败关闭
EXIT_CONFLICT = 4   # 同日已有不同内容

ENV_TOKEN = "DQ_GM_TOKEN"
ENV_ACCOUNT = "DQ_GM_ACCOUNT_ID"
ENV_ENDPOINT = "DQ_GM_ENDPOINT"
DEFAULT_ENDPOINT = "api.myquant.cn:9000"

#: 官方文档里 Cash 的字段名；``total`` 不在其中，由算术补出。
CASH_AVAILABLE = "available"
CASH_FROZEN = "frozen"
#: Position 的字段名。``vwap`` 是加权均价，对应 AccountSnapshot.avg_cost。
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
        # 只报缺了哪个变量名，绝不打印值
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
    """对象属性或 dict 键都支持；取不到就抛错，不用默认值蒙混。"""
    if isinstance(obj, dict):
        if name not in obj:
            raise AdapterError(f"缺字段 {name}（实际字段：{sorted(obj)}）")
        return obj[name]
    if not hasattr(obj, name):
        names = sorted(n for n in dir(obj) if not n.startswith("_"))
        raise AdapterError(f"缺字段 {name}（实际字段：{names}）")
    return getattr(obj, name)


def normalize_symbol(sym: Any) -> str:
    """``SHSE.600990`` / ``SZSE.000001`` → 6 位代码。别的格式一律拒绝。"""
    s = str(sym).strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    if not (s.isdigit() and len(s) == 6):
        raise AdapterError(f"无法识别的证券代码格式：{sym!r}")
    return s


def cash_of(obj: Any) -> dict[str, float]:
    """⭐ ``total`` 不在官方 Cash 字段表里，必须由分项相加。"""
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


def build_snapshot(day: str, cash: Any, poses: Any) -> dict[str, Any]:
    return {"asof": day, "source": SOURCE,
            "cash": cash_of(cash), "positions": positions_of(poses)}


def write_snapshot(rec: dict[str, Any], path: Path | None = None,
                   write: bool = True) -> int:
    path = path or OUT
    body = json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not write:
        return EXIT_OK
    if path.is_file() and path.read_text(encoding="utf-8") != body:
        return EXIT_CONFLICT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return EXIT_OK


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
    args = ap.parse_args(argv)

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
        rc = write_snapshot(rec, write=not args.no_write)
        if rc == EXIT_CONFLICT:
            print("[paper] ✗ 当日快照已存在且内容不同，拒绝覆盖", file=sys.stderr)
            return rc
        c, n = rec["cash"], len(rec["positions"])
        print(f"[paper] {args.date} source={SOURCE} "
              f"cash_avail={c['available']:.2f} cash_frozen={c['frozen']:.2f} "
              f"total={c['total']:.2f} positions={n} "
              f"｜{'no-write' if args.no_write else 'written'}｜未发任何委托")
        return EXIT_OK
    except AdapterError as exc:
        print(f"[paper] ✗ {exc}", file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:  # 不把 SDK 的异常原始内容（可能含凭证）打出来
        print(f"[paper] ✗ 未预期异常 {type(exc).__name__}；不产出快照", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
