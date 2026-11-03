"""出站目标策略：白名单 + 内网穿透防护。

现状必须说清
------------
本仓库当前**没有回调面，也没有任何出站 URL 调用点**（行情经 akshare 取，URL 在
第三方库内部；``backend/src`` 里 0 个 ``http://`` 字面量，见 ``scan_url_literals``）。
所以这块不是"修一个已存在的洞"，而是**先立边界**：任何将来的出站调用（回调、
通知、Webhook）都必须经过 :func:`assert_allowed`，否则默认拒绝。

把"没有洞"和"立了闸"分开讲，是为了避免下次有人读到"完成了出站白名单"就以为
线上曾有回调被拦。验收证据是"判据对的 30+ 条穿透用例"，而不是"线上拦了多少次"。

为什么白名单必须配地址分类，而不是只看域名
------------------------------------------
只做字符串白名单会被廉价绕过：``http://127.0.0.1``、``http://2130706433``
（十进制 127.0.0.1）、``http://0x7f.0.0.1``、``http://0177.0.0.1``、
``http://[::ffff:127.0.0.1]``、``http://127.1``、``http://user@evil.com@127.0.0.1``
在文本上都不等于白名单里的域名，但它们指向本机；而
``http://internal.example.com`` 若解析到 ``10.0.0.5``，字符串匹配也看不出来。

因此判定分两层，缺一不可：

1. **名字层**：协议、userinfo、歧义数字写法、白名单匹配；
2. **地址层**：解析结果（全部 A/AAAA 记录）逐个分类，任一落在私网/环回/链路本地/
   保留段即拒。**必须检查全部**——只检查第一条记录会被"前公网后私网"的解析绕过。

解析不出来怎么办
----------------
拒绝（``outbound_dns_unresolvable``，503）。"解析失败"既可能是拼错域名，也可能是
攻击者控制的 DNS 不可达；这两种情况下放行都等于跳过检查。这与 T2 的
「算不出就说算不出」是同一条原则。
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from deepquant.safety.codes import safety_reason

#: 允许的出站协议。刻意不含 ``file`` / ``ftp`` / ``gopher`` / ``data``：
#: 它们要么能读本地文件，要么能构造协议走私。
ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")

#: 出站白名单环境变量（逗号分隔；支持 ``*.example.com`` 形式）。
ALLOWED_HOSTS_ENV = "DEEPQUANT_OUTBOUND_ALLOWED_HOSTS"

#: 云厂商元数据地址：拿到它基本等于拿到实例凭据，单列一类，便于日志里一眼认出。
METADATA_IPS: frozenset[str] = frozenset(
    {
        "169.254.169.254",   # AWS / GCP / Azure / 阿里云通用
        "100.100.100.200",    # 阿里云
        "fd00:ec2::254",      # AWS IPv6
    }
)

_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)


class OutboundBlocked(RuntimeError):
    """出站目标被拒绝。字段与风控/能力层拒绝保持同构，便于统一处理。"""

    def __init__(self, code: str, detail: str = "", *, host: str | None = None) -> None:
        spec = safety_reason(code)
        self.code = code
        self.status_code = spec.status_code
        self.detail = detail or spec.summary
        self.host = host
        super().__init__(f"[{code}] {self.detail}")


@dataclass(frozen=True)
class OutboundDecision:
    """一次出站判定的结果。"""

    allowed: bool
    url: str
    host: str | None = None
    code: str | None = None
    detail: str = ""
    resolved: tuple[str, ...] = ()
    category: str = "outbound"
    checkpoint: str = "outbound"

    @property
    def status_code(self) -> int | None:
        return None if self.code is None else safety_reason(self.code).status_code

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "allowed": self.allowed,
            "url": self.url,
            "host": self.host,
            "checkpoint": self.checkpoint,
            "resolved": list(self.resolved),
        }
        if self.code is not None:
            payload["reason_code"] = self.code
            payload["status_code"] = self.status_code
            payload["detail"] = self.detail
        return payload


# --------------------------------------------------------------------------- #
# 主机名解析与地址分类
# --------------------------------------------------------------------------- #


def normalize_host(host: str) -> str:
    """小写、去尾部点、IDNA 转 ASCII。

    去尾部点很关键：``localhost.`` 与 ``localhost`` 在 DNS 上等价，但字符串不等价。
    只做字符串比较的白名单会被一个点绕过。
    """
    text = (host or "").strip().strip("[]").rstrip(".").lower()
    if not text:
        return ""
    try:
        return text.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return text


def parse_ip_literal(host: str) -> ipaddress._BaseAddress | None:
    """把主机名解析成 IP 字面量（含各种歧义写法）；不是字面量则返回 ``None``。

    覆盖 ``inet_aton`` 能理解的写法（浏览器/部分 HTTP 库也照此解析），
    否则这些东西会在"看起来不是 IP"的同时真的连到本机：

    - ``2130706433``（单个十进制整数）
    - ``0x7f000001``（十六进制）
    - ``017700000001``（八进制）
    - ``127.1``（省略段）
    - ``0x7f.0.0.1``（混合进制分段）
    """
    text = normalize_host(host)
    if not text:
        return None

    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass

    # 单段整数 / 单段十六进制 / 单段八进制
    if "." not in text:
        if text.isdigit():
            base = 8 if len(text) > 1 and text.startswith("0") else 10
            try:
                value = int(text, base)
            except ValueError:
                return None
            return ipaddress.IPv4Address(value) if 0 <= value <= 0xFFFFFFFF else None
        if text.lower().startswith("0x"):
            try:
                value = int(text, 16)
            except ValueError:
                return None
            return ipaddress.IPv4Address(value) if 0 <= value <= 0xFFFFFFFF else None
        return None

    # 2~4 段：每段可为十进制 / 0x 十六进制 / 前导 0 八进制
    parts = text.split(".")
    if not 2 <= len(parts) <= 4 or not all(_NUMERIC_LABEL.match(p) for p in parts):
        return None

    numbers: list[int] = []
    for part in parts:
        if part.lower().startswith("0x"):
            numbers.append(int(part, 16))
        elif len(part) > 1 and part.startswith("0"):
            numbers.append(int(part, 8))
        else:
            numbers.append(int(part, 10))

    if len(numbers) == 1:
        return ipaddress.IPv4Address(numbers[0]) if numbers[0] <= 0xFFFFFFFF else None
    if any(n > 255 for n in numbers[:-1]) or numbers[-1] >= 256 ** (4 - len(numbers) + 1):
        return None
    value = 0
    for number in numbers[:-1]:
        value = (value << 8) | number
    remaining = 4 - (len(numbers) - 1)
    value = (value << (8 * remaining)) | numbers[-1]
    return ipaddress.IPv4Address(value)


def classify_address(address: str) -> str | None:
    """给一个 IP 打分类；公网地址返回 ``None`` 表示"可放行"。

    顺序有意义：元数据地址同时属于链路本地，先判元数据才能给出更准确的码
    （运维看到 ``outbound_metadata_endpoint`` 会立刻想到凭据泄露，而不是"地址段问题"）。
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "outbound_reserved_address"

    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:  # ::ffff:127.0.0.1 之类，按 IPv4 判
        ip = mapped

    if str(ip) in METADATA_IPS:
        return "outbound_metadata_endpoint"
    # 0.0.0.0 / :: 在 Python 里 is_private 也为 True，先判 unspecified 才能给出更准的码
    if ip.is_unspecified:
        return "outbound_reserved_address"
    if ip.is_loopback:
        return "outbound_loopback"
    if ip.is_link_local:
        return "outbound_link_local"
    if ip.is_multicast or ip.is_reserved:
        return "outbound_reserved_address"
    if ip.is_private:
        return "outbound_private_address"
    return None


