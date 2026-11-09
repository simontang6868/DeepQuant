#!/usr/bin/env python3
"""PA-OCT2026-1 §6 变异测试：证明 as-of 守卫的每条判据都真改行为。

⚠️ 项目铁律：变异必须**真改行为**（假变异已出现两次）；
   变异脚本原地改源码期间禁止并发跑任何 pytest —— 本脚本改为
   「把守卫源码复制到临时目录做替换后 importlib 加载」，不改产品文件，
   因此可与其它命令并发，且不存在源码被还原失败的风险。

检出判据（双向，防假检出）：
  1) 原实现在负例上 ok=False；
  2) 变异体在同一负例上 ok=True（或至少原因码减少且 ok 翻转）；
  3) 变异体在正例上仍 ok=True（防止「全判 FAIL」被误算检出）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "oct2026_asof_guard.py"
QFQ = REPO / "data" / "qfq_all"
CODES = ("603193", "301336", "301202", "000597", "002692")
DAYS = ("20261008", "20261009", "20261012", "20261013", "20261014",
        "20261015", "20261016", "20261019", "20261020", "20261021",
        "20261022", "20261023", "20261026", "20261027", "20261028",
        "20261029", "20261030")
ASOF = "20261030"

src = GUARD.read_text(encoding="utf-8")
spec = importlib.util.spec_from_file_location("asof_guard_base", GUARD)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def _write(path: pathlib.Path, text: str) -> None:
    path.write_bytes((text if text.endswith("\n") else text + "\n").encode("utf-8"))


def build_root(tmp: pathlib.Path, mutate=None) -> pathlib.Path:
    d = tmp / "qfq"
    d.mkdir()
    for code in CODES:
        p = next(QFQ.glob(code + ".*.qfq.csv"))
        lines = p.read_text(encoding="utf-8").splitlines()
        keep = [lines[0]] + [ln for ln in lines[1:]
                             if ln.split(",")[0][:10] >= "2026-09-30"
                             and ln.split(",")[0][:10] <= "2026-10-30"]
        _write(d / p.name, "\n".join(keep))
    if mutate:
        mutate(d)
    return d


def inject_future(d: pathlib.Path) -> None:
    p = next(d.glob("603193.*.qfq.csv"))
    _write(p, p.read_text(encoding="utf-8").rstrip("\n")
           + "\n2026-11-09,1.0,1.0,1.0,1.0,1\n")


def drop_file(d: pathlib.Path) -> None:
    next(d.glob("301336.*.qfq.csv")).unlink()


def drop_mid_day(d: pathlib.Path) -> None:
    p = next(d.glob("000597.*.qfq.csv"))
    lines = [x for x in p.read_text(encoding="utf-8").splitlines()
             if not x.startswith("2026-10-20,")]
    _write(p, "\n".join(lines))


def zero_price(d: pathlib.Path) -> None:
    p = next(d.glob("002692.*.qfq.csv"))
    out = []
    for x in p.read_text(encoding="utf-8").splitlines():
        if x.startswith("2026-10-15,"):
            z = x.split(",")
            z[2] = "0"
            x = ",".join(z)
        out.append(x)
    _write(p, "\n".join(out))


# 输入变异：不改源码，只破坏输入，原实现必须 FAIL 且原因码正确
INPUT_MUTATIONS = [
    ("I1 注入 2026-11-09 未来行", inject_future, "FAIL:FUTURE_ROW"),
    ("I2 删除 301336 文件", drop_file, "FAIL:MISSING_FILE"),
    ("I3 删除中间交易日 2026-10-20", drop_mid_day, "FAIL:MISSING_TRADE_DAY"),
    ("I4 把 2026-10-15 收盘价置 0", zero_price, "FAIL:NONPOSITIVE_CLOSE"),
]

# 源码变异：每条只改一处，必须是被测试路径上的真实判据
SOURCE_MUTATIONS = [
    ("M1 比较时绕过日期归一化（直接比原始字符串）",
     "if d > asof:", "if z[0].strip() > asof:", inject_future),
    ("M2 未来行判定永假",
     "if d > asof:", "if False:", inject_future),
    ("M3 缺文件不记原因（静默跳过）",
     'reasons.append("FAIL:MISSING_FILE:" + code)', "pass", drop_file),
    ("M4 空输入不失败关闭（all({}.values()) 恒真坑）",
     "if not codes or not days:", "if False:", None),
    ("M5 未来行不记原因也不累加计数",
     "if future:", "if False:", inject_future),
    ("M6 缺交易日不记原因",
     "if missing:", "if False:", drop_mid_day),
    ("M7 非正收盘价不记原因",
     "if bad:", "if False:", zero_price),
    ("M8 闸门恒真",
     "ok = len(reasons) == 0 and n_future == 0", "ok = True", inject_future),
]


def load_mutant(mut_src: str, tmp: pathlib.Path, tag: str):
    p = tmp / ("guard_mut_" + tag + ".py")
    _write(p, mut_src)
    s = importlib.util.spec_from_file_location("guard_mut_" + tag, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def main() -> int:
    detected = 0
    total = len(INPUT_MUTATIONS) + len(SOURCE_MUTATIONS)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        clean = build_root(tmp)
        r_clean = base.evaluate(clean, CODES, DAYS, ASOF)
        assert r_clean["ok"] is True, "干净输入必须先 ASOF_OK，否则后续检出无意义"
        print("[base] 干净输入 ASOF_OK | files 5 | max_date " + r_clean["max_date_seen"])

        for name, mutate, expect in INPUT_MUTATIONS:
            sub = tmp / ("in_" + name.split()[0])
            sub.mkdir()
            root = build_root(sub, mutate)
            r = base.evaluate(root, CODES, DAYS, ASOF)
            hit = (not r["ok"]) and any(x.startswith(expect) for x in r["reasons"])
            if not hit:
                print("[%s] ✗ 未检出：%s" % (name.split()[0], r))
                continue
            detected += 1
            print("[%s] ✓ 检出：%s（reason=%s）" % (
                name.split()[0], name, next(x for x in r["reasons"] if x.startswith(expect))))

        for i, (name, old, new, mutate) in enumerate(SOURCE_MUTATIONS, 1):
            tag = "M%d" % i
            if src.count(old) != 1:
                print("[%s] ✗ 变异锚点不唯一/不存在（count=%d）" % (tag, src.count(old)))
                continue
            mut = load_mutant(src.replace(old, new, 1), tmp, tag)
            # 空输入变异用空 codes 作负例
            if mutate is None:
                neg_codes, neg_days, neg_root = (), DAYS, clean
            else:
                sub = tmp / ("src_" + tag)
                sub.mkdir()
                neg_root = build_root(sub, mutate)
                neg_codes, neg_days = CODES, DAYS
            b = base.evaluate(neg_root, neg_codes, neg_days, ASOF)
            m = mut.evaluate(neg_root, neg_codes, neg_days, ASOF)
            mpos = mut.evaluate(clean, CODES, DAYS, ASOF)
            hit = (b["ok"] is False and m["ok"] is True and mpos["ok"] is True)
            if not hit:
                print("[%s] ✗ 未检出：base=%s mut=%s mut_pos=%s" % (
                    tag, b["ok"], m["ok"], mpos["ok"]))
                continue
            detected += 1
            print("[%s] ✓ 检出：%s（base FAIL → 变异体 PASS，正例仍 PASS）" % (tag, name))

    print("\n[asof-guard 变异] %d/%d 检出" % (detected, total))
    return 0 if detected == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
