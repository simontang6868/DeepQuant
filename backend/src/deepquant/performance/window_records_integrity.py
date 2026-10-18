"""W1 结果文件的完整性层：跨实验加载 + 篡改可检测（只读，不改既有落盘格式）。

为什么需要单独一层
------------------
``window_records.write_records`` 负责「把一次实验写成一等公民的行」，
``read_records`` 负责「把一行还原成对象并校验表头」。两者之外还有两个缺口：

1. **跨实验汇总没有入口**。Gate 1 与阶段 E 要同时汇总多个实验（不同标的、
   不同策略、不同费率档），若各自手写 glob + 拼接，最容易犯的错是
   「按目录顺序 zip」和「静默丢弃失败窗口」——两者都会让分母悄悄变小，
   进而让正收益率虚高（B20 的直接教训）。
2. **内容被改过无法发现**。``read_records`` 只校验表头，把某个
   ``strategy_cumulative_return`` 从 ``-0.05`` 改成 ``0.05`` 读回来毫无察觉。
   结果文件要跨会话、跨人传递，没有内容印记就无法确认「这份 CSV 就是当初
   跑出来的那份」。

本模块只做加法：不修改 ``windows.csv`` 的格式，不改变既有函数行为，
额外落一个 ``windows.manifest.json`` 记录内容摘要。

manifest 记什么、为什么
-----------------------
只有整个文件的 sha256 不够用：它能说明「变了」，但不能说明「变了什么」。
因此同时记录可在不重跑的情况下重算的几个量：

- ``csv_sha256`` / ``csv_bytes``：文件级指纹，检测任意字节改动；
- ``n_rows``：行数，检测「少了几行失败窗口」这类静默丢行；
- ``non_null_counts``：逐列非空计数。整列变空是最隐蔽的事故（导表时丢列、
  改字段名后全空），文件 sha 会变但人看不出哪列出了问题；
- ``status_counts``：状态分布，直接对上「失败窗口有没有被丢掉」这个问题；
- ``window_keys_sha256``：窗口键序列的摘要。用它而不是键列表本身：
  既能检测「窗口被替换/重排」，又不至于让 manifest 变成第二份数据。

所有量都是**内容确定性**的——同一批记录两次生成 manifest 必须逐字节一致，
否则无法用哈希证明可复现（这也是 ``run_at`` 不允许进 CSV 的原因）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from deepquant.performance.window_records import (
    CSV_NAME,
    SCHEMA_VERSION,
    WindowRecord,
    read_records,
    summarize_records,
)

MANIFEST_NAME = "windows.manifest.json"
MANIFEST_VERSION = "w1-manifest-v1"


class IntegrityError(ValueError):
    """结果文件与其 manifest 不一致——数据来源不可信，必须人工介入。"""


# --------------------------------------------------------------------------- #
# 内容摘要
# --------------------------------------------------------------------------- #


def record_columns() -> list[str]:
    """``WindowRecord`` 的字段顺序（与 CSV 列一一对应，不依赖私有函数）。"""
    return [f.name for f in fields(WindowRecord)]


def _csv_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _keys_digest(records: Sequence[WindowRecord]) -> str:
    """窗口键序列的摘要（按给定顺序，顺序变了摘要就变）。"""
    joined = "\n".join(r.window_key for r in records)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _non_null_counts(records: Sequence[WindowRecord]) -> dict[str, int]:
    counts = {name: 0 for name in record_columns()}
    for r in records:
        for name in counts:
            if getattr(r, name) is not None:
                counts[name] += 1
    return counts


def _status_counts(records: Sequence[WindowRecord]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        out[r.status] = out.get(r.status, 0) + 1
    return dict(sorted(out.items()))


def build_manifest(records: Sequence[WindowRecord], csv_path: str | Path) -> dict[str, object]:
    """由「已落盘的 CSV」+「内存中的记录」共同生成 manifest。

    两者都要：CSV 提供字节级指纹，记录提供可读的分布事实。
    如果二者本就不一致（调用方传错了配对），这里直接拒绝，
    不能让一份与 CSV 不符的 manifest 落盘。
    """
    if not records:
        raise ValueError("records 为空：不为空结果生成 manifest")

    records = list(records)   # 后面要遍历两次，避免一次性可迭代被消费掉

    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV 不存在，无法生成 manifest：{path}")

    exp_ids = sorted({r.experiment_id for r in records})
    if len(exp_ids) != 1:
        raise ValueError(f"manifest 只能描述单个实验，收到 {exp_ids}")

    # manifest 是「对某份 CSV 的描述」。若传入的记录与磁盘内容不是同一批，
    # 生成出来的就是一份描述 A、实际配 B 的假证据——比没有 manifest 更危险。
    # 按 window_key 对齐比较（write_records 落盘时会按窗口序号排序，不能用位置）。
    on_disk = {r.window_key: r for r in read_records(path)}
    for r in records:
        disk = on_disk.get(r.window_key)
        if disk is None:
            raise ValueError(f"窗口 {r.window_key} 在 CSV 中不存在：记录与文件不是同一批")
        if disk != r:
            raise ValueError(
                f"窗口 {r.window_key} 的内存记录与 CSV 内容不一致："
                "拒绝为一份与记录不符的文件生成指纹"
            )
    if len(on_disk) != len({r.window_key for r in records}):
        raise ValueError("记录的窗口键数量与 CSV 不符：manifest 会描述错对象")

    summary = summarize_records(records)
    return {
        "manifest_version": MANIFEST_VERSION,
        "schema_version": SCHEMA_VERSION,
        "csv_name": path.name,
        "csv_sha256": _csv_digest(path),
        "csv_bytes": path.stat().st_size,
        "n_rows": len(records),
        "experiment_id": exp_ids[0],
        "columns": record_columns(),
        "non_null_counts": _non_null_counts(records),
        "status_counts": summary["status_counts"],
        "n_paired": summary["n_paired"],
        "window_keys_sha256": _keys_digest(records),
    }


def write_manifest(records: Sequence[WindowRecord], out_dir: str | Path) -> Path:
    """把 manifest 写到 ``<out_dir>/<experiment_id>/windows.manifest.json``。

    与 ``write_records`` 同样的**拒覆盖**语义：内容相同的重复写是幂等的，
    内容不同则报错——manifest 被悄悄替换等于抹掉「原始结果长什么样」的证据。
    """
    if not records:
        raise ValueError("records 为空：不为空结果生成 manifest")
    exp_id = records[0].experiment_id
    exp_dir = Path(out_dir) / exp_id
    csv_path = exp_dir / CSV_NAME
    manifest = build_manifest(records, csv_path)
    payload = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    target = exp_dir / MANIFEST_NAME
    if target.exists():
        if target.read_text(encoding="utf-8") == payload:
            return target
        raise RuntimeError(
            f"{target} 已存在且内容不同：拒绝覆盖原始结果指纹。"
            "若确需替换，请先人工确认并移走旧文件。"
        )
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)
    return target


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ManifestVerdict:
    experiment_dir: Path
    ok: bool
    verified: bool          # 是否真的做了内容校验（False = 没有 manifest，只做了结构校验）
    checks: tuple[CheckResult, ...]

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def explain(self) -> str:
        if self.ok:
            return f"{self.experiment_dir.name}: OK（{len(self.checks)} 项检查通过）"
        lines = [f"{self.experiment_dir.name}: FAILED"]
        for c in self.failures:
            lines.append(f"  - {c.name}: {c.detail}")
        return "\n".join(lines)


def _diff_dict(expected: Mapping[str, object], actual: Mapping[str, object]) -> str:
    """把两本计数表的差异写成人能读的一句，而不是「不一致」。"""
    keys = sorted(set(expected) | set(actual))
    diffs = [
        f"{k}: {expected.get(k, '<缺>')} -> {actual.get(k, '<缺>')}"
        for k in keys
        if expected.get(k) != actual.get(k)
    ]
    return "; ".join(diffs) if diffs else "无差异"


def verify_experiment(exp_dir: str | Path, *, require_manifest: bool = True) -> ManifestVerdict:
    """校验单个实验目录：结构可读 + 内容与 manifest 一致。

    不重跑回测，只重算 manifest 里那些「可从 CSV 重新得到的量」。
    """
    exp_dir = Path(exp_dir)
    checks: list[CheckResult] = []

    csv_path = exp_dir / CSV_NAME
    if not csv_path.exists():
        return ManifestVerdict(
            experiment_dir=exp_dir, ok=False, verified=False,
            checks=(CheckResult("csv_exists", False, f"缺少 {csv_path}"),),
        )
    checks.append(CheckResult("csv_exists", True))

    try:
        records = read_records(csv_path)
    except Exception as exc:  # 表头不符 / 类型无法解析
        return ManifestVerdict(
            experiment_dir=exp_dir, ok=False, verified=False,
            checks=tuple(checks) + (CheckResult("schema_readable", False, str(exc)),),
        )
    checks.append(CheckResult("schema_readable", True))

    exp_ids = sorted({r.experiment_id for r in records})
    single = len(exp_ids) == 1
    checks.append(CheckResult(
        "single_experiment",
        single,
        "" if single else f"目录内混入多个实验：{exp_ids}",
    ))
    dir_match = single and exp_ids[0] == exp_dir.name
    checks.append(CheckResult(
        "dir_matches_experiment_id",
        dir_match,
        "" if dir_match else f"目录名 {exp_dir.name} 与 experiment_id {exp_ids} 不符",
    ))
    schema_ok = all(r.schema_version == SCHEMA_VERSION for r in records)
    checks.append(CheckResult(
        "row_schema_version",
        schema_ok,
        "" if schema_ok else "存在 schema_version 与当前版本不符的行（跨版本混用）",
    ))

    manifest_path = exp_dir / MANIFEST_NAME
    if not manifest_path.exists():
        if require_manifest:
            checks.append(CheckResult("manifest_exists", False, f"缺少 {MANIFEST_NAME}"))
            return ManifestVerdict(exp_dir, False, False, tuple(checks))
        checks.append(CheckResult("manifest_exists", True, "缺失，按调用方要求跳过内容校验"))
        return ManifestVerdict(exp_dir, all(c.ok for c in checks), False, tuple(checks))

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        checks.append(CheckResult("manifest_exists", False, f"无法解析：{exc}"))
        return ManifestVerdict(exp_dir, False, False, tuple(checks))
    checks.append(CheckResult("manifest_exists", True))

    mv_ok = manifest.get("manifest_version") == MANIFEST_VERSION
    checks.append(CheckResult(
        "manifest_version", mv_ok,
        "" if mv_ok else f"期望 {MANIFEST_VERSION}，实到 {manifest.get('manifest_version')}",
    ))

    ms_ok = manifest.get("schema_version") == SCHEMA_VERSION
    checks.append(CheckResult(
        "manifest_schema_version", ms_ok,
        "" if ms_ok else f"manifest 记录 {manifest.get('schema_version')}，当前 {SCHEMA_VERSION}",
    ))

    cols_ok = manifest.get("columns") == record_columns()
    checks.append(CheckResult(
        "columns_match", cols_ok,
        "" if cols_ok else "manifest 的列定义与当前 schema 不一致",
    ))

    if mv_ok and ms_ok:
        digest = _csv_digest(csv_path)
        d_ok = manifest.get("csv_sha256") == digest
        checks.append(CheckResult(
            "csv_sha256", d_ok,
            "" if d_ok else f"内容已被改动：manifest {manifest.get('csv_sha256')} / 实到 {digest}",
        ))
        size_ok = manifest.get("csv_bytes") == csv_path.stat().st_size
        checks.append(CheckResult(
            "csv_bytes", size_ok,
            "" if size_ok else f"字节数不符：manifest {manifest.get('csv_bytes')} / 实到 {csv_path.stat().st_size}",
        ))

        rows_ok = manifest.get("n_rows") == len(records)
        checks.append(CheckResult(
            "n_rows", rows_ok,
            "" if rows_ok else f"行数不符：manifest {manifest.get('n_rows')} / 实到 {len(records)}",
        ))

        exp_ok = manifest.get("experiment_id") == (exp_ids[0] if exp_ids else None)
        checks.append(CheckResult(
            "experiment_id", exp_ok,
            "" if exp_ok else f"experiment_id 不符：manifest {manifest.get('experiment_id')} / 实到 {exp_ids}",
        ))

        nn_expected = manifest.get("non_null_counts") or {}
        nn_actual = _non_null_counts(records)
        nn_ok = nn_expected == nn_actual
        checks.append(CheckResult(
            "non_null_counts", nn_ok,
            "" if nn_ok else _diff_dict(nn_expected, nn_actual),
        ))

        st_expected = manifest.get("status_counts") or {}
        st_actual = _status_counts(records)
        st_ok = st_expected == st_actual
        checks.append(CheckResult(
            "status_counts", st_ok,
            "" if st_ok else _diff_dict(st_expected, st_actual),
        ))

        keys_ok = manifest.get("window_keys_sha256") == _keys_digest(records)
        checks.append(CheckResult(
            "window_keys", keys_ok,
            "" if keys_ok else "窗口键序列变化：可能被替换、重排或删除了某几行",
        ))

    return ManifestVerdict(exp_dir, all(c.ok for c in checks), True, tuple(checks))


def load_experiment(exp_dir: str | Path, *, require_manifest: bool = True) -> list[WindowRecord]:
    """读取并校验一个实验目录；校验不过直接抛错，绝不返回可疑数据。"""
    verdict = verify_experiment(exp_dir, require_manifest=require_manifest)
    if not verdict.ok:
        raise IntegrityError(verdict.explain())
    return read_records(Path(exp_dir) / CSV_NAME)


# --------------------------------------------------------------------------- #
# 跨实验加载与分组汇总
# --------------------------------------------------------------------------- #


def load_experiments(
    root: str | Path, *, require_manifest: bool = True
) -> dict[str, list[WindowRecord]]:
    """扫描 ``root`` 下的所有实验目录，逐个校验后返回 ``{experiment_id: records}``。

    拒绝的两种情形（都会让汇总失去意义）：
    - 同一 ``experiment_id`` 出现在两个目录 → 来源不明，无法判断以哪份为准；
    - 任一目录校验失败 → 整批失败。不做「跳过坏的继续汇总」，
      那等于把「有一份结果不可信」这个事实从报告里抹掉。
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"实验根目录不存在：{root}")

    exp_dirs = sorted(
        p for p in root.iterdir()
        if p.is_dir() and (p / CSV_NAME).exists()
    )
    if not exp_dirs:
        raise ValueError(f"{root} 下没有任何含 {CSV_NAME} 的实验目录")

    out: dict[str, list[WindowRecord]] = {}
    problems: list[str] = []
    for d in exp_dirs:
        verdict = verify_experiment(d, require_manifest=require_manifest)
        if not verdict.ok:
            problems.append(verdict.explain())
            continue
        records = read_records(d / CSV_NAME)
        exp_id = records[0].experiment_id
        if exp_id in out:
            problems.append(
                f"{exp_id} 同时出现在两个目录：已读过一份，又遇到 {d.name}"
            )
            continue
        out[exp_id] = records

    if problems:
        raise IntegrityError("跨实验加载失败：\n" + "\n".join(problems))
    return out