def _default_resolver(host: str, port: int) -> list[str]:
    """默认解析器：取全部 A/AAAA 记录。"""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


# --------------------------------------------------------------------------- #
# 策略
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UrlPolicy:
    """出站策略。``allowed_hosts`` 为空表示**全部拒绝**（默认最小权限）。"""

    allowed_hosts: frozenset[str] = frozenset()
    allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES
    #: 仅用于本机联调；生产中置 True 等于放弃内网防护。
    allow_private_networks: bool = False
    #: 注入点：测试用固定解析结果，避免依赖真实 DNS。
    resolver: Callable[[str, int], Sequence[str]] | None = None

    def matches(self, host: str) -> bool:
        """白名单匹配。``*.example.com`` 只匹配子域，不匹配 ``example.com`` 本身。"""
        target = normalize_host(host)
        if not target:
            return False
        for pattern in self.allowed_hosts:
            entry = normalize_host(pattern)
            if not entry:
                continue
            if entry.startswith("*."):
                suffix = entry[1:]  # ".example.com"
                if target.endswith(suffix) and len(target) > len(suffix):
                    return True
                continue
            if target == entry:
                return True
        return False


@dataclass
class _Parsed:
    scheme: str
    host: str
    port: int
    has_userinfo: bool
    raw_netloc: str


