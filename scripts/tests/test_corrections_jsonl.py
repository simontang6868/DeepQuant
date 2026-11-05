"""证据文件 `corrections.jsonl` 必须是合法 JSONL。

## 为什么单独写一个测试

`corrections.jsonl` 是对外证据的一部分（更正/决策的追加式日志），下游会被脚本解析。
2026-11-05 我手工追加一条记录时，在 JSON 字符串里用了**未转义的英文双引号**
（`……得出"闲置 27.1%"……`），那行立刻变成非法 JSON——但文件本身"看起来"没问题，
`cat` 也不报错。如果没人解析，这个坏行会一直留在证据里。

这类"文本看起来对、结构其实坏了"的缺陷，用一条机械校验最省事。

## 校验什么

1. 每一非空行都能 `json.loads` 成对象（不是数组、不是标量）；
2. 每行都有 `ts` 与 `event`（两条必备字段，缺了就无法追溯）；
3. `(ts, event)` 组合唯一 —— 同一时刻的同一事件出现两次，才是重复追加；
   ⚠️ **同一 event 名允许出现多次**（同一类更正可能在不同日期发生多次）。
   本测试第一版按"event 名唯一"写，在真实文件上立刻失败：
   `LEDGER_OVERWRITTEN_WITH_FORCE` 与 `LATE_RUN_RECORDED` 各出现 3 次，
   全都是不同日期的真实事件。这是"把正常当异常"的典型——判据本身写错了。

**不校验**字段内容的具体取值——那是人工评审的事，机器只保证结构不坏。
"""

from __future__ import annotations

import json
import pathlib

import pytest

FWD = pathlib.Path(__file__).resolve().parents[2] / "research" / "stock_pick_2026-10-25" / "forward"
CORR = FWD / "corrections.jsonl"


def _lines() -> list[tuple[int, str]]:
    if not CORR.is_file():
        pytest.skip(f"未找到 {CORR}（可能是裁剪后的工作区）")
    return [(i, ln) for i, ln in enumerate(CORR.read_text(encoding="utf-8").splitlines(), 1)
            if ln.strip()]


def test_every_line_is_valid_json_object():
    bad = []
    for i, ln in _lines():
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as e:
            bad.append(f"第 {i} 行不是合法 JSON：{e.msg}（列 {e.colno}）")
            continue
        if not isinstance(obj, dict):
            bad.append(f"第 {i} 行是 {type(obj).__name__}，应为对象")
    assert not bad, "corrections.jsonl 结构损坏：\n" + "\n".join(bad)


def test_required_fields_present():
    missing = []
    for i, ln in _lines():
        obj = json.loads(ln)
        for k in ("ts", "event"):
            if not obj.get(k):
                missing.append(f"第 {i} 行缺 {k!r}")
    assert not missing, "\n".join(missing)


def test_ts_event_pair_unique():
    """同一 (ts, event) 出现两次 ⇒ 重复追加（同一类事件在不同时间是正常的）。"""
    seen: dict[tuple[str, str], int] = {}
    dup: list[str] = []
    for i, ln in _lines():
        o = json.loads(ln)
        key = (str(o.get("ts")), str(o.get("event")))
        if key in seen:
            dup.append(f"{key} 出现在第 {seen[key]} 与第 {i} 行")
        else:
            seen[key] = i
    assert not dup, "同一时刻同一事件重复追加：\n" + "\n".join(dup)


def test_event_names_are_uppercase_tokens():
    """事件名统一大写下划线风格，便于机械检索（不强制，但偏离要显眼）。"""
    import re
    bad = []
    for i, ln in _lines():
        ev = str(json.loads(ln).get("event", ""))
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", ev):
            bad.append(f"第 {i} 行 event={ev!r}")
    assert not bad, "事件名风格不一致：\n" + "\n".join(bad)


def test_ts_format_is_iso_like():
    """时间戳必须是 `YYYY-MM-DDTHH:MM:SS` 开头——排序与对账都依赖它。"""
    import re
    pat = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    bad = [f"第 {i} 行 ts={json.loads(ln).get('ts')!r}"
           for i, ln in _lines() if not pat.match(str(json.loads(ln).get("ts", "")))]
    assert not bad, "\n".join(bad)
