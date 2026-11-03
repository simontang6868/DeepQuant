"""训练/留出切分：按会话 id 的哈希切分，与时间无关。

为什么不用时间切分
------------------
本语料是**一次构建**的引导语料，所有会话的真实时间几乎重合。按时间切会让
「训练」与「留出」的会话类型分布完全一样，却对外宣称是前向验证——那是拿
「同一天的上午训练、下午测试」当样本外。

按 id 哈希切分至少保证：同一条会话不会同时出现在两边，且切分可被任何人用
同一 ``seed`` 复现。它**不能**替代真正的前瞻验证，这一点写在验收文件的缺口里。
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from deepquant.shadow.sequences import OperationSequence

#: 留出比例。
HOLDOUT_FRACTION = 0.3


def _holdout_of(sequence_id: str, *, seed: int, fraction: float) -> bool:
    digest = hashlib.sha256(f"{seed}:{sequence_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF < fraction


def split_sequences(
    sequences: Sequence[OperationSequence],
    *,
    holdout_fraction: float = HOLDOUT_FRACTION,
    seed: int = 20261027,
) -> tuple[list[OperationSequence], list[OperationSequence]]:
    """返回 ``(训练集, 留出集)``。排序后切分，保证结果与输入顺序无关。"""
    ordered = sorted(sequences, key=lambda item: item.sequence_id)
    train: list[OperationSequence] = []
    holdout: list[OperationSequence] = []
    for sequence in ordered:
        if _holdout_of(sequence.sequence_id, seed=seed, fraction=holdout_fraction):
            holdout.append(sequence)
        else:
            train.append(sequence)
    return train, holdout
