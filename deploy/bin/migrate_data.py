#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据迁移的 sha256 清单、拷贝与逐文件校验。

## 为什么不用 rsync 的 `--checksum` 就够了

rsync 校验的是"传输过程中没坏"，不校验"传完之后两边的**文件集合**是否一致"。
少传一个文件时 rsync 会认为自己成功（它没被要求传那个文件）。而缺一个
`qfq_all/*.qfq.csv` 的后果是：某些股票在截面里消失，选股结果变了，**但没有任何报错**。

所以这里做三件事，缺一不可：
1. **manifest**：在源机生成"文件 → sha256"的清单（含大小与相对路径）；
2. 传输（rsync/scp 由外部执行，本脚本不猜 SSH 参数）；
3. **verify**：在目标机按清单逐个重算 sha256，并**双向**报告
   （清单有、磁盘无 = 缺文件；磁盘有、清单无 = 多了文件）。

## 三条使用约定

- 清单本身也要迁移过去，否则目标机无从校验。清单里带 ``manifest_sha256``，
  校验前先自检，防止清单在传输中被改。
- **不跳过任何文件**：`--exclude` 只在 manifest 阶段生效，且必须写进清单的
  ``excludes`` 字段，事后可查"当初排除了什么"。
- 校验失败**不自动重传**：重传会掩盖"为什么只坏了这一个"，而那通常是磁盘或
  网络问题的第一个信号。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import time


