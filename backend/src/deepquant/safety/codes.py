"""安全层原因码：白名单、最小权限、熔断、退出清理。

与风控码（T2）、能力码（T4）**字段同构、表独立**。三张表分开的理由一样：
每张表的验收标准各自绑定一个分母（风控四类、能力 6 项、安全 4 类），
混表会让某个报表的分母悄悄变掉。

一条编码原则
------------
凡是"**判不了**"的情况都必须有码，且按不通过处理：DNS 解析不出来、
清理验证拿不到证据、熔断状态文件读坏——这些都不等于"没问题"。
把它们混进"放行"里，就等于把"没检出"当成"没问题"，这是本项目复发性最强的一类错误。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 安全层四个类别（对应计划 T5 的四项范围）。
CATEGORY_OUTBOUND = "outbound"
CATEGORY_PRIVILEGE = "privilege"
CATEGORY_BREAKER = "breaker"
CATEGORY_CLEANUP = "cleanup"

#: 计划要求的四类（覆盖率分母）。
REQUIRED_CATEGORIES: tuple[str, ...] = (
    CATEGORY_OUTBOUND,
    CATEGORY_PRIVILEGE,
    CATEGORY_BREAKER,
    CATEGORY_CLEANUP,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_OUTBOUND: "出站目标不在白名单或指向内网",
    CATEGORY_PRIVILEGE: "权限不足或未显式授予",
    CATEGORY_BREAKER: "熔断已打开或状态不可判",
    CATEGORY_CLEANUP: "退出清理未完成或证据不全",
}


@dataclass(frozen=True)
class SafetyReason:
    """一个安全层原因码。"""

    code: str
    category: str
    status_code: int
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "category": self.category,
            "category_label": CATEGORY_LABELS[self.category],
            "status_code": self.status_code,
            "summary": self.summary,
        }


SAFETY_CODES: dict[str, SafetyReason] = {
    # ---------------- 出站目标 ----------------
    "outbound_not_whitelisted": SafetyReason(
        "outbound_not_whitelisted", CATEGORY_OUTBOUND, 403,
        "目标主机不在出站白名单内",
    ),
    "outbound_scheme_forbidden": SafetyReason(
        "outbound_scheme_forbidden", CATEGORY_OUTBOUND, 403,
        "协议不在允许集合内（只允许 http/https）",
    ),
    "outbound_ambiguous_host": SafetyReason(
        "outbound_ambiguous_host", CATEGORY_OUTBOUND, 403,
        "主机写法有歧义（十进制/八进制/十六进制 IP、userinfo、反斜杠），可能绕过文本匹配",
    ),
    "outbound_private_address": SafetyReason(
        "outbound_private_address", CATEGORY_OUTBOUND, 403,
        "目标解析到私网地址",
    ),
    "outbound_loopback": SafetyReason(
        "outbound_loopback", CATEGORY_OUTBOUND, 403,
        "目标解析到环回地址",
    ),
    "outbound_link_local": SafetyReason(
        "outbound_link_local", CATEGORY_OUTBOUND, 403,
        "目标解析到链路本地地址",
    ),
    "outbound_metadata_endpoint": SafetyReason(
        "outbound_metadata_endpoint", CATEGORY_OUTBOUND, 403,
        "目标是云厂商元数据地址（凭据泄露风险）",
    ),
    "outbound_reserved_address": SafetyReason(
        "outbound_reserved_address", CATEGORY_OUTBOUND, 403,
        "目标解析到保留/多播/未指定地址",
    ),
    "outbound_dns_unresolvable": SafetyReason(
        "outbound_dns_unresolvable", CATEGORY_OUTBOUND, 503,
        "无法解析目标主机，按不通过处理（算不出就说算不出）",
    ),
    "outbound_malformed_url": SafetyReason(
        "outbound_malformed_url", CATEGORY_OUTBOUND, 422,
        "URL 无法解析或缺少必填部分",
    ),
    # ---------------- 最小权限 ----------------
    "privileged_operation_denied": SafetyReason(
        "privileged_operation_denied", CATEGORY_PRIVILEGE, 403,
        "该操作会改变状态，但当前操作人未被显式授予（最小权限模式）",
    ),
    "privilege_unidentified": SafetyReason(
        "privilege_unidentified", CATEGORY_PRIVILEGE, 403,
        "最小权限模式要求明确的操作人身份，但请求未提供",
    ),
    # ---------------- 熔断 ----------------
    "breaker_open": SafetyReason(
        "breaker_open", CATEGORY_BREAKER, 503,
        "熔断已打开，暂停该类操作",
    ),
    "breaker_state_unreadable": SafetyReason(
        "breaker_state_unreadable", CATEGORY_BREAKER, 503,
        "熔断状态文件损坏或不可读，按不通过处理",
    ),
    # ---------------- 退出清理 ----------------
    "cleanup_incomplete": SafetyReason(
        "cleanup_incomplete", CATEGORY_CLEANUP, 503,
        "上次退出未确认清理完成，拒绝启动（需人工确认后解除）",
    ),
    "cleanup_step_failed": SafetyReason(
        "cleanup_step_failed", CATEGORY_CLEANUP, 500,
        "清理步骤执行或验证失败",
    ),
    "cleanup_evidence_missing": SafetyReason(
        "cleanup_evidence_missing", CATEGORY_CLEANUP, 503,
        "清理缺少可检索的证据（日志或比对快照）",
    ),
}


def safety_reason(code: str) -> SafetyReason:
    try:
        return SAFETY_CODES[code]
    except KeyError as exc:  # pragma: no cover - 拼错码属编程错误
        raise KeyError(f"未登记的安全原因码 {code!r}，可选：{sorted(SAFETY_CODES)}") from exc


def safety_codes() -> list[dict[str, object]]:
    return [SAFETY_CODES[k].to_dict() for k in sorted(SAFETY_CODES)]


def category_coverage() -> dict[str, object]:
    """四类覆盖率：每类至少一个码，且反查每个码的类别都合法。"""
    present = {spec.category for spec in SAFETY_CODES.values()}
    missing = [c for c in REQUIRED_CATEGORIES if c not in present]
    return {
        "required": list(REQUIRED_CATEGORIES),
        "present": sorted(present),
        "missing_categories": missing,
        "ratio": round(len(REQUIRED_CATEGORIES) - len(missing), 0) / len(REQUIRED_CATEGORIES),
        "n_codes": len(SAFETY_CODES),
    }


def assert_codes_complete() -> None:
    problems: list[str] = []
    for key, spec in SAFETY_CODES.items():
        if spec.code != key:
            problems.append(f"{key}: code 字段为 {spec.code!r}")
        if not (400 <= spec.status_code < 600):
            problems.append(f"{key}: 状态码 {spec.status_code} 不是拒绝类")
        if spec.category not in CATEGORY_LABELS:
            problems.append(f"{key}: 类别 {spec.category!r} 没有标签")
        if not spec.summary.strip():
            problems.append(f"{key}: 缺少人话说明")
    missing = [c for c in REQUIRED_CATEGORIES if c not in {s.category for s in SAFETY_CODES.values()}]
    if missing:
        problems.append(f"缺少类别：{missing}")
    if problems:
        raise ValueError("安全原因码表不自洽：" + "；".join(problems))


__all__ = [
    "CATEGORY_BREAKER",
    "CATEGORY_CLEANUP",
    "CATEGORY_LABELS",
    "CATEGORY_OUTBOUND",
    "CATEGORY_PRIVILEGE",
    "REQUIRED_CATEGORIES",
    "SAFETY_CODES",
    "SafetyReason",
    "assert_codes_complete",
    "category_coverage",
    "safety_codes",
    "safety_reason",
]
