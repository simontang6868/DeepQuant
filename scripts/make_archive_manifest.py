#!/usr/bin/env python
"""为已验收的数据层生成逐文件 sha256 清单（存档校验用）。

## 为什么不是"再打包一份"

全 A 数据层实测体积：

    data/exec_all                730 MB / 5,472 文件
    data/qfq_all                 925 MB / 5,444 文件
    data/div_all_em + sina        52 MB / 10,930 文件

合计 **1.65 GB**。邮件发不了，云端上传在本机反复失败（`LibraryUpload fetch failed`）。
而"存档"的实际用途是**能证明手里这份没坏、能被别人核对、能重建**，
不是再复制一份。所以这里产出的是：

1. 逐文件 sha256（约 1 MB 文本）—— 拿到它就能校验任何一份拷贝；
2. 汇总指纹（目录级 sha256）—— 一行比对即可；
3. 行数/日期跨度摘要 —— 光有哈希不能说明"内容对不对"，行数能看出截断。

## 用法

    python scripts/make_archive_manifest.py
    python scripts/make_archive_manifest.py --dirs data/exec_all data/qfq_all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor


def _sha256(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _rows(path: str) -> int:
    """数据行数（不含表头）。用于发现截断——哈希只证明"同一份"，不证明"完整"。

    ⚠️ 只对 CSV 有意义。分红事件是 `.div.json`（JSON 数组），行数概念不适用，
    调用方传 0。第一版无脑数行，把 div 目录判成 0 文件——因为过滤条件只认 `.csv`。
    """
    with open(path, "rb") as f:
        return max(0, sum(1 for _ in f) - 1)


def collect(dir_path: str, workers: int = 8) -> dict:
    names = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]
    # 优先 CSV；没有 CSV 的目录（分红事件）退到 JSON。
    # 混着的目录（如 exec_all 有 _acceptance.json）只用 CSV，避免把元数据算进指纹。
    csvs = [f for f in names if f.endswith(".csv")]
    jsons = [f for f in names if f.endswith(".json")]
    files = sorted(csvs if csvs else jsons)
    kind = "csv" if csvs else ("json" if jsons else "none")
    t0 = time.time()

    def one(name: str) -> tuple:
        p = os.path.join(dir_path, name)
        return name, _sha256(p), os.path.getsize(p), (_rows(p) if name.endswith(".csv") else 0)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        recs = list(ex.map(one, files))

    # 目录级指纹：对 (文件名, 文件哈希) 排序后串接再哈希。
    # ⚠️ 不能用"文件哈希之和"之类的可交换运算——那样换名换序都发现不了。
    h = hashlib.sha256()
    for name, sha, _sz, _n in recs:
        h.update(f"{name}:{sha}\n".encode())
    total_rows = sum(r[3] for r in recs)
    return {
        "dir": dir_path,
        "kind": kind,
        "n_files": len(recs),
        "n_rows_total": total_rows if kind == "csv" else None,
        "bytes_total": sum(r[2] for r in recs),
        "dir_sha256": h.hexdigest(),
        "files": {name: {"sha256": sha, "bytes": sz, "rows": (n if kind == "csv" else None)}
                  for name, sha, sz, n in recs},
        "elapsed_sec": round(time.time() - t0, 1),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成数据层逐文件 sha256 清单")
    ap.add_argument("--dirs", nargs="+", default=["data/exec_all", "data/qfq_all",
                                                  "data/div_all_em", "data/div_all_sina"])
    ap.add_argument("--out-dir", default="dist")
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    summary = {}
    for d in args.dirs:
        if not os.path.isdir(d):
            print(f"[跳过] {d} 不存在")
            continue
        print(f"[清单] {d} …")
        r = collect(d)
        tag = os.path.basename(d.rstrip("/\\"))
        out = os.path.join(args.out_dir, f"MANIFEST_{tag}.json")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(r, ensure_ascii=False, indent=1) + "\n")
        summary[tag] = {
            "dir": r["dir"], "n_files": r["n_files"], "n_rows_total": r["n_rows_total"],
            "bytes_total": r["bytes_total"], "dir_sha256": r["dir_sha256"],
            "manifest": out, "manifest_sha256": _sha256(out),
        }
        rows_s = f"{r['n_rows_total']:,} 行" if r["n_rows_total"] is not None else "（JSON，不数行）"
        print(f"       {r['n_files']} 文件 / {rows_s} / "
              f"{r['bytes_total']/1e6:.1f} MB  → {out}")
        print(f"       dir_sha256 = {r['dir_sha256']}  ({r['elapsed_sec']}s)")

    path = os.path.join(args.out_dir, "ARCHIVE_FINGERPRINT.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "dir_sha256 = 对排序后的 (文件名, 文件sha256) 串接再哈希；"
                    "换名/换序/换内容都会变。n_rows_total 用于发现截断。",
            "layers": summary,
        }, ensure_ascii=False, indent=1) + "\n")
    print(f"\n[指纹] -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