def sha256_file(p: pathlib.Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def walk(root: pathlib.Path, excludes: list[str]):
    """产出 (相对路径, 绝对路径)。excludes 是相对 root 的前缀/子串匹配。"""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(x and (rel == x or rel.startswith(x.rstrip("/") + "/") or x in rel)
               for x in excludes):
            continue
        yield rel, p


def build_manifest(specs: list[tuple[str, pathlib.Path]], excludes: list[str],
                   specs_rel: list[tuple[str, pathlib.Path, str]] | None = None) -> dict:
    items: list[dict] = []
    t0 = time.time()
    specs_rel = specs_rel or [(lbl, root, root.as_posix()) for lbl, root in specs]
    for label, root in specs:
        if not root.exists():
            raise SystemExit(f"[manifest] 目录不存在：{root}")
        n_before = len(items)
        for rel, p in walk(root, excludes):
            st = p.stat()
            items.append({
                "root": label,
                "path": rel,
                "size": st.st_size,
                "sha256": sha256_file(p),
            })
        print(f"[manifest] {label:12s} {len(items) - n_before:6d} 个文件  {root}")
    total = sum(i["size"] for i in items)
    payload = {
        "created_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        # ⚠️ roots 必须写进清单：verify 若自己用默认三项去推目录，
        #    清单只覆盖 forward 时会把 exec_all 的 1 万个文件全报成"多余"。
        #    这不是理论问题——首次试跑就是这么错的。
        "roots_rel": {label: rel for label, _, rel in specs_rel},
        "n_files": len(items),
        "total_bytes": total,
        "excludes": excludes,
        "items": items,
        "elapsed_s": round(time.time() - t0, 1),
    }
    # 清单自校验：只覆盖 (path,size,sha256) 三元组，顺序固定
    h = hashlib.sha256()
    for i in items:
        h.update(f"{i['root']}/{i['path']}\0{i['size']}\0{i['sha256']}\n".encode("utf-8"))
    payload["manifest_sha256"] = h.hexdigest()
    return payload


def verify(manifest: dict, roots: dict[str, pathlib.Path]) -> int:
    """逐文件重算并**双向**比。返回不一致条数。"""
    if manifest.get("manifest_sha256"):
        h = hashlib.sha256()
        for i in manifest["items"]:
            h.update(f"{i['root']}/{i['path']}\0{i['size']}\0{i['sha256']}\n".encode("utf-8"))
        if h.hexdigest() != manifest["manifest_sha256"]:
            print("[verify] ✗ 清单自身的 sha256 不符——清单在传输中被改过，停止校验")
            return 1

    missing, size_bad, hash_bad, ok = [], [], [], 0
    seen: set[tuple[str, str]] = set()
    for i in manifest["items"]:
        root = roots.get(i["root"])
        if root is None:
            raise SystemExit(f"[verify] 未知 root 标签：{i['root']}")
        p = root / i["path"]
        seen.add((i["root"], i["path"]))
        if not p.exists():
            missing.append(f"{i['root']}/{i['path']}")
            continue
        if p.stat().st_size != i["size"]:
            size_bad.append(f"{i['root']}/{i['path']}  {p.stat().st_size} vs {i['size']}")
            continue
        if sha256_file(p) != i["sha256"]:
            hash_bad.append(f"{i['root']}/{i['path']}")
            continue
        ok += 1

    # 反向：磁盘上有、清单里没有（多出来的文件同样是偏差）
    extra: list[str] = []
    for label, root in roots.items():
        for rel, _p in walk(root, manifest.get("excludes") or []):
            if (label, rel) not in seen:
                extra.append(f"{label}/{rel}")

    print(f"[verify] 一致 {ok}｜缺失 {len(missing)}｜大小不符 {len(size_bad)}"
          f"｜哈希不符 {len(hash_bad)}｜多余 {len(extra)}")
    for title, rows in (("缺失", missing), ("大小不符", size_bad),
                        ("哈希不符", hash_bad), ("多余", extra)):
        for r in rows[:20]:
            print(f"  ✗ [{title}] {r}")
        if len(rows) > 20:
            print(f"  ... [{title}] 另有 {len(rows) - 20} 条")
    return len(missing) + len(size_bad) + len(hash_bad) + len(extra)


def copy(manifest: dict, roots: dict[str, pathlib.Path], dest_root: pathlib.Path,
         apply: bool) -> int:
    """本地拷贝（用于同机演练）。真正的跨机传输由 rsync 完成，本函数不碰 SSH。"""
    n = 0
    for i in manifest["items"]:
        src = roots[i["root"]] / i["path"]
        dst = dest_root / i["root"] / i["path"]
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        n += 1
    print(f"[copy] {'已拷贝' if apply else '将拷贝'} {n} 个文件 → {dest_root}"
          f"（{'apply' if apply else 'dry-run，加 --apply 才真写'}）")
    return 0


DEFAULT_SPECS = [
    ("exec_all", "data/exec_all"),
    ("qfq_all", "data/qfq_all"),
    ("forward", "research/stock_pick_2026-10-25/forward"),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="生成 sha256 清单")
    m.add_argument("--base", default=".", help="仓库根（相对路径基准）")
    m.add_argument("--out", required=True)
    m.add_argument("--exclude", action="append", default=[],
                   help="相对路径片段；如 universe_state（每日快照不入迁移）")
    m.add_argument("--root", action="append", default=[],
                   help="自定义 label=相对路径，可多次；不传则用默认三项")

    v = sub.add_parser("verify", help="按清单逐文件校验")
    v.add_argument("--base", default=".")
    v.add_argument("--manifest", required=True)

    c = sub.add_parser("copy", help="本地拷贝演练")
    c.add_argument("--base", default=".")
    c.add_argument("--manifest", required=True)
    c.add_argument("--dest", required=True)
    c.add_argument("--apply", action="store_true")

    args = ap.parse_args(argv)
    base = pathlib.Path(args.base).resolve()

    if args.cmd == "manifest":
        if args.root:
            specs = []
            for spec in args.root:
                label, _, rel = spec.partition("=")
                specs.append((label, base / rel))
        else:
            specs = [(label, base / rel) for label, rel in DEFAULT_SPECS]
        payload = build_manifest(specs, args.exclude)
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8", newline="\n")
        print(f"[manifest] {payload['n_files']} 个文件｜"
              f"{payload['total_bytes'] / 1048576:.1f} MB｜{payload['elapsed_s']} s")
        print(f"[manifest] sha256={payload['manifest_sha256'][:16]}…  已写入 {out}")
        print("[manifest] ⚠️ 清单本身也要传到目标机，否则无法校验")
        return 0

    if args.cmd == "verify":
        manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
        roots = {label: base / rel
                 for label, rel in (manifest.get("roots_rel") or dict(DEFAULT_SPECS)).items()}
        bad = verify(manifest, roots)
        print("[verify] PASS" if bad == 0 else f"[verify] FAIL（{bad} 处偏差）")
        return 0 if bad == 0 else 1

    if args.cmd == "copy":
        manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
        roots = {label: base / rel
                 for label, rel in (manifest.get("roots_rel") or dict(DEFAULT_SPECS)).items()}
        return copy(manifest, roots, pathlib.Path(args.dest), args.apply)

    return 2


if __name__ == "__main__":
    sys.exit(main())