def _parse(url: str) -> _Parsed | OutboundDecision:
    """只做结构解析。

    **刻意不在这里判协议**：``file:///etc/passwd`` 没有 host，若先要求 host，
    它会得到 ``outbound_malformed_url``，而真正的问题是"协议不允许"。
    错误码指错原因，会把排查带偏到 URL 拼写上。
    """
    if not isinstance(url, str) or not url.strip():
        return OutboundDecision(False, str(url), code="outbound_malformed_url", detail="URL 为空")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        return OutboundDecision(
            False, url, code="outbound_malformed_url", detail=f"URL 无法解析：{exc}"
        )

    if not parts.scheme:
        return OutboundDecision(
            False, url, code="outbound_malformed_url", detail="URL 缺少协议"
        )
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return OutboundDecision(
            False, url, code="outbound_malformed_url", detail="端口不是合法整数"
        )

    return _Parsed(
        scheme=parts.scheme.lower(),
        host=normalize_host(parts.hostname or ""),
        port=port,
        has_userinfo=bool(parts.username or parts.password),
        raw_netloc=parts.netloc,
    )


def check(url: str, policy: UrlPolicy | None = None) -> OutboundDecision:
    """判定一个出站 URL 是否放行。返回判定而不抛错，便于批量体检。"""
    active = policy if policy is not None else default_policy()
    parsed = _parse(url)
    if isinstance(parsed, OutboundDecision):
        return parsed

    if parsed.scheme not in active.allowed_schemes:
        return OutboundDecision(
            False,
            url,
            host=parsed.host or None,
            code="outbound_scheme_forbidden",
            detail=f"协议 {parsed.scheme!r} 不允许，只允许 {list(active.allowed_schemes)}",
        )

    if not parsed.host:
        return OutboundDecision(
            False, url, code="outbound_malformed_url", detail="URL 缺少主机"
        )

    # userinfo 会让"看到的域名"与"实际连接的主机"不一致（user@evil.com@127.0.0.1）。
    # 反斜杠等价于斜杠，部分解析器会在此处产生分歧。两者都直接拒绝，不做归一化猜测。
    if parsed.has_userinfo or "\\" in parsed.raw_netloc:
        return OutboundDecision(
            False,
            url,
            host=parsed.host,
            code="outbound_ambiguous_host",
            detail="URL 含 userinfo 或反斜杠，主机边界有歧义",
        )

    literal = parse_ip_literal(parsed.host)
    if literal is not None:
        # 先分类再看白名单：把这些地址单独放进白名单，仍然应该被拒；
        # 而且元数据地址要给出更 alarmed 的码，而不是笼统的"不在白名单"。
        if not active.allow_private_networks:
            code = classify_address(str(literal))
            if code is not None:
                return OutboundDecision(
                    False,
                    url,
                    host=parsed.host,
                    code=code,
                    detail=f"{parsed.host} 属于 {code.split('_', 1)[1]}",
                    resolved=(str(literal),),
                )
        if not active.matches(parsed.host):
            return OutboundDecision(
                False,
                url,
                host=parsed.host,
                code="outbound_not_whitelisted",
                detail=f"IP 字面量 {parsed.host} 不在白名单内",
            )
        return OutboundDecision(True, url, host=parsed.host, resolved=(str(literal),))

    if not active.matches(parsed.host):
        return OutboundDecision(
            False,
            url,
            host=parsed.host,
            code="outbound_not_whitelisted",
            detail=f"主机 {parsed.host} 不在白名单内",
        )

    resolver = active.resolver or _default_resolver
    try:
        addresses = tuple(dict.fromkeys(resolver(parsed.host, parsed.port)))
    except Exception as exc:  # noqa: BLE001 - 解析失败一律不放行
        return OutboundDecision(
            False,
            url,
            host=parsed.host,
            code="outbound_dns_unresolvable",
            detail=f"解析 {parsed.host} 失败：{type(exc).__name__}: {exc}",
        )

    if not addresses:
        return OutboundDecision(
            False,
            url,
            host=parsed.host,
            code="outbound_dns_unresolvable",
            detail=f"{parsed.host} 没有任何 A/AAAA 记录",
        )

    if not active.allow_private_networks:
        # 必须检查**全部**记录：只查第一条会被"前公网后私网"的解析绕过。
        for address in addresses:
            code = classify_address(address)
            if code is not None:
                return OutboundDecision(
                    False,
                    url,
                    host=parsed.host,
                    code=code,
                    detail=f"{parsed.host} 解析到 {address}（{code.split('_', 1)[1]}）",
                    resolved=addresses,
                )

    return OutboundDecision(True, url, host=parsed.host, resolved=addresses)


