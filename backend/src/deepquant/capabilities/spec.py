"""能力契约的数据结构：把「一个业务动作对外承诺了什么」写成可校验的对象。

为什么要有这一层
----------------
本仓库已经有三套「清单」，但它们各自回答不同的问题，都不能回答
「这个业务动作对外承诺的输入输出是什么、谁能调用、能不能关掉」：

===================  ==========================================  ==========================
既有清单              回答的问题                                  失效方式
===================  ==========================================  ==========================
``api/registry.py``   有哪些**策略插件**、参数类型与默认值            与 HTTP 无关，管不到接口
``audit/operations``  有哪些**核心操作**需要留痕（覆盖率分母）        只管记不记，不管能不能调
``risk/checkpoints``  一次请求**能不能做**（身份/限额/状态/字段）      不管"这个能力是否开放"
===================  ==========================================  ==========================

T4 要补齐的是**能力层**：一个 ``CapabilitySpec`` 同时声明传输契约（方法/路径）、
数据契约（输入/输出模型）、前置条件、风控标签、版本与生命周期。它是唯一一处
「改了就要升版本」的地方。

为什么声明必须能被代码验证
--------------------------
手写声明最大的风险不是写错，而是**写完之后代码改了、声明没改**。这种漂移不会
报错：接口照常工作，声明继续声称旧契约，直到有人按声明去对接才发现对不上。
因此本模块只负责"声明长什么样"，与真实装配的对账放在 ``contract.py``——
那边会把声明和 ``app`` 的真实路由、``OPERATION_ROUTES``、风控必填字段逐项比对，
任一项不一致即失败。

摘要（digest）覆盖什么
----------------------
``contract_digest()`` 只覆盖**行为相关**字段：传输方式、方法、路径、操作类型、
输入/输出 schema、必填字段、前置条件、风控标签。标题与说明文字不进摘要——
改错别字不该逼人升版本，而改一个风控标签必须升版本（调用方可能据此做准入判断）。

版本语义
--------
``major.minor.patch``：

- **major**：不兼容变更（删字段、改类型、改路径、改必填）。调用方必须改代码；
- **minor**：向后兼容的新增（加可选字段、加输出字段）；
- **patch**：实现修复，契约不变。

服务端只对**同一 major** 的请求放行（见 ``registry.check_version``）：同一 major
内承诺向后兼容，跨 major 一律拒绝而不是"尽力兼容"——静默降级会让调用方以为
拿到的是它要的契约。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

#: 能力标识：点分层级，便于按域授权与按前缀批量禁用（如 ``backtest.*``）。
CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$")

#: 严格三段式版本号。刻意不接受 ``1.0`` / ``v1`` / ``1.0.0-rc1``：
#: 预发布版本在"能不能放行"的判定上语义模糊，而本系统的判定必须二值。
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

TRANSPORT_HTTP = "http"
TRANSPORT_SERVICE = "service"
TRANSPORTS: tuple[str, ...] = (TRANSPORT_HTTP, TRANSPORT_SERVICE)

#: HTTP 中会改变状态的方法。与 ``risk.bypass`` / ``capabilities.contract`` 同口径。
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 风控标签词表：受控取值，拼错即报错。
#: 为什么是词表而不是自由字符串——标签要参与准入判断，自由字符串会让
#: 「声称不会写盘」这类断言无法被机器核对。
RISK_TAGS: dict[str, str] = {
    #: 二选一必填。强制显式声明，是为了堵住「POST 但其实是只读」和
    #: 「POST 真的会写但忘了打标签」这两种相反的错误——前者会误拦，后者会漏拦，
    #: 而两者在代码里长得一样（都是 POST）。
    "mutating": "会产生持久状态变更（写库、写文件或改变业务对象状态）",
    "read_only": "不改变任何持久状态（如用 POST 传查询条件的只读查询）",
    "reads_dataset": "读取数据集文件",
    "reads_registry": "读取进程内注册表",
    "reads_db": "读取运行记录数据库",
    "external_io": "访问本进程之外的服务或文件系统",
    "compute_heavy": "单次调用可能占用显著 CPU 时间",
    "writes_artifact": "产出可被后续复用的工件（如回测记录、审计卡片）",
}

#: 前置条件词表。前四项与风控检查点同名（``risk.checkpoints``），
#: 便于 ``contract.py`` 反查"你声明的前置条件真的有检查点实现吗"。
PRECONDITIONS: dict[str, str] = {
    "identity": "调用方身份已被识别（对应风控 identity 检查点）",
    "limit": "请求规模在允许区间内（对应风控 limit 检查点）",
    "state": "操作对象存在且可用（对应风控 state 检查点）",
    "field": "契约声明的必填字段齐全且非空（对应风控 field 检查点）",
    "operator_grant": "该操作人被授予此能力（对应 T4 能力授权）",
}

#: 与风控检查点同名、可被 ``contract.py`` 交叉核对的前置条件子集。
CHECKPOINT_PRECONDITIONS: tuple[str, ...] = ("identity", "limit", "state", "field")


class CapabilityContractError(ValueError):
    """能力契约自身不合法（字段缺失、取值越界、声明与传输方式矛盾等）。"""


def _canonical(payload: Any) -> str:
    """规范化 JSON：键排序、无多余空白。摘要必须与字典插入顺序无关。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class CapabilitySpec:
    """一个业务能力的完整契约声明。

    字段刻意分成三组，对应计划 T4 范围里的四项要求：

    - **可枚举**：``cap_id`` / ``title`` / ``summary`` / ``transport`` / ``method`` / ``path``；
    - **可测试**：``input_model`` / ``output_model`` / ``required_fields`` / ``preconditions``；
    - **可授权、可禁用**：``risk_tags`` / ``version`` / ``deprecated`` / ``successor``。
    """

    cap_id: str
    title: str
    version: str
    summary: str
    transport: str
    #: 与 T1 操作注册表同名：这是"可审计"的接缝。
    #: 能力被拒绝时，工件里的 action 就是它，拒绝事件因此可按原因码检索到具体能力。
    operation_id: str
    method: str | None = None
    path: str | None = None
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    #: 契约层面的必填字段。必须与风控 ``REQUIRED_FIELDS_BY_OPERATION`` 一致——
    #: 两处故意重复，靠对账保持一致（像校验和，重复本身就是检查手段）。
    required_fields: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    deprecated: bool = False
    successor: str | None = None
    note: str = ""

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        """契约自检。任一项不合法都抛 ``CapabilityContractError``。

        只在这里做「单个条目自身」的检查；跨条目的唯一性、引用的存在性
        放在 ``registry.validate_registry``。
        """
        problems: list[str] = []

        if not CAPABILITY_ID_RE.match(self.cap_id or ""):
            problems.append(f"cap_id 不合法（需点分小写标识）：{self.cap_id!r}")
        if not SEMVER_RE.match(self.version or ""):
            problems.append(f"version 必须是 major.minor.patch：{self.version!r}")
        if not (self.title or "").strip():
            problems.append("title 不能为空")
        if not (self.summary or "").strip():
            problems.append("summary 不能为空")
        if not (self.operation_id or "").strip():
            problems.append("operation_id 不能为空（否则无法与审计工件关联）")

        if self.transport not in TRANSPORTS:
            problems.append(f"transport 必须是 {TRANSPORTS} 之一：{self.transport!r}")

        if self.transport == TRANSPORT_HTTP:
            if (self.method or "").upper() not in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            }:
                problems.append(f"HTTP 能力必须声明合法 method：{self.method!r}")
            if not (self.path or "").startswith("/"):
                problems.append(f"HTTP 能力必须声明以 / 开头的 path：{self.path!r}")
        else:
            # 服务层能力没有传输面。留空是"如实缺省"，填了则是错误声明。
            if self.method or self.path:
                problems.append("service 能力不得声明 method/path（它没有 HTTP 面）")

        if self.input_model is not None and not (
            isinstance(self.input_model, type) and issubclass(self.input_model, BaseModel)
        ):
            problems.append("input_model 必须是 pydantic BaseModel 子类或 None")
        if self.output_model is not None and not (
            isinstance(self.output_model, type) and issubclass(self.output_model, BaseModel)
        ):
            problems.append("output_model 必须是 pydantic BaseModel 子类或 None")

        unknown_pre = [p for p in self.preconditions if p not in PRECONDITIONS]
        if unknown_pre:
            problems.append(f"未登记的前置条件：{unknown_pre}，可选 {sorted(PRECONDITIONS)}")
        unknown_tags = [t for t in self.risk_tags if t not in RISK_TAGS]
        if unknown_tags:
            problems.append(f"未登记的风控标签：{unknown_tags}，可选 {sorted(RISK_TAGS)}")

        if len(set(self.required_fields)) != len(self.required_fields):
            problems.append("required_fields 有重复项")
        if self.required_fields and self.input_model is None:
            problems.append("声明了必填字段却没有输入模型（无从校验）")

        # 副作用必须显式二选一：mutating 还是 read_only。
        # 不允许"不声明"——不声明时，准入判断只能退回按 HTTP 方法猜，
        # 而 POST 既可能是真写入（回测落库），也可能只是用请求体传查询条件
        # （结果比较），两者按方法猜必然错一半。
        side_effect_tags = [t for t in ("mutating", "read_only") if t in self.risk_tags]
        if len(side_effect_tags) != 1:
            problems.append(
                "risk_tags 必须且只能包含 mutating / read_only 之一，"
                f"当前为 {side_effect_tags or '（都没写）'}"
            )
        elif side_effect_tags[0] == "mutating":
            # 声明会写，方法就必须允许写。GET 宣称会改变状态是硬错误：
            # HTTP 语义、缓存、代理重试都会因此出错。
            method = (self.method or "").upper()
            if self.transport == TRANSPORT_HTTP and method not in MUTATING_METHODS:
                problems.append(f"{method} 能力声明了 mutating 标签，但该方法不应改变状态")

        if self.deprecated and not self.successor:
            problems.append("标记 deprecated 必须给出 successor，否则调用方无处可迁")
        if self.successor and self.successor == self.cap_id:
            problems.append("successor 不能指向自己")

        if problems:
            raise CapabilityContractError(
                f"能力 {self.cap_id!r} 契约不合法：" + "；".join(problems)
            )

    # ------------------------------------------------------------------ #
    # 摘要
    # ------------------------------------------------------------------ #
    def contract_payload(self) -> dict[str, Any]:
        """参与摘要的字段集合。刻意排除 title/summary/note 等纯文字。"""
        return {
            "cap_id": self.cap_id,
            "transport": self.transport,
            "method": (self.method or "").upper() or None,
            "path": self.path or None,
            "operation_id": self.operation_id,
            "input_schema": _model_schema(self.input_model),
            "output_schema": _model_schema(self.output_model),
            "required_fields": list(self.required_fields),
            "preconditions": list(self.preconditions),
            "risk_tags": list(self.risk_tags),
        }

    def contract_digest(self) -> str:
        """契约摘要（sha256 前 32 位，足够区分且便于人工比对）。"""
        return hashlib.sha256(_canonical(self.contract_payload()).encode("utf-8")).hexdigest()[
            :32
        ]

    def input_schema_digest(self) -> str:
        """只覆盖输入/输出 schema 的摘要，用于向调用方宣告"数据契约变了吗"。"""
        payload = {
            "input_schema": _model_schema(self.input_model),
            "output_schema": _model_schema(self.output_model),
        }
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:32]

    # ------------------------------------------------------------------ #
    # 展示
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """可序列化的公开描述（不含模型类对象，只留限定名与摘要）。"""
        return {
            "cap_id": self.cap_id,
            "title": self.title,
            "version": self.version,
            "summary": self.summary,
            "transport": self.transport,
            "operation_id": self.operation_id,
            "method": (self.method or "").upper() or None,
            "path": self.path,
            "input_model": _model_name(self.input_model),
            "output_model": _model_name(self.output_model),
            "required_fields": list(self.required_fields),
            "preconditions": list(self.preconditions),
            "risk_tags": list(self.risk_tags),
            "deprecated": self.deprecated,
            "successor": self.successor,
            "contract_digest": self.contract_digest(),
            "schema_digest": self.input_schema_digest(),
            "note": self.note,
        }