def group_records(
    records: Iterable[WindowRecord], key_fields: Sequence[str]
) -> dict[tuple[object, ...], list[WindowRecord]]:
    """按若干字段分组，并**强制检查分母守恒**。

    分组的唯一硬要求：``sum(len(组)) == len(records)``。少了任何一行都说明
    有记录被静默丢弃（典型来源：``key_fields`` 里某个字段为 ``None`` 时
    被过滤掉）。这里不做过滤，``None`` 原样作为分组键的一部分。
    """
    names = list(key_fields)
    if not names:
        raise ValueError("key_fields 不能为空：否则分组无意义")
    valid = set(record_columns())
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(f"未知分组字段：{unknown}")

    seq = list(records)
    out: dict[tuple[object, ...], list[WindowRecord]] = {}
    for r in seq:
        key = tuple(getattr(r, n) for n in names)
        out.setdefault(key, []).append(r)

    total = sum(len(v) for v in out.values())
    if total != len(seq):
        raise IntegrityError(
            f"分组后记录数不守恒：输入 {len(seq)} 行，分组后 {total} 行——有记录被丢弃"
        )
    return out


def summarize_by(
    records: Iterable[WindowRecord], key_fields: Sequence[str]
) -> list[dict[str, object]]:
    """按字段分组后逐组 ``summarize_records``，并附上守恒校验结果。

    每组结果里额外放 ``group``（分组键的字典形式）与 ``n_windows``，
    方便直接喂给报告表格。**失败窗口留在各自的组里**，不因为 status 被剔除。
    """
    names = list(key_fields)
    groups = group_records(records, names)
    out: list[dict[str, object]] = []
    for key, rows in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        summary = summarize_records(rows)
        summary["group"] = dict(zip(names, key))
        summary["n_windows"] = len(rows)
        out.append(summary)

    total = sum(int(s["n_windows"]) for s in out)
    if total != sum(len(v) for v in groups.values()):
        raise IntegrityError("分组汇总的总窗口数不守恒")
    return out