def assert_allowed(url: str, policy: UrlPolicy | None = None) -> OutboundDecision:
    """放行则返回判定，否则抛 :class:`OutboundBlocked`。

    所有出站调用都应经过这里，而不是自己 ``if "example.com" in url``——
    后者是可以被 ``example.com.evil.io`` 绕过的子串匹配。
    """
    decision = check(url, policy)
    if not decision.allowed:
        assert decision.code is not None
        raise OutboundBlocked(decision.code, decision.detail, host=decision.host)
    return decision


# --------------------------------------------------------------------------- #
# 默认策略与静态扫描
# --------------------------------------------------------------------------- #


def parse_hosts(raw: str) -> frozenset[str]:
    """解析白名单字符串。空串 = 空集 = 全部拒绝（默认最小权限）。"""
    return frozenset(
        normalize_host(item) for item in (raw or "").replace(";", ",").split(",") if item.strip()
    )


def default_policy() -> UrlPolicy:
    """环境变量驱动的默认策略：未配置白名单即拒绝一切出站。"""
    return UrlPolicy(allowed_hosts=parse_hosts(os.environ.get(ALLOWED_HOSTS_ENV, "")))


_URL_RE = re.compile(r"https?://[^\s\"'`<>\\)\]}]+")


@dataclass
class ScanFinding:
    """源码扫描的一条结果。"""

    path: str
    line: int
    url: str
    allowed: bool
    code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "url": self.url,
            "allowed": self.allowed,
            "code": self.code,
        }


@dataclass
class ScanReport:
    root: str
    findings: list[ScanFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.allowed for f in self.findings)

    @property
    def n_urls(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "ok": self.ok,
            "n_urls": self.n_urls,
            "n_blocked": sum(1 for f in self.findings if not f.allowed),
            "findings": [f.to_dict() for f in self.findings],
        }


def _iter_url_strings(source: str):
    """产出 ``(行号, 字符串)``，**跳过文档字符串**。

    为什么用 ``ast`` 而不是按行正则：文档里天然会写示例 URL（本模块的说明就有
    ``http://127.0.0.1`` 之类的反例）。按行扫会把说明文字当成真实调用点，
    于是"绊线"一上线就自报警，几次之后大家就会把它加进忽略名单——那等于没有绊线。

    已知盲区：``f"http://{host}/x"`` 这类拼接串是 ``JoinedStr``，扫不到；
    所以它是绊线，不是完备检测。
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def scan_url_literals(source_root: str | Path, policy: UrlPolicy | None = None) -> ScanReport:
    """扫描源码里**真实的字符串常量**中的 URL，逐个过策略。

    目的不是"证明现在安全"，而是**给未来加一道绊线**：有人写下
    ``requests.get("http://127.0.0.1:8080/admin")`` 时，这个扫描会报出来。
    只扫字符串常量（跳过文档串），拼出来的 URL 扫不到——绊线不是完备检测，这点必须写明。
    """
    root = Path(source_root)
    report = ScanReport(root=str(root))
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, value in _iter_url_strings(source):
            for match in _URL_RE.finditer(value):
                url = match.group(0)
                decision = check(url, policy)
                report.findings.append(
                    ScanFinding(
                        path=str(path.relative_to(root)),
                        line=number,
                        url=url,
                        allowed=decision.allowed,
                        code=decision.code,
                    )
                )
    return report


def enforce_scan(source_root: str | Path, policy: UrlPolicy | None = None) -> ScanReport:
    """扫描并断言：任一字面量不放行即抛错（供测试与 CI 调用）。"""
    report = scan_url_literals(source_root, policy)
    blocked = [f for f in report.findings if not f.allowed]
    if blocked:
        detail = "；".join(f"{f.path}:{f.line} {f.url} → {f.code}" for f in blocked[:5])
        raise OutboundBlocked(
            "outbound_not_whitelisted",
            f"源码中有 {len(blocked)} 个未放行的出站地址：{detail}",
        )
    return report


__all__ = [
    "ALLOWED_HOSTS_ENV",
    "ALLOWED_SCHEMES",
    "METADATA_IPS",
    "OutboundBlocked",
    "OutboundDecision",
    "ScanFinding",
    "ScanReport",
    "UrlPolicy",
    "assert_allowed",
    "check",
    "classify_address",
    "default_policy",
    "enforce_scan",
    "normalize_host",
    "parse_hosts",
    "parse_ip_literal",
    "scan_url_literals",
]
