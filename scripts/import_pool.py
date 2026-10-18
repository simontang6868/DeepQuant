"""把研究池（b50_v3 的 50 只）导入成本机可用数据集。

问题
----
界面上的数据集来自 ``data/metadata/*.meta.json``，而研究线的 50 只行情 CSV 在
仓库外的 ``b50_v3/``。两者不通，结果是软件里只能选 5 只，研究池一只都选不到。

这个脚本为什么不是「一个 for 循环 + ingest」
--------------------------------------------
1. **不猜路径**：``--pool-dir`` 必须显式给。研究池在仓库外，写死
   ``../b50_v3`` 只在某台机器上成立，换个目录就会「什么都没导入，但退出码 0」。
2. **先验指纹再导入**：池清单里每只都有 sha256。文件对不上就**拒绝**，
   而不是「反正能读进来」。研究结论建立在特定数据版本上，导入的若是另一版，
   界面上的结果与研究文档就对不上了，而这一点从界面上看不出来。
3. **不修数据**：价格非正、OHLC 矛盾的标的一律交给既有闸门拒绝并记录原因。
   修一修再导入会得到「能跑」的结果，但那个结果没有任何文档对应。
4. **幂等**：同 sha256 已导入 → 跳过（不重写、不改 mtime）；元数据缺名称才回填名称。
   交换目录下的数据（sha 不同）不会被静默覆盖，只会报冲突。
5. **留证据**：落盘 ``data/pool_manifest.json``，逐只记录 imported / enriched /
   unchanged / rejected / failed 与原因。没有这份清单，「48 个数据集」这个数字
   以后没人能复现。

用法::

    python scripts/import_pool.py --pool-dir "D:/DeepQuant power by txm/b50_v3" --dry-run
    python scripts/import_pool.py --pool-dir "D:/DeepQuant power by txm/b50_v3"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from deepquant.data.instrument import normalize_display_name  # noqa: E402
from deepquant.data.loaders.csv_loader import load_daily_csv  # noqa: E402
from deepquant.data.pipeline import DataValidationError, ingest_daily_csv  # noqa: E402
from deepquant.data.storage import io as storage_io  # noqa: E402
from deepquant.data.validation.checks import ERROR, validate_daily  # noqa: E402

DEFAULT_SUMMARY = "step75_research_summary.json"
MANIFEST_NAME = "pool_manifest.json"

#: 退出码语义（与 start_deepquant.py 的约定一致：0 正常，1 有问题，2 用法/输入错误）。
EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_USAGE = 2

_CODE_IN_MESSAGE = re.compile(r"([a-z_]+)\((\d+)\)")


@dataclass(frozen=True)
class PoolSymbol:
    """池清单里的一只标的。"""

    code: str
    name: str | None
    sha256: str | None


@dataclass
class SymbolOutcome:
    """单只标的的导入结果。"""

    code: str
    name: str | None
    status: str
    reason: str | None = None
    rows: int | None = None
    sha256: str | None = None
    #: 该标的在元数据里记录的来源（如 ``b50_v3/600000.SH.qfq.csv``）。
    #: 幂等运行时 manifest 全是 unchanged，没有来源就看不出这份数据是哪来的。
    source_name: str | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "rows": self.rows,
            "sha256": self.sha256,
            "source_name": self.source_name,
        }


@dataclass
class PoolResult:
    """一次导入的完整结果（也是 manifest 的内容）。"""

    pool_label: str
    summary_name: str
    summary_sha256: str | None
    n_listed: int
    n_files_found: int
    n_unlisted_files: int
    outcomes: list[SymbolOutcome] = field(default_factory=list)
    research_skipped: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def disagreements(self) -> list[dict]:
        """本工具的数据闸门与研究线的结论不一致的标的。

        两种不一致都必须报出来：
        - 研究说跳过、这里导入了 → 研究结论的样本范围与界面上能选的不一致；
        - 研究说可用、这里被拒 → 界面缺了一只研究用过的标的。
        """
        out = []
        for item in self.outcomes:
            research_rejected = item.code in self.research_skipped
            gate_rejected = item.status in {"rejected", "would_reject"}
            if research_rejected != gate_rejected:
                out.append(
                    {
                        "code": item.code,
                        "research": "rejected" if research_rejected else "usable",
                        "import_gate": item.status,
                        "reason": item.reason,
                    }
                )
        return out

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.outcomes:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def as_manifest(self) -> dict:
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "dry_run": self.dry_run,
            "pool": {
                "label": self.pool_label,
                "summary": self.summary_name,
                "summary_sha256": self.summary_sha256,
                "n_listed": self.n_listed,
                "n_files_found": self.n_files_found,
                "n_unlisted_files": self.n_unlisted_files,
                "research_skipped": self.research_skipped,
            },
            "gate": self.counts,
            "disagreements": self.disagreements,
            "symbols": [o.as_dict() for o in self.outcomes],
            "notes": (
                "本文件由 scripts/import_pool.py 生成。gate 计数描述**最近一次运行**的状态分布，"
                "不是累计导入历史；每只标的的数据来源见 source_name。status 语义："
                "imported=本次导入；enriched=数据未变、仅回填名称；unchanged=已导入且无变化；"
                "rejected=数据闸门拒绝（不导入、不修改）；conflict=已存在但指纹不同（不覆盖）；"
                "failed=文件缺失或读取失败。"
            ),
        }


# --------------------------------------------------------------------------- #
# 池清单
# --------------------------------------------------------------------------- #


def load_pool_spec(
    pool_dir: Path,
    *,
    summary_name: str = DEFAULT_SUMMARY,
    extra_names: dict[str, str] | None = None,
) -> tuple[list[PoolSymbol], dict[str, str], str | None]:
    """读取池清单，返回 (标的列表, 研究线跳过的标的及原因, 清单文件 sha256)。

    ``extra_names`` 只用于清单里没有名称的标的（本仓库里手挑的几只），
    清单自带的名称优先——避免两处名称打架时由「后写的那份」决定。
    """
    summary_path = pool_dir / summary_name
    if not summary_path.is_file():
        raise FileNotFoundError(f"池清单不存在：{summary_path}")

    raw = summary_path.read_bytes()
    summary_sha = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw.decode("utf-8"))

    stocks = data.get("stocks")
    if not isinstance(stocks, list) or not stocks:
        raise ValueError("池清单里没有 stocks 列表，无法确定要导入哪些标的")

    extra_names = extra_names or {}
    symbols: list[PoolSymbol] = []
    for item in stocks:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise ValueError("池清单中存在缺少 code 的条目")
        code = item["code"]
        name = item.get("name") or extra_names.get(code)
        symbols.append(
            PoolSymbol(code=code, name=name, sha256=item.get("sha256") or None)
        )

    skipped: dict[str, str] = {}
    for item in data.get("skipped") or []:
        if isinstance(item, dict) and isinstance(item.get("code"), str):
            reason = item.get("error_codes") or item.get("reason") or "unknown"
            skipped[item["code"]] = ",".join(reason) if isinstance(reason, list) else str(reason)

    return symbols, skipped, summary_sha


def _load_extra_names(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"名称补充文件必须是对象：{path}")
    out: dict[str, str] = {}
    for code, name in data.items():
        if code == "source":
            continue
        out[str(code)] = normalize_display_name(name) or ""
    return out


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #


def _existing_metadata(meta_path: Path) -> dict | None:
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _enrich_name(meta_path: Path, meta: dict, name: str) -> None:
    """只补名称，不动其它任何字段。"""
    updated = dict(meta)
    instrument = updated.get("instrument")
    instrument = dict(instrument) if isinstance(instrument, dict) else {}
    instrument["display_name"] = name
    updated["instrument"] = instrument
    storage_io.write_json(updated, meta_path)


def _codes_from_message(message: str) -> str | None:
    hits = _CODE_IN_MESSAGE.findall(message)
    return ",".join(f"{code}({count})" for code, count in hits) or None


def import_pool(
    pool_dir: Path,
    data_root: Path,
    *,
    summary_name: str = DEFAULT_SUMMARY,
    extra_names: dict[str, str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> PoolResult:
    """执行导入；返回逐只结果（未抛出的单只失败都记在结果里）。"""
    symbols, research_skipped, summary_sha = load_pool_spec(
        pool_dir, summary_name=summary_name, extra_names=extra_names
    )
    listed = {s.code for s in symbols}
    files = sorted(p.name for p in pool_dir.glob("*.qfq.csv"))
    found = {name[: -len(".qfq.csv")] for name in files}

    result = PoolResult(
        pool_label=pool_dir.name,
        summary_name=summary_name,
        summary_sha256=summary_sha,
        n_listed=len(symbols),
        n_files_found=len(found & listed),
        n_unlisted_files=len(found - listed),
        research_skipped=research_skipped,
        dry_run=dry_run,
    )

    metadata_dir = data_root / "metadata"
    for symbol in symbols:
        source_path = pool_dir / f"{symbol.code}.qfq.csv"
        meta_path = metadata_dir / f"{symbol.code}.meta.json"
        name = normalize_display_name(symbol.name) if symbol.name else None

        if not source_path.is_file():
            result.outcomes.append(
                SymbolOutcome(symbol.code, name, "failed", "file_missing")
            )
            continue

        sha = storage_io.file_sha256(source_path)

        # 指纹先于一切：对不上就不读、不写。
        if symbol.sha256 and sha != symbol.sha256:
            result.outcomes.append(
                SymbolOutcome(
                    symbol.code,
                    name,
                    "rejected",
                    f"sha256_mismatch(expected={symbol.sha256[:12]},actual={sha[:12]})",
                    sha256=sha,
                )
            )
            continue

        existing = _existing_metadata(meta_path)
        existing_sha = None
        existing_source_name = None
        if existing is not None:
            source = existing.get("source")
            if isinstance(source, dict):
                existing_sha = source.get("sha256")
                if isinstance(source.get("name"), str):
                    existing_source_name = source["name"]

        if existing is not None and existing_sha == sha and not force:
            has_name = isinstance(existing.get("instrument"), dict) and bool(
                existing["instrument"].get("display_name")
            )
            if name and not has_name:
                if not dry_run:
                    _enrich_name(meta_path, existing, name)
                rows = existing.get("rows")
                result.outcomes.append(
                    SymbolOutcome(
                        symbol.code,
                        name,
                        "would_enrich" if dry_run else "enriched",
                        None,
                        rows.get("processed") if isinstance(rows, dict) else None,
                        sha,
                        existing_source_name,
                    )
                )
            else:
                rows = existing.get("rows")
                result.outcomes.append(
                    SymbolOutcome(
                        symbol.code,
                        name,
                        "unchanged",
                        None,
                        rows.get("processed") if isinstance(rows, dict) else None,
                        sha,
                        existing_source_name,
                    )
                )
            continue

        if existing is not None and existing_sha not in (None, sha) and not force:
            # 同一 symbol 已有另一份数据：不覆盖。覆盖会让用户手上正在用的数据集
            # 在背后换成另一版行情，而界面上没有任何变化提示。
            result.outcomes.append(
                SymbolOutcome(
                    symbol.code,
                    name,
                    "conflict",
                    f"existing_sha256_differs(existing={str(existing_sha)[:12]})",
                    sha256=sha,
                )
            )
            continue

        if dry_run:
            report = validate_daily(load_daily_csv(source_path))
            if report.is_valid:
                result.outcomes.append(
                    SymbolOutcome(symbol.code, name, "would_import", None, int(report.n_rows), sha)
                )
            else:
                detail = "; ".join(
                    f"{i.code}({i.count})" for i in report.issues if i.level == ERROR
                )
                result.outcomes.append(
                    SymbolOutcome(symbol.code, name, "would_reject", detail or "invalid", None, sha)
                )
            continue

        try:
            ingested = ingest_daily_csv(
                source_path,
                symbol=symbol.code,
                data_root=data_root,
                source_name=f"{pool_dir.name}/{source_path.name}",
                instrument_name=name,
            )
        except DataValidationError as exc:
            result.outcomes.append(
                SymbolOutcome(
                    symbol.code,
                    name,
                    "rejected",
                    _codes_from_message(str(exc)) or str(exc),
                    sha256=sha,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单只失败不应中断整批
            result.outcomes.append(
                SymbolOutcome(symbol.code, name, "failed", f"{type(exc).__name__}: {exc}", sha256=sha)
            )
        else:
            result.outcomes.append(
                SymbolOutcome(
                    symbol.code,
                    name,
                    "imported",
                    None,
                    int(ingested.metadata["rows"]["processed"]),
                    sha,
                )
            )

    # 补充名称也可以覆盖「不在池里、但已登记」的数据集（本仓库里手挑的 3 只）。
    # 只补缺失的名称，已有名称一律不动——两处名称不一致时以先写进去的为准，
    # 不在这里做「谁更新算谁对」的判断。
    for code, raw_name in (extra_names or {}).items():
        if code in listed:
            continue
        meta_path = metadata_dir / f"{code}.meta.json"
        existing = _existing_metadata(meta_path)
        if existing is None:
            continue
        name = normalize_display_name(raw_name)
        if not name:
            continue
        instrument = existing.get("instrument")
        if isinstance(instrument, dict) and instrument.get("display_name"):
            continue
        if not dry_run:
            _enrich_name(meta_path, existing, name)
        rows = existing.get("rows")
        result.outcomes.append(
            SymbolOutcome(
                code,
                name,
                "would_enrich" if dry_run else "enriched",
                "named_from_extra_file",
                rows.get("processed") if isinstance(rows, dict) else None,
                None,
                str((existing.get("source") or {}).get("name") or "") or None,
            )
        )

    return result


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


_STATUS_LABEL = {
    "imported": "IMPORT",
    "enriched": "ENRICH",
    "unchanged": "SAME  ",
    "rejected": "REJECT",
    "conflict": "CONFLCT",
    "failed": "FAILED",
    "would_import": "WOULD+",
    "would_enrich": "WOULD~",
    "would_reject": "WOULD-",
}


def _render(result: PoolResult) -> str:
    lines = [
        f"[POOL ] {result.pool_label} · 清单 {result.summary_name}"
        f"（sha256 {str(result.summary_sha256)[:12]}）",
        f"[POOL ] 清单 {result.n_listed} 只 / 找到文件 {result.n_files_found} 只"
        f" / 文件不在清单里 {result.n_unlisted_files} 个",
    ]
    for item in result.outcomes:
        label = _STATUS_LABEL.get(item.status, item.status.upper())
        name = item.name or "-"
        rows = f"{item.rows} 行" if item.rows is not None else ""
        reason = f"  ← {item.reason}" if item.reason else ""
        lines.append(f"[{label}] {item.code} {name:<8} {rows}{reason}")

    counts = result.counts
    order = ["imported", "enriched", "unchanged", "rejected", "conflict", "failed",
             "would_import", "would_enrich", "would_reject"]
    summary = " / ".join(f"{k} {counts[k]}" for k in order if counts.get(k))
    lines.append(f"[SUM  ] {summary or '无变化'}{'（dry-run，未写盘）' if result.dry_run else ''}")

    for item in result.disagreements:
        lines.append(
            f"[WARN ] {item['code']} 研究线={item['research']} / 导入闸门={item['import_gate']}"
            f"  ← {item['reason']}"
        )
    return "\n".join(lines)


def _exit_code(result: PoolResult) -> int:
    if result.disagreements:
        return EXIT_PROBLEM
    if any(o.status in {"failed", "conflict"} for o in result.outcomes):
        return EXIT_PROBLEM
    if not any(
        o.status in {"imported", "enriched", "unchanged", "would_import", "would_enrich"}
        for o in result.outcomes
    ):
        # 一个都没成功也返回非 0：否则「路径写错、目录是空的」看起来和「全都已完成」一样。
        return EXIT_PROBLEM
    return EXIT_OK


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把研究池的日线 CSV 导入为 DeepQuant 可用的数据集",
    )
    parser.add_argument("--pool-dir", required=True, help="研究池目录（含 *.qfq.csv 与池清单）")
    parser.add_argument("--data-root", default=str(REPO_ROOT / "data"), help="数据根目录")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="池清单文件名")
    parser.add_argument("--extra-names", default=None, help="补充名称的 JSON（仅补清单里没有的名称）")
    parser.add_argument("--dry-run", action="store_true", help="只校验与预览，不写任何文件")
    parser.add_argument("--force", action="store_true", help="已存在且指纹不同时也覆盖（默认拒绝）")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    pool_dir = Path(args.pool_dir).expanduser()
    data_root = Path(args.data_root).expanduser()

    if not pool_dir.is_dir():
        print(f"[ERROR] 池目录不存在：{pool_dir}", file=sys.stderr)
        return EXIT_USAGE

    try:
        extra_names = _load_extra_names(Path(args.extra_names) if args.extra_names else None)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 名称补充文件无法使用：{exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        result = import_pool(
            pool_dir,
            data_root,
            summary_name=args.summary,
            extra_names=extra_names,
            dry_run=args.dry_run,
            force=args.force,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 无法读取池清单：{exc}", file=sys.stderr)
        return EXIT_USAGE

    print(_render(result))

    if not args.dry_run:
        manifest_path = data_root / MANIFEST_NAME
        storage_io.write_json(result.as_manifest(), manifest_path)
        print(f"[FILE ] {manifest_path}")

    return _exit_code(result)


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