def _model_schema(model: type[BaseModel] | None) -> dict[str, Any] | None:
    """取模型 JSON Schema。``None`` 表示「该方向没有数据契约」（如 GET 无输入）。"""
    if model is None:
        return None
    return model.model_json_schema()


def _model_name(model: type[BaseModel] | None) -> str | None:
    return None if model is None else f"{model.__module__}.{model.__qualname__}"


def parse_version(version: str) -> tuple[int, int, int]:
    """解析三段式版本；不合法抛错（不返回 ``None``——静默失败会让版本比对失效）。"""
    match = SEMVER_RE.match(version or "")
    if match is None:
        raise CapabilityContractError(f"版本号不合法：{version!r}")
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def compare_versions(left: str, right: str) -> int:
    """返回 -1/0/1。用于校验 lock 历史版本严格递增。"""
    a, b = parse_version(left), parse_version(right)
    return (a > b) - (a < b)


def same_major(left: str, right: str) -> bool:
    """同一 major 视为向后兼容。跨 major 一律拒绝，不做"尽力兼容"。"""
    return parse_version(left)[0] == parse_version(right)[0]


__all__ = [
    "CAPABILITY_ID_RE",
    "CHECKPOINT_PRECONDITIONS",
    "CapabilityContractError",
    "CapabilitySpec",
    "MUTATING_METHODS",
    "PRECONDITIONS",
    "RISK_TAGS",
    "SEMVER_RE",
    "TRANSPORTS",
    "TRANSPORT_HTTP",
    "TRANSPORT_SERVICE",
    "compare_versions",
    "parse_version",
    "same_major",
]
