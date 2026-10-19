"""包身份核验：先证明「你手上这份是哪一版」，再谈跑出来的数字。

为什么必须要有这个
------------------
同一个文件名被重建过多次，zip 的 sha256 变了但文件名没变。
上一轮复核就发生过：对方核的是旧包，却把结论写进了新包的评审里 —— 双方都没说谎，
只是**没有一个可引用的包身份**。

本脚本给出三样东西：
1. **包 ID** = `MANIFEST.sha256` 的 sha256（与 zip 压缩方式无关，重建内容不变则 ID 不变）；
2. 逐文件 sha256 校验（等价 `sha256sum -c`）；
3. 关键标记的存在性（少一个就说明你拿的不是完整版）。

用法
----
    python sandbox_verify/verify_package.py
    python sandbox_verify/verify_package.py --manifest ../MANIFEST.sha256

退出码：0 通过；1 有缺失/不匹配/标记缺失。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 关键标记：(相对路径, 必须包含的子串或 None=只要求存在, 说明)
MARKERS: list[tuple[str, str | None, str]] = [
    ("backend/requirements.txt", "tabulate==", "tabulate 已显式声明（不能只靠 akshare 传递依赖）"),
    ("backend/tests/backtest/test_cross_section.py", "DEEPQUANT_REAL_600809", "真实行情用例路径可覆盖（否则换机永久 skip）"),
    ("data/step75", None, "data/step75 存在（缺它测试会 1 failed + 31 skipped）"),
    ("frontend/dist/index.html", None, "前端产物已构建（单端口界面需要）"),
    ("sandbox_verify/p0_1_windows.py", None, "P0-1 验证脚本在包内"),
    ("sandbox_verify/p0_2_price_jump.py", None, "P0-2 验证脚本在包内"),
    ("sandbox_verify/check_deps.py", None, "依赖完备性冒烟在包内"),
    ("docs/GATE1_PREREGISTRATION.md", None, "预登记原件在包内"),
    ("gate1_out/032a11f591553e25/summary.json", None, "Gate 1 原始结果在包内"),
]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_manifest(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"找不到 manifest：{p}")
        return p
    for cand in (REPO.parent / "MANIFEST.sha256", REPO / "MANIFEST.sha256"):
        if cand.is_file():
            return cand
    raise SystemExit("找不到 MANIFEST.sha256；请在解压后的根目录运行，或用 --manifest 指定")


def main() -> int:
    ap = argparse.ArgumentParser(description="包身份核验")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--skip-hash", action="store_true", help="只查标记，不逐文件校验（大包快查）")
    args = ap.parse_args()

    manifest = find_manifest(args.manifest)
    raw = manifest.read_bytes()
    package_id = sha256_bytes(raw)
    lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]

    print(f"包 ID（MANIFEST 的 sha256）: {package_id}")
    print(f"  短 ID（引用用这个）      : {package_id[:16]}")
    print(f"受校验文件数             : {len(lines)}")

    # 与 PACKAGE_INFO.json 交叉核对（若存在）
    info_path = manifest.parent / "PACKAGE_INFO.json"
    if info_path.is_file():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            info = {}
        ok_id = info.get("package_id") == package_id
        ok_cnt = info.get("file_count") == len(lines)
        print(f"PACKAGE_INFO.json        : {'一致' if ok_id and ok_cnt else '与 manifest 不一致！'}")
        if info.get("built_at"):
            print(f"构建时间                 : {info['built_at']}")
        if not (ok_id and ok_cnt):
            print("\n[FAIL] PACKAGE_INFO.json 与 MANIFEST 不一致 —— 包被改过或打包中断，不要用它出结论。")
            return 1
    else:
        print("PACKAGE_INFO.json        : 不存在（旧包装法，仍可校验，但没有构建时间）")

    missing = bad = 0
    if not args.skip_hash:
        print(f"\n逐文件校验中……（{len(lines)} 个文件，约数秒）")
        for ln in lines:
            try:
                want, rel = ln.split("  ", 1)
            except ValueError:
                print(f"  [格式错] {ln[:60]}")
                bad += 1
                continue
            p = REPO / rel
            if not p.is_file():
                print(f"  [缺失] {rel}")
                missing += 1
                continue
            if sha256_file(p) != want:
                print(f"  [不匹配] {rel}")
                bad += 1
        print(f"结果：{len(lines) - missing - bad}/{len(lines)} 通过，缺失 {missing}，不匹配 {bad}")

    print("\n关键标记：")
    mark_fail = 0
    for rel, needle, note in MARKERS:
        p = REPO / rel
        if not p.exists():
            print(f"  [缺失] {rel} —— {note}")
            mark_fail += 1
            continue
        if needle is not None:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if needle not in text:
                print(f"  [不符] {rel} 不含 {needle!r} —— {note}")
                mark_fail += 1
                continue
        print(f"  [OK]   {rel}")

    print()
    if missing or bad or mark_fail:
        print("[FAIL] 这份包不完整或与 MANIFEST 不符。**不要用它的结果下结论**，先重新取包。")
        return 1
    print("[PASS] 包完整。")
    print(f"请在报告里引用包 ID：{package_id[:16]}（完整值见上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
