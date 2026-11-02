"""交易所交易日历（独立于行情数据）。

## 为什么需要这个模块

原实现用「qfq_all 文件里的日期并集」当日历，并且只抽样前 40 个文件
（``fast_calendar(sample=40)``）。这带来两个**静默**后果：

1. **样本被停牌钉死**：抽样对象若在月末停牌，并集的最大日期就停在更早的一天。
   函数看到「数据末日 = 10-29」会照常返回，于是一个**非月末日**被当成月末决策日。
2. **缺数据与假日不可区分**：数据更新少拉了一天时，并集同样停在 10-29，
   表现与上面完全一样，但原因完全不同、处置也完全不同。

两者的共同点是：**错误不会报错，只会让决策日悄悄提前一天**，而月度信号的
日期一旦错一天，"月末"这个前提就不成立了。

## 做法

把「哪一天是交易日」和「哪一天有数据」拆成两件事：

- 交易日历来自**独立源**（新浪 ``tool_trade_date_hist_sina``，akshare 封装），
  缓存到 ``forward/trade_calendar.json``；
- 数据末日由全池尾部扫描得到（见 ``p13_forward.data_last_date``）；
- 两者交叉校验：**数据末日必须等于日历上的当月最后一个交易日**，否则拒绝。

日历拿不到时**失败关闭**（``CalendarUnavailable``），不退回「按周一至周五猜」：
猜出来的月末在长假月份必错，而错的那一次恰好会是春节。

⚠️ 本模块**不判断某天有没有数据**，只回答"交易所开不开市"。混在一起又会
回到原来那个分不清的情形。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import sys

#: 缓存位置。放在 ``forward/`` 下与配置、台账同级，随 research 一起入库。
DEFAULT_PATH = (pathlib.Path(__file__).resolve().parents[2]
                / "forward" / "trade_calendar.json")

#: 日历允许的陈旧上限（自然日）。超过则视为不可用——宁可不生成决策，
#: 也不要用一份可能缺了新年假日的日历去判月末。
MAX_CALENDAR_AGE_DAYS = 400

SOURCE = "sina:tool_trade_date_hist_sina"


class CalendarUnavailable(RuntimeError):
    """没有可用的交易日历。**失败关闭**，不猜。"""


class CalendarNotCovered(CalendarUnavailable):
    """日历没有覆盖到需要判断的那个月（被截断在月中）。"""


def _norm(d: str) -> str:
    return d.replace("-", "").replace("/", "")[:8]


def _parse(d: str) -> dt.date:
    d = _norm(d)
    return dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))


def _fmt(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def month_bounds(ym: str) -> tuple[dt.date, dt.date]:
    """返回该月的首日与末日（自然日，不含节假日概念）。"""
    y, m = int(ym[:4]), int(ym[4:6])
    first = dt.date(y, m, 1)
    last = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
    return first, last


def fetch(timeout: int = 30) -> list[str]:
    """从新浪拉取全量交易日历（通过 akshare）。返回 YYYYMMDD 升序列表。"""
    try:
        import akshare as ak  # 延迟导入：本模块的纯函数不该依赖网络库
    except Exception as e:  # pragma: no cover - 环境问题
        raise CalendarUnavailable(f"akshare 不可用：{e}") from e
    try:
        df = ak.tool_trade_date_hist_sina()
    except Exception as e:
        raise CalendarUnavailable(f"拉取交易日历失败：{type(e).__name__}: {e}") from e
    if df is None or len(df) == 0:
        raise CalendarUnavailable("交易日历返回空表")
    col = df.columns[0]
    days = sorted({_norm(str(x)) for x in df[col].tolist() if _norm(str(x)).isdigit()
                   and len(_norm(str(x))) == 8})
    if len(days) < 1000:
        # 少于 1000 个交易日（≈4 年）不可能是全量日历，多半是接口换了格式。
        raise CalendarUnavailable(f"交易日历条目异常少（{len(days)} 条），判为不可用")
    return days


def save(days: list[str], path: pathlib.Path | None = None,
         source: str = SOURCE) -> pathlib.Path:
    """落盘缓存。**必须带 sha256 与抓取时间**——否则以后无法判断这份日历是哪天的。"""
    p = pathlib.Path(path or DEFAULT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    days = sorted({_norm(d) for d in days})
    payload = {
        "source": source,
        "fetch_ts": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "n": len(days),
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "sha256_of_dates": hashlib.sha256(
            "\n".join(days).encode("ascii")).hexdigest(),
        "dates": days,
        "_note": "交易日历缓存。sha256_of_dates 只覆盖 dates 字段，便于逐位复核。",
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8", newline="\n")
    return p


def load(path: pathlib.Path | None = None, *,
         today: str | None = None,
         max_age_days: int = MAX_CALENDAR_AGE_DAYS) -> list[str]:
    """读缓存并**校验自洽性**：条目数、首尾顺序、sha256、陈旧度。

    这里的四项校验都不是形式主义——文件被手工编辑过一次就会露馅，
    而一份被编辑过的日历会让月末判断无声地错。
    """
    p = pathlib.Path(path or DEFAULT_PATH)
    if not p.exists():
        raise CalendarUnavailable(f"交易日历缓存不存在：{p}（先跑 --refresh-calendar）")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise CalendarUnavailable(f"交易日历缓存不可解析：{e}") from e
    days = payload.get("dates") or []
    if len(days) != payload.get("n"):
        raise CalendarUnavailable(
            f"交易日历条目数与 n 不符（{len(days)} vs {payload.get('n')}），文件可能被改过")
    dig = hashlib.sha256("\n".join(days).encode("ascii")).hexdigest()
    if dig != payload.get("sha256_of_dates"):
        raise CalendarUnavailable("交易日历 sha256 不符，文件可能被改过")
    if days != sorted(days):
        raise CalendarUnavailable("交易日历未按升序排列，判为不可用")
    # ⚠️ 取日期必须按 `YYYY-MM-DD` 全取（前 10 位）再去横线：
    #    写成 `[:8]` 会得到 "2026-11-0"，去掉横线只剩 7 位，`_parse` 直接抛
    #    ValueError——而这个异常发生在"读日历"里，看起来像日历坏了。
    raw_ts = payload.get("fetch_ts") or ""
    fetched = _parse(raw_ts[:10]) if len(raw_ts) >= 10 else None
    if fetched is not None and today:
        age = (_parse(today) - fetched).days
        if age > max_age_days:
            raise CalendarUnavailable(
                f"交易日历已陈旧 {age} 天（上限 {max_age_days}），先刷新再判月末")
    return days


def ensure_covered(days: list[str], ym: str) -> None:
    """确认日历覆盖到了 ``ym`` 的**整月**。

    判据不是「日历里有这个月的某一天」，而是「日历里这个月的最后一天之后还有
    交易日，或者日历恰好结束在该月月末」。前者说明该月已完整；后者说明日历是
    整齐截断在月末（常见于只更新到年末）。**截断在月中必须报错**——否则会把
    月中那天误当成月末。
    """
    if not days:
        raise CalendarUnavailable("交易日历为空")
    first, last = month_bounds(ym)
    cal_max = days[-1]
    in_month = [d for d in days if d[:6] == ym]
    if not in_month:
        # ⚠️ 顺序有意义：日历整体**不包含**该月（在该月之前或之后结束）属"未覆盖"，
        #    与"日历本身有问题"不同——前者刷新即可，后者要查文件。
        if cal_max < _fmt(first):
            raise CalendarNotCovered(
                f"交易日历止于 {cal_max}，未覆盖 {ym}（该月首日 {_fmt(first)}）⇒ 无法判定月末")
        if days[0] > _fmt(last):
            raise CalendarNotCovered(
                f"交易日历始于 {days[0]}，未覆盖 {ym}（该月末日 {_fmt(last)}）⇒ 无法判定月末")
        raise CalendarUnavailable(f"交易日历里没有 {ym} 的任何交易日（日历 {days[0]}→{cal_max}）")
    if cal_max < _fmt(last):
        # 日历结束在本月之内：只有当它正好结束在本月最后一个**交易日**上时才可能完整，
        # 但无法区分「月末就是这天」与「更新到这天为止」，一律判为未覆盖。
        raise CalendarNotCovered(
            f"交易日历止于 {cal_max}，未覆盖 {ym} 全月（月末 {_fmt(last)}）⇒ 无法判定月末")
    _ = first  # 首日仅用于语义完整，不作判据


def last_trading_day(ym: str, days: list[str]) -> str:
    """该月最后一个交易日。日历未覆盖时抛异常（不返回近似值）。"""
    ensure_covered(days, ym)
    in_month = [d for d in days if d[:6] == ym]
    if not in_month:
        raise CalendarUnavailable(f"交易日历里没有 {ym} 的交易日")
    return in_month[-1]


def is_month_end(day: str, days: list[str]) -> bool:
    """``day`` 是否为其所在月的最后一个交易日。"""
    d = _norm(day)
    return d == last_trading_day(d[:6], days)


def describe(day: str, days: list[str]) -> str:
    """给错误消息用的一句话说明：该月真正的月末是哪天。"""
    d = _norm(day)
    try:
        want = last_trading_day(d[:6], days)
    except CalendarUnavailable as e:
        return f"无法确认 {d[:6]} 的月末交易日：{e}"
    if want == d:
        return f"{d} 是 {d[:6]} 的最后一个交易日"
    return f"{d[:6]} 的最后一个交易日是 {want}，不是 {d}"


def _main(argv: list[str]) -> int:
    """``--refresh`` 刷新缓存并打印摘要。

    刻意做成极简 CLI：部署到云主机后，每季度（或每年假日表发布后）跑一次
    ``python -m trade_calendar --refresh`` 即可。**不自动刷新**——
    自动刷新意味着某天接口变了会静默换掉日历；月末判断依赖它，必须留痕。
    """
    if "--refresh" not in argv:
        print("用法：python trade_calendar.py --refresh [--path PATH]")
        return 2
    path = None
    if "--path" in argv:
        path = pathlib.Path(argv[argv.index("--path") + 1])
    days = fetch()
    p = save(days, path)
    print(f"[calendar] {len(days)} 个交易日｜{days[0]} → {days[-1]}")
    print(f"[calendar] 已写入 {p}")
    for ym in ("202609", "202610", "202611", "202612", "202701"):
        try:
            print(f"  {ym} 月末交易日 = {last_trading_day(ym, days)}")
        except CalendarUnavailable as e:
            print(f"  {ym} 未覆盖：{e}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
