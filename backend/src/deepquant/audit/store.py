"""审计工件的追加式存储：不可篡改、可回放、可按会话/时间/操作人检索。

为什么不是再开一张 SQLite 表
----------------------------
``persistence.store`` 保存的是「回测结果」，它允许 ``update``（例如补写
``duration_ms`` 这类派生字段），语义是**当前状态**。审计工件的语义是
**发生过什么**：一旦允许改写，工件就不能再作为证据使用。

因此本模块刻意选择「只能追加的文本文件 + 哈希链」而不是数据库表：

1. 追加式文件的写入路径只有一条（打开→写一行→关闭），没有 ``UPDATE`` 语句，
   也就不存在「代码某处悄悄改了一行」的可能；
2. 每张卡片的摘要包含上一张卡片的摘要，改动任何一张都会让后续所有摘要失配，
   篡改从「改一个字段」变成「重算整条链」；
3. 文本文件可被 ``git``、``diff``、``grep`` 直接检查，不依赖任何运行时。

链式校验能发现什么、不能发现什么（必须说明）
--------------------------------------------
- **能发现**：字段被改、卡片被删、卡片被重排、索引与卡片不一致、尾部被截断；
- **不能发现**：整条链被从头重算并整体替换（攻击者持有全部内容时，任何单向
  哈希链都挡不住）。防这一层需要外部锚定（例如定期把链尾摘要写入独立的
  不可写介质），本模块只提供 ``last_sha256()`` 供外部锚定，不假装自己能做。

时间与顺序
----------
``occurred_at`` 由调用方给出（UTC），``seq`` 由存储层在锁内分配。
不用 ``occurred_at`` 排序：同一毫秒内的两次操作无法靠时间戳定序，
而 ``seq`` 在锁内单调递增，是唯一可靠的顺序依据。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from deepquant.audit.run_card import (
    GENESIS_SHA256,
    AuditSchemaError,
    build_card,
    card_digest,
    now_iso,
    validate_card,
    verify_card,
)

#: 索引文件名。索引是**派生**数据，用于加速检索；核验以卡片本身为准。
INDEX_NAME = "index.jsonl"

#: 卡片分片目录名（按月分片，避免单个文件无限增长）。
SHARDS_DIR = "cards"

#: 单次回放查询的默认上限（防止误把全库读进内存）。
DEFAULT_LIMIT = 1000

#: 锁文件获取的最长等待时间（秒）。
_LOCK_TIMEOUT = 10.0

#: 超过该秒数的锁视为残留（进程崩溃未清理），允许抢占。
_LOCK_STALE_SECONDS = 60.0


class AuditStoreError(RuntimeError):
    """存储层错误（路径不可写、追加与既有链冲突等）。"""


class AuditChainError(AuditStoreError):
    """哈希链断裂（卡片被改动、删除、重排，或索引与卡片不一致）。"""


@dataclass(frozen=True)
class ChainFailure:
    """一处链校验失败。``seq`` 为 ``None`` 表示无法定位到具体序号。"""

    reason: str
    seq: int | None = None
    detail: str = ""
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "seq": self.seq,
            "detail": self.detail,
            "location": self.location,
        }


@dataclass(frozen=True)
class ChainReport:
    """全链校验结果。

    ``ok`` 为 ``True`` 只表示「链条自洽」，**不**表示业务正确、也不表示操作合规。
    """

    ok: bool
    n_cards: int
    n_shards: int
    last_sha256: str
    failures: tuple[ChainFailure, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "n_cards": self.n_cards,
            "n_shards": self.n_shards,
            "last_sha256": self.last_sha256,
            "failures": [f.to_dict() for f in self.failures],
        }


def _shard_name(occurred_at: str) -> str:
    """按 ``occurred_at`` 的 UTC 年月分片；时间不可解析时退化为 ``cards-unknown``。"""
    try:
        dt = datetime.fromisoformat(occurred_at)
    except (TypeError, ValueError):
        return "cards-unknown.jsonl"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return f"cards-{dt.astimezone(UTC):%Y%m}.jsonl"


class AuditStore:
    """追加式审计工件存储。

    目录布局::

        <root>/index.jsonl          每行一条索引（seq, card_id, action, operator, ...）
        <root>/cards/cards-YYYYMM.jsonl   每行一张完整卡片

    索引与分片都只追加，不存在就地修改的代码路径。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        # 缓存键 = (mtime_ns, size)：只用文件名做键会让「别的进程追加了卡片」
        # 之后仍返回旧快照。这会制造最坏的一种错误——校验报告"卡片不存在"，
        # 而实际卡片好好地在磁盘上（本次 T2 取证就被它坑过一次）。
        self._shard_cache: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}

    # ------------------------------------------------------------------ #
    # 路径与锁
    # ------------------------------------------------------------------ #
    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    @property
    def shards_dir(self) -> Path:
        return self.root / SHARDS_DIR

    def _ensure_dirs(self) -> None:
        self.shards_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """跨进程互斥。

        用 ``O_CREAT|O_EXCL`` 创建锁文件：这在 Windows 与 POSIX 上都是原子的，
        不依赖 ``fcntl``（Windows 没有）。锁文件残留超过阈值即视为崩溃遗留并抢占，
        否则一次崩溃会让审计永久写不进去——那会诱使运维直接删库绕过。
        """
        self._ensure_dirs()
        lock_path = self.root / ".append.lock"
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > _LOCK_STALE_SECONDS:
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise AuditStoreError(
                        f"获取审计写入锁超时（{_LOCK_TIMEOUT:.0f}s）：{lock_path}"
                    ) from None
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def read_index(self) -> list[dict[str, Any]]:
        """读取索引（只读，不修复、不补写）。"""
        if not self.index_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.index_path.open("r", encoding="utf-8", newline="") as fh:
            for line_no, line in enumerate(fh, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    entries.append(json.loads(text))
                except json.JSONDecodeError as exc:
                    raise AuditChainError(
                        f"索引第 {line_no} 行无法解析（可能被截断或篡改）：{exc}"
                    ) from exc
        return entries

    def read_shard(self, shard: str) -> list[dict[str, Any]]:
        """读取一个分片的全部卡片。

        缓存以「文件大小 + mtime」为版本键，而不是"读过就不再读"：审计存储是
        多方追加的（每个请求都新建一个 store），若缓存不随文件变化失效，本实例
        会一直看见旧快照，把"别人刚写的卡片"判成不存在。
        """
        path = self.shards_dir / shard
        if not path.exists():
            raise AuditChainError(f"索引指向的分片不存在：{path}")
        stat = path.stat()
        version = (stat.st_mtime_ns, stat.st_size)
        cached = self._shard_cache.get(shard)
        if cached is not None and cached[0] == version:
            return cached[1]

        cards: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            for line_no, line in enumerate(fh, start=1):
                text = line.rstrip("\n")
                if not text.strip():
                    continue
                try:
                    cards.append(json.loads(text))
                except json.JSONDecodeError as exc:
                    raise AuditChainError(
                        f"分片 {shard} 第 {line_no} 行无法解析（可能被截断或篡改）：{exc}"
                    ) from exc
        self._shard_cache[shard] = (version, cards)
        return cards

    def tail(self) -> tuple[int, str, str | None]:
        """返回 ``(下一张卡的 seq, 链尾摘要, 链尾所在分片)``。空库返回创世摘要。"""
        entries = self.read_index()
        if not entries:
            return 1, GENESIS_SHA256, None
        last = entries[-1]
        seq = int(last["seq"])
        return seq + 1, str(last["card_sha256"]), str(last.get("shard") or "")

    def last_sha256(self) -> str:
        """链尾摘要，供外部锚定（本模块不负责外部锚定本身）。"""
        return self.tail()[1]

    def count(self) -> int:
        return len(self.read_index())

    # ------------------------------------------------------------------ #
    # 追加
    # ------------------------------------------------------------------ #
    def append(
        self,
        *,
        action: str,
        operator: str,
        basis: Mapping[str, Any],
        outcome: Mapping[str, Any],
        caveats: Sequence[str],
        subject: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        env: Mapping[str, Any] | None = None,
        session: str | None = None,
        occurred_at: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """追加一张工件并返回它（含 ``seq`` 与 ``card_sha256``）。

        在锁内完成「读链尾 → 构卡 → 封章 → 落盘 → 写索引」，避免并发追加
        出现两个人拿到同一个 ``seq``。任何一步失败都不写索引，保证索引永不领先于卡片。
        """
        occurred_at = occurred_at or now_iso()
        with self._lock():
            seq, prev_sha, _ = self.tail()
            try:
                card = build_card(
                    action=action,
                    operator=operator,
                    occurred_at=occurred_at,
                    basis=basis,
                    outcome=outcome,
                    caveats=caveats,
                    subject=subject,
                    inputs=inputs,
                    env=env,
                    session=session,
                    seq=seq,
                    prev_sha256=prev_sha,
                    extra=extra,
                )
            except AuditSchemaError:
                # 结构不合格不落盘：宁可这次操作没有工件（会被覆盖率检查抓住），
                # 也不要往不可改写的链条里塞一张残缺卡片。
                raise

            shard = _shard_name(occurred_at)
            shard_path = self.shards_dir / shard
            line = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if "\n" in line or "\r" in line:
                raise AuditStoreError("卡片序列化后含换行，无法写入 JSONL")
            with shard_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())

            index_entry = {
                "seq": card["seq"],
                "card_id": card["card_id"],
                "action": card["action"],
                "operator": card["operator"],
                "occurred_at": card["occurred_at"],
                "session": card.get("session"),
                # 冗余两个高频检索维度：T2 要求「拒绝可检索」，若只把原因码放在卡片的
                # outcome 里，检索就得先加载全部分片再过滤，规模一大就退化。索引里多存
                # 两个字符串，换来等值过滤走索引。
                "status": (card.get("outcome") or {}).get("status"),
                "reason_code": (card.get("outcome") or {}).get("reason_code"),
                "shard": shard,
                "card_sha256": card["card_sha256"],
            }
            with self.index_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(
                    json.dumps(index_entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
                fh.flush()
                os.fsync(fh.fileno())

            self._shard_cache.pop(shard, None)
            return card

    # ------------------------------------------------------------------ #
    # 回放查询
    # ------------------------------------------------------------------ #
    def query(
        self,
        *,
        action: str | None = None,
        operator: str | None = None,
        session: str | None = None,
        since: str | None = None,
        until: str | None = None,
        status: str | None = None,
        reason_code: str | None = None,
        limit: int = DEFAULT_LIMIT,
        ascending: bool = True,
    ) -> list[dict[str, Any]]:
        """按操作类型 / 操作人 / 会话 / 时间区间 / 状态 / 原因码检索并回放完整卡片。

        先过滤索引、再按需加载分片：检索成本与「命中数」相关，而不是与全库大小
        相关，这是「单次回放 < 5 秒」能成立的原因。

        ``status`` / ``reason_code`` 走索引字段（老索引行没有这两个键时视为不匹配，
        这是**如实缺省**：那些卡片确实没记录过该字段）。
        """
        entries = self.read_index()
        hits = [
            e
            for e in entries
            if (action is None or e.get("action") == action)
            and (operator is None or e.get("operator") == operator)
            and (session is None or e.get("session") == session)
            and (since is None or str(e.get("occurred_at", "")) >= since)
            and (until is None or str(e.get("occurred_at", "")) <= until)
            and (status is None or e.get("status") == status)
            and (reason_code is None or e.get("reason_code") == reason_code)
        ]
        hits.sort(key=lambda e: int(e["seq"]), reverse=not ascending)
        if limit is not None:
            hits = hits[:limit]

        wanted: dict[str, set[str]] = {}
        for e in hits:
            wanted.setdefault(str(e["shard"]), set()).add(str(e["card_id"]))

        by_id: dict[str, dict[str, Any]] = {}
        for shard, ids in wanted.items():
            for card in self.read_shard(shard):
                if str(card.get("card_id")) in ids:
                    by_id[str(card.get("card_id"))] = card
        return [by_id[str(e["card_id"])] for e in hits if str(e["card_id"]) in by_id]

    def get(self, card_id: str) -> dict[str, Any] | None:
        """按 ``card_id`` 取单张卡片（找不到返回 ``None``，不抛异常）。"""
        for e in self.read_index():
            if e.get("card_id") == card_id:
                for card in self.read_shard(str(e["shard"])):
                    if card.get("card_id") == card_id:
                        return card
        return None

    def iter_all(self, *, ascending: bool = True) -> Iterator[dict[str, Any]]:
        """按链序（``seq``）遍历全部卡片。"""
        for e in sorted(self.read_index(), key=lambda x: int(x["seq"]), reverse=not ascending):
            for card in self.read_shard(str(e["shard"])):
                if card.get("card_id") == e.get("card_id"):
                    yield card
                    break

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def verify(self) -> ChainReport:
        """全链校验：逐张重算摘要、核对前后衔接、核对索引一致、检测孤立卡片。"""
        failures: list[ChainFailure] = []
        try:
            entries = self.read_index()
        except AuditChainError as exc:
            return ChainReport(
                ok=False,
                n_cards=0,
                n_shards=0,
                last_sha256=GENESIS_SHA256,
                failures=(ChainFailure(reason="index_unreadable", detail=str(exc)),),
            )

        expected_prev = GENESIS_SHA256
        expected_seq = 1
        seen_ids: set[str] = set()
        shards_seen: set[str] = set()

        for entry in entries:
            seq = int(entry["seq"])
            shard = str(entry["shard"])
            card_id = str(entry["card_id"])
            shards_seen.add(shard)

            if seq != expected_seq:
                failures.append(
                    ChainFailure(
                        reason="seq_gap",
                        seq=seq,
                        detail=f"期望 seq={expected_seq}，实际 {seq}（有卡片被删除或索引被改写）",
                        location=INDEX_NAME,
                    )
                )

            try:
                cards = self.read_shard(shard)
            except AuditChainError as exc:
                failures.append(
                    ChainFailure(reason="shard_unreadable", seq=seq, detail=str(exc), location=shard)
                )
                expected_seq = seq + 1
                continue

            card = next((c for c in cards if str(c.get("card_id")) == card_id), None)
            if card is None:
                failures.append(
                    ChainFailure(
                        reason="card_missing",
                        seq=seq,
                        detail=f"索引记录了 card_id={card_id}，但分片中找不到",
                        location=shard,
                    )
                )
                expected_seq = seq + 1
                continue

            ok, message = verify_card(card)
            if not ok:
                failures.append(
                    ChainFailure(reason="digest_mismatch", seq=seq, detail=message, location=shard)
                )

            if card.get("prev_sha256") != expected_prev:
                failures.append(
                    ChainFailure(
                        reason="prev_mismatch",
                        seq=seq,
                        detail=(
                            f"prev_sha256 与上一张卡摘要不符（期望 {str(expected_prev)[:16]}…，"
                            f"实际 {str(card.get('prev_sha256'))[:16]}…）"
                        ),
                        location=shard,
                    )
                )

            if str(card.get("card_sha256")) != str(entry.get("card_sha256")):
                failures.append(
                    ChainFailure(
                        reason="index_digest_mismatch",
                        seq=seq,
                        detail="索引记录的摘要与卡片自身摘要不一致",
                        location=INDEX_NAME,
                    )
                )

            expected_prev = str(card.get("card_sha256"))
            expected_seq = seq + 1
            seen_ids.add(card_id)

        # 孤立卡片：分片里有、索引里没有（说明索引被删行，或卡片被外部追加）
        for shard_path in sorted(self.shards_dir.glob("cards-*.jsonl")):
            shard = shard_path.name
            try:
                cards = self.read_shard(shard)
            except AuditChainError as exc:
                failures.append(
                    ChainFailure(reason="shard_unreadable", detail=str(exc), location=shard)
                )
                continue
            for card in cards:
                if str(card.get("card_id")) not in seen_ids:
                    failures.append(
                        ChainFailure(
                            reason="orphan_card",
                            seq=card.get("seq"),
                            detail=f"卡片 {card.get('card_id')} 不在索引中",
                            location=shard,
                        )
                    )

        last_sha = expected_prev
        return ChainReport(
            ok=not failures,
            n_cards=len(entries),
            n_shards=len(shards_seen),
            last_sha256=last_sha,
            failures=tuple(failures),
        )


def open_store(root: str | Path | None) -> AuditStore | None:
    """按路径打开存储；``None`` 或空串表示未启用，返回 ``None``（调用方据此跳过写入）。"""
    if root is None:
        return None
    text = str(root).strip()
    if not text:
        return None
    return AuditStore(Path(text))


def verify_shard_digests(store: AuditStore) -> dict[str, str]:
    """逐个分片计算文件级摘要，供外部锚定或人工比对（只读）。"""
    import hashlib

    out: dict[str, str] = {}
    for path in sorted(store.shards_dir.glob("cards-*.jsonl")):
        out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def chain_fingerprint(store: AuditStore) -> dict[str, Any]:
    """导出链条指纹：链尾摘要 + 卡片数 + 各分片文件摘要。

    用于把「某一时刻的审计状态」交给外部记录（例如写入发布单、邮件、git tag），
    这样即使有人重算整条链，也无法同时改掉外部锚点。
    """
    report = store.verify()
    return {
        "n_cards": report.n_cards,
        "n_shards": report.n_shards,
        "last_sha256": report.last_sha256,
        "shards": verify_shard_digests(store),
        "verified_ok": report.ok,
    }


__all__ = [
    "AuditChainError",
    "AuditStore",
    "AuditStoreError",
    "ChainFailure",
    "ChainReport",
    "DEFAULT_LIMIT",
    "INDEX_NAME",
    "SHARDS_DIR",
    "chain_fingerprint",
    "open_store",
    "validate_card",
    "card_digest",
    "verify_shard_digests",
]
