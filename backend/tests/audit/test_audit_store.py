"""追加式审计存储测试：链校验、篡改检测、回放查询与耗时。

覆盖《推进计划》T1 的两条硬性要求：
- 工件不可篡改（追加式存储 + 哈希校验）：改字段、删中间卡、重排、删索引均可检出；
- 任一历史工件的回放时间 < 5 秒（本文件用 200 张卡实测并断言上界）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquant.audit.store import (
    INDEX_NAME,
    SHARDS_DIR,
    AuditChainError,
    AuditStore,
    chain_fingerprint,
)

AT = "2026-10-27T02:00:00.000000+00:00"


def _append(store: AuditStore, *, seq_hint: int | None = None, operator: str = "ops", action: str = "backtest.run", session: str | None = "s1", occurred_at: str = AT):
    return store.append(
        action=action,
        operator=operator,
        basis={"rule": "r1"},
        outcome={"status": "ok", "n": seq_hint},
        caveats=["c1"],
        subject={"symbol": "TEST01"},
        session=session,
        occurred_at=occurred_at,
    )


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    return AuditStore(tmp_path / "audit")


def _rewrite_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _shard_file(store: AuditStore, name: str | None = None) -> Path:
    files = sorted((store.root / SHARDS_DIR).glob("cards-*.jsonl"))
    assert files, "没有任何分片文件"
    if name is None:
        return files[-1]
    return next(f for f in files if f.name == name)


# --------------------------------------------------------------------------- #
# 基本写入与链条
# --------------------------------------------------------------------------- #
def test_append_assigns_monotonic_seq_and_chain(store: AuditStore) -> None:
    first = _append(store, seq_hint=1)
    second = _append(store, seq_hint=2)
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["prev_sha256"] == "0" * 64
    assert second["prev_sha256"] == first["card_sha256"]
    report = store.verify()
    assert report.ok is True
    assert report.n_cards == 2
    assert report.last_sha256 == second["card_sha256"]


def test_index_matches_cards(store: AuditStore) -> None:
    card = _append(store)
    index = store.read_index()
    assert len(index) == 1
    assert index[0]["card_id"] == card["card_id"]
    assert index[0]["card_sha256"] == card["card_sha256"]
    assert index[0]["shard"] == _shard_file(store).name
    assert store.get(card["card_id"]) == card


def test_files_are_lf_only(store: AuditStore) -> None:
    """工件落盘必须是 LF（项目历史上 CRLF 曾让摘要不可复核）。"""
    _append(store)
    raw = _shard_file(store).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert b"\r\n" not in store.index_path.read_bytes()


# --------------------------------------------------------------------------- #
# 篡改检测
# --------------------------------------------------------------------------- #
def test_detects_field_tampering(store: AuditStore) -> None:
    _append(store, seq_hint=1)
    _append(store, seq_hint=2)
    path = _shard_file(store)
    lines = path.read_text(encoding="utf-8").splitlines()
    card = json.loads(lines[0])
    card["outcome"]["status"] = "rejected"  # 把成功改成拒绝
    lines[0] = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _rewrite_lines(path, lines)

    report = store.verify()
    assert report.ok is False
    reasons = {f.reason for f in report.failures}
    assert "digest_mismatch" in reasons
    assert report.failures[0].seq == 1


def test_detects_deleted_middle_card(store: AuditStore) -> None:
    _append(store, seq_hint=1)
    _append(store, seq_hint=2)
    _append(store, seq_hint=3)
    path = _shard_file(store)
    lines = path.read_text(encoding="utf-8").splitlines()
    _rewrite_lines(path, [lines[0], lines[2]])  # 删掉第 2 张

    report = store.verify()
    assert report.ok is False
    reasons = {f.reason for f in report.failures}
    assert "card_missing" in reasons or "prev_mismatch" in reasons


def test_shard_line_order_is_irrelevant_but_index_order_is_not(store: AuditStore) -> None:
    """分片内行序不影响校验（顺序由索引定义），索引行序被打乱必须被检出。

    这是一个重要的设计边界：若把「分片行序」当作链条顺序，任何按内容重排文件的
    工具（备份还原、去重脚本）都会让审计无辜报错，久而久之运维就会习惯性忽略报警。
    """
    _append(store, seq_hint=1)
    _append(store, seq_hint=2)

    path = _shard_file(store)
    lines = path.read_text(encoding="utf-8").splitlines()
    _rewrite_lines(path, [lines[1], lines[0]])
    assert store.verify().ok is True, "分片内重排不应破坏链（顺序由索引定义）"

    index_lines = store.index_path.read_text(encoding="utf-8").splitlines()
    _rewrite_lines(store.index_path, [index_lines[1], index_lines[0]])
    report = store.verify()
    assert report.ok is False
    assert {f.reason for f in report.failures} & {"seq_gap", "prev_mismatch"}


def test_detects_orphan_card_when_index_line_removed(store: AuditStore) -> None:
    """索引被删一行时，卡片仍在分片里——必须报「孤立卡片」，而不是当作没发生。"""
    _append(store, seq_hint=1)
    _append(store, seq_hint=2)
    lines = store.index_path.read_text(encoding="utf-8").splitlines()
    _rewrite_lines(store.index_path, lines[:1])

    report = store.verify()
    assert report.ok is False
    assert "orphan_card" in {f.reason for f in report.failures}


def test_detects_truncated_shard_line(store: AuditStore) -> None:
    _append(store, seq_hint=1)
    path = _shard_file(store)
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw[: len(raw) // 2], encoding="utf-8", newline="\n")

    with pytest.raises(AuditChainError):
        store.read_shard(path.name)


# --------------------------------------------------------------------------- #
# 回放查询
# --------------------------------------------------------------------------- #
def test_query_filters_and_order(store: AuditStore) -> None:
    _append(store, seq_hint=1, action="backtest.run", operator="a", session="s1", occurred_at="2026-10-01T00:00:00+00:00")
    _append(store, seq_hint=2, action="dataset.list", operator="b", session="s1", occurred_at="2026-10-02T00:00:00+00:00")
    _append(store, seq_hint=3, action="backtest.run", operator="b", session="s2", occurred_at="2026-10-03T00:00:00+00:00")

    assert [c["seq"] for c in store.query(action="backtest.run")] == [1, 3]
    assert [c["seq"] for c in store.query(operator="b")] == [2, 3]
    assert [c["seq"] for c in store.query(session="s1")] == [1, 2]
    assert [c["seq"] for c in store.query(since="2026-10-02T00:00:00+00:00")] == [2, 3]
    assert [c["seq"] for c in store.query(until="2026-10-02T00:00:00+00:00")] == [1, 2]
    assert [c["seq"] for c in store.query(ascending=False)] == [3, 2, 1]
    assert [c["seq"] for c in store.query(limit=2)] == [1, 2]


def test_replay_of_single_card_is_under_five_seconds(store: AuditStore) -> None:
    """单张回放耗时上界（T1 验收：< 5 秒）。

    200 张卡规模下实测值写在断言消息里，便于后续回归时看到真实数字，
    而不是只知道「没超时」。
    """
    import time

    for i in range(200):
        _append(store, seq_hint=i, occurred_at=AT)
    cards = store.query(action="backtest.run", limit=10)
    assert len(cards) == 10

    started = time.perf_counter()
    target = store.get(cards[-1]["card_id"])
    elapsed = time.perf_counter() - started
    assert target is not None
    assert elapsed < 5.0, f"单张回放耗时 {elapsed:.3f}s，超过 5 秒上限"


def test_query_over_large_store_is_under_five_seconds(store: AuditStore) -> None:
    """整体查询耗时上界：200 张卡、按操作类型过滤。"""
    import time

    for i in range(200):
        _append(store, seq_hint=i, action="backtest.run" if i % 2 == 0 else "dataset.list")
    started = time.perf_counter()
    cards = store.query(action="backtest.run", limit=1000)
    elapsed = time.perf_counter() - started
    assert len(cards) == 100
    assert elapsed < 5.0, f"查询耗时 {elapsed:.3f}s，超过 5 秒上限"


# --------------------------------------------------------------------------- #
# 指纹
# --------------------------------------------------------------------------- #
def test_chain_fingerprint_reports_shard_digests(store: AuditStore) -> None:
    _append(store, seq_hint=1, occurred_at="2026-10-01T00:00:00+00:00")
    _append(store, seq_hint=2, occurred_at="2026-11-01T00:00:00+00:00")
    fp = chain_fingerprint(store)
    assert fp["verified_ok"] is True
    assert fp["n_cards"] == 2
    assert set(fp["shards"]) == {"cards-202610.jsonl", "cards-202611.jsonl"}
    assert all(len(v) == 64 for v in fp["shards"].values())


def test_index_file_name_is_stable(store: AuditStore) -> None:
    """索引文件名是外部工具（grep/备份脚本）的约定，改名即破坏兼容。"""
    _append(store)
    assert (store.root / INDEX_NAME).exists()


def test_cache_sees_cards_written_by_another_instance(store: AuditStore) -> None:
    """多方追加：另一个实例写入的卡片，本实例必须能看见。

    T2 取证时踩过这个坑——缓存只按文件名做键，导致校验报「卡片不存在」，
    而卡片其实好好地在磁盘上。这类假阴性会让校验被当成"误报"而被忽略，
    比没有校验更危险。
    """
    _append(store, seq_hint=1)
    assert store.verify().ok is True

    other = AuditStore(store.root)
    _append(other, seq_hint=2)

    cards = store.read_shard(_shard_file(store).name)
    assert len(cards) == 2, "本实例仍在读旧快照"
    assert store.verify().ok is True
    assert len(store.query(action="backtest.run")) == 2


def test_query_filters_by_reason_code(store: AuditStore) -> None:
    """T2 要求「拒绝可在工件中检索」：按原因码等值过滤走索引字段。"""
    store.append(
        action="backtest.run",
        operator="ops",
        basis={"rule": "x"},
        outcome={"status": "rejected", "reason_code": "identity_missing", "status_code": 403},
        caveats=["c"],
        session="s",
        occurred_at=AT,
    )
    store.append(
        action="backtest.run",
        operator="ops",
        basis={"rule": "x"},
        outcome={"status": "ok", "status_code": 200},
        caveats=["c"],
        session="s",
        occurred_at=AT,
    )
    assert len(store.query(reason_code="identity_missing")) == 1
    assert len(store.query(status="ok")) == 1
    assert len(store.query(status="rejected")) == 1
    # 老索引行没有该字段时视为不匹配（如实缺省，而不是"全都算命中"）
    assert len(store.query(reason_code="no_such_code")) == 0
