# -*- coding: utf-8 -*-
"""通达信 tool-results 落盘文件的统一读取器。

坑：落盘 .txt 不是纯 JSON，前面有一行中文摘要（【工商银行】601398 | 现价...）
    和一行 "详细K线数据:"，JSON 从第一个 '{' 开始。直接 json.load 会全失败。
"""
import json, glob, os

TDIR = (r"C:\Users\77436\.workbuddy\projects"
        r"\d-DeepQuant power by txm-DeepQuant-main"
        r"\33860a17-098b-4d49-954f-75fde862c20d\tool-results")


def load_tdx(path):
    """从落盘文件里抽出 JSON dict，失败返回 None。"""
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    i = raw.find("{")
    if i < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[i:])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def scan(period=None, tdir=TDIR):
    """扫全部 kline 落盘文件，返回 {code: [ (start, end, nrows, depth), ... ]}。

    depth = 'D'(period 4) / 'M'(period 6) / 其它
    """
    out = {}
    for p in glob.glob(os.path.join(tdir, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        per = str(d.get("Period", "")).split(".")[0]
        if period is not None and per != str(period):
            continue
        code = str(d.get("Code", "")).strip()
        rows = d["Rows"] or []
        ds = [str(r.get("Data", "")) for r in rows if r.get("Data")]
        if not ds:
            continue
        out.setdefault(code, []).append((min(ds), max(ds), len(rows)))
    return out


if __name__ == "__main__":
    for per in ("4", "6"):
        m = scan(per)
        print(f"period={per}: {len(m)} codes")
        k = sorted(m)[:3]
        for c in k:
            print("  ", c, m[c])
