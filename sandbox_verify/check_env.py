"""环境自检：把「数据根 / 数据集 / 前端产物 / 端口」四件事一次说清。

为什么单独写一个
----------------
交付物被质疑最多的不是代码，是**环境**：
「为什么我这边 0 个数据集」「为什么启动器能发现、uvicorn 直连发现不了」。
这些都能用三条规则回答，但不该让审查方去读源码。本脚本把规则**打印出来**并标出命中哪一条。

对应关系（必须一致，不一致就是 bug）
------------------------------------
| 起法                          | 数据根解析                        |
|-------------------------------|-----------------------------------|
| `scripts/start_deepquant.py`  | env → 仓库 data/ → backend/data/  |
| `uvicorn deepquant.api.app:app` | **只看 env → 默认 backend/data/** |

用法
----
    python sandbox_verify/check_env.py
    python sandbox_verify/check_env.py --port 8123 --expect-datasets 51

退出码：0 正常；1 数据根无数据集或前端产物缺失。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def meta_datasets(root: Path) -> list[Path]:
    return sorted((root / "metadata").glob("*.meta.json")) if root.is_dir() else []


def parquet_datasets(root: Path) -> list[Path]:
    return sorted((root / "processed").glob("*.parquet")) if root.is_dir() else []


def resolve_data_root(repo: Path) -> tuple[Path, str]:
    """复刻 scripts/start_deepquant.py:resolve_data_root 的三级规则，并返回命中说明。"""
    env = os.environ.get("DEEPQUANT_DATA_ROOT")
    if env and env.strip():
        return Path(env).expanduser().resolve(), "规则1：环境变量 DEEPQUANT_DATA_ROOT"
    for candidate in (repo / "data", repo / "backend" / "data"):
        if candidate.is_dir() and meta_datasets(candidate):
            return candidate.resolve(), f"规则2：自动发现（{candidate.relative_to(repo).as_posix()} 含 metadata）"
    return (repo / "data").resolve(), "规则3：回退到 仓库/data（可能为空）"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    ap = argparse.ArgumentParser(description="环境自检")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--expect-datasets", type=int, default=0, help=">0 时校验数据集条数")
    args = ap.parse_args()

    problems: list[str] = []

    print("== 运行时 ==")
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"平台        : {sys.platform}")

    print("\n== 数据根 ==")
    root, why = resolve_data_root(REPO)
    metas, pars = meta_datasets(root), parquet_datasets(root)
    print(f"仓库根      : {REPO}")
    print(f"命中规则    : {why}")
    print(f"数据根      : {root}")
    print(f"数据集      : metadata {len(metas)} 个 / processed {len(pars)} 个")
    if not metas:
        problems.append("数据根下没有 metadata/*.meta.json → 界面会是 0 个数据集")

    api_default = (REPO / "backend" / "data").resolve()
    print("\n== 两种起法的差异（这是上一轮争论的点，这里直接给结论）==")
    print(f"启动器 start_deepquant.py : {root}")
    print(f"直连 uvicorn 的默认值     : {api_default}")
    if root != api_default:
        print("→ 两者不同。**直连 uvicorn 必须显式设 DEEPQUANT_DATA_ROOT**，否则读不到数据集。")
    else:
        print("→ 两者相同，无需额外设置。")

    print("\n== 前端与落库 ==")
    index = REPO / "frontend" / "dist" / "index.html"
    print(f"frontend/dist/index.html : {'存在' if index.is_file() else '缺失'}")
    if not index.is_file():
        problems.append("frontend/dist/index.html 缺失 → 只能用 API，不能开界面")
    db = REPO / "backend" / "data"
    print(f"backend/data（SQLite 落库目录） : {'存在' if db.is_dir() else '不存在（首次运行会创建，需可写）'}")

    print("\n== 端口 ==")
    free = port_free(args.port)
    print(f"127.0.0.1:{args.port} : {'空闲' if free else '被占用（启动器会自动顺延）'}")

    if args.expect_datasets:
        if len(metas) != args.expect_datasets:
            problems.append(f"数据集条数 {len(metas)} ≠ 期望 {args.expect_datasets}")

    print()
    if problems:
        print("[FAIL]")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("[PASS] 环境自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
