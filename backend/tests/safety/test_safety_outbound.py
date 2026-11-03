"""出站目标策略：白名单语义与内网穿透防护。

这份测试就是"非白名单回调 100% 拒绝"这条验收标准的**唯一依据**：
本仓库当前没有回调面，线上不可能有"被拒的回调"，所以判据正确性只能靠用例证明。
因此穿透用例按真实攻击手法逐条列：歧义数字、userinfo、反斜杠、IPv6 映射、
多记录解析（前公网后私网）、尾部点。

判据是"全部拒绝"而不是"大部分拒绝"——绕过只需要成功一次。
"""

from __future__ import annotations

import pytest

from deepquant.safety.outbound import (
    OutboundBlocked,
    UrlPolicy,
    assert_allowed,
    check,
    classify_address,
    default_policy,
    enforce_scan,
    normalize_host,
    parse_hosts,
    parse_ip_literal,
    scan_url_literals,
)

PUBLIC = ["93.184.216.34"]  # example.com 的典型公网地址


def _policy(hosts: tuple[str, ...] = ("example.com",), ips: list[str] | None = None, **kw) -> UrlPolicy:
    return UrlPolicy(
        allowed_hosts=frozenset(hosts),
        resolver=lambda host, port: ips if ips is not None else PUBLIC,
        **kw,
    )


# --------------------------------------------------------------------------- #
# 白名单语义
# --------------------------------------------------------------------------- #


def test_empty_whitelist_denies_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认最小权限：没配白名单 = 拒绝一切出站，而不是放行一切。"""
    monkeypatch.delenv("DEEPQUANT_OUTBOUND_ALLOWED_HOSTS", raising=False)
    policy = default_policy()
    assert policy.allowed_hosts == frozenset()
    assert check("https://example.com/x", policy).code == "outbound_not_whitelisted"


def test_whitelisted_public_host_allowed() -> None:
    decision = check("https://example.com/cb", _policy())
    assert decision.allowed is True
    assert decision.resolved == tuple(PUBLIC)


def test_host_not_in_whitelist_blocked() -> None:
    assert check("https://evil.io/cb", _policy()).code == "outbound_not_whitelisted"


def test_substring_match_is_not_enough() -> None:
    """``example.com.evil.io`` 含有白名单字符串，但不是白名单主机。"""
    assert check("https://example.com.evil.io/", _policy()).code == "outbound_not_whitelisted"


def test_wildcard_matches_subdomain_only() -> None:
    policy = _policy(hosts=("*.example.com",))
    assert check("https://api.example.com/", policy).allowed is True
    assert check("https://example.com/", policy).code == "outbound_not_whitelisted"


def test_trailing_dot_is_normalised() -> None:
    """``example.com.`` 与 ``example.com`` 在 DNS 上等价，字符串不等价。"""
    assert normalize_host("Example.COM.") == "example.com"
    assert check("https://example.com./cb", _policy()).allowed is True


def test_uppercase_scheme_and_host_allowed() -> None:
    assert check("HTTPS://EXAMPLE.COM/cb", _policy()).allowed is True


def test_parse_hosts_accepts_commas_and_semicolons() -> None:
    assert parse_hosts("a.com, b.com;c.com") == {"a.com", "b.com", "c.com"}
    assert parse_hosts("") == frozenset()


# --------------------------------------------------------------------------- #
# 协议
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/"])
def test_forbidden_schemes_blocked(url: str) -> None:
    assert check(url, _policy()).code == "outbound_scheme_forbidden"


def test_malformed_urls_blocked() -> None:
    assert check("", _policy()).code == "outbound_malformed_url"
    assert check("example.com/cb", _policy()).code == "outbound_malformed_url"
    assert check("https:///cb", _policy()).code == "outbound_malformed_url"


# --------------------------------------------------------------------------- #
# 歧义写法（文本上看不出来，实际指向别处）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com@127.0.0.1/",       # userinfo 伪装成域名
        "http://user:pw@example.com/",          # 带凭据
        "http://example.com\\@127.0.0.1/",      # 反斜杠
    ],
)
def test_ambiguous_host_blocked(url: str) -> None:
    decision = check(url, _policy())
    assert decision.allowed is False
    assert decision.code in {"outbound_ambiguous_host", "outbound_not_whitelisted"}


@pytest.mark.parametrize(
    "host, expect",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("0x7f.0.0.1", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("017700000001", "127.0.0.1"),
    ],
)
def test_ambiguous_numeric_hosts_parse_to_ip(host: str, expect: str) -> None:
    """这些写法在浏览器/inet_aton 里都指向同一地址，必须被识别成 IP 再分类。"""
    parsed = parse_ip_literal(host)
    assert parsed is not None and str(parsed) == expect


def test_non_numeric_host_is_not_ip_literal() -> None:
    assert parse_ip_literal("example.com") is None
    assert parse_ip_literal("api.example.com") is None


# --------------------------------------------------------------------------- #
# 地址分类：穿透的主力
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "address, code",
    [
        ("127.0.0.1", "outbound_loopback"),
        ("::1", "outbound_loopback"),
        ("10.0.0.5", "outbound_private_address"),
        ("192.168.1.1", "outbound_private_address"),
        ("172.16.0.1", "outbound_private_address"),
        ("169.254.1.1", "outbound_link_local"),
        ("169.254.169.254", "outbound_metadata_endpoint"),
        ("100.100.100.200", "outbound_metadata_endpoint"),
        ("fd00:ec2::254", "outbound_metadata_endpoint"),
        ("0.0.0.0", "outbound_reserved_address"),
        ("224.0.0.1", "outbound_reserved_address"),
        ("::ffff:127.0.0.1", "outbound_loopback"),
        ("93.184.216.34", None),
        ("8.8.8.8", None),
    ],
)
def test_classify_address(address: str, code: str | None) -> None:
    assert classify_address(address) == code


def test_loopback_by_literal_blocked_even_if_whitelisted() -> None:
    """把 127.0.0.1 写进白名单也不行：地址分类是第二道，不能只靠名单。"""
    policy = _policy(hosts=("127.0.0.1",))
    assert check("http://127.0.0.1/", policy).code == "outbound_loopback"


def test_public_ip_literal_must_be_explicitly_whitelisted() -> None:
    policy = _policy(hosts=("example.com",))
    assert check("https://8.8.8.8/", policy).code == "outbound_not_whitelisted"


def test_dns_resolving_to_private_blocked() -> None:
    """域名白名单 + 解析到内网，是最常见的一类 SSRF。"""
    policy = _policy(ips=["10.0.0.5"])
    assert check("https://example.com/cb", policy).code == "outbound_private_address"


def test_all_records_checked_not_just_first() -> None:
    """只查第一条会被"前公网后私网"的解析绕过。"""
    policy = _policy(ips=["93.184.216.34", "127.0.0.1"])
    assert check("https://example.com/cb", policy).code == "outbound_loopback"


def test_unresolvable_host_blocked() -> None:
    def boom(host: str, port: int):
        raise OSError("dns down")

    policy = UrlPolicy(allowed_hosts=frozenset({"example.com"}), resolver=boom)
    assert check("https://example.com/", policy).code == "outbound_dns_unresolvable"


def test_empty_resolution_blocked() -> None:
    policy = _policy(ips=[])
    assert check("https://example.com/", policy).code == "outbound_dns_unresolvable"


def test_allow_private_networks_escape_hatch() -> None:
    """本机联调用的显式开关（生产打开它等于放弃内网防护）。"""
    policy = _policy(hosts=("localhost",), ips=["127.0.0.1"], allow_private_networks=True)
    assert check("http://localhost:8080/", policy).allowed is True


# --------------------------------------------------------------------------- #
# 断言入口
# --------------------------------------------------------------------------- #


def test_assert_allowed_raises_with_code() -> None:
    with pytest.raises(OutboundBlocked) as excinfo:
        assert_allowed("http://169.254.169.254/latest/meta-data/", _policy())
    assert excinfo.value.code == "outbound_metadata_endpoint"
    assert excinfo.value.status_code == 403


def test_assert_allowed_returns_decision() -> None:
    decision = assert_allowed("https://example.com/ok", _policy())
    assert decision.allowed and decision.host == "example.com"


# --------------------------------------------------------------------------- #
# 源码绊线
# --------------------------------------------------------------------------- #


def test_scan_finds_bad_url_in_source(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text(
        'import requests\nrequests.get("http://169.254.169.254/latest/meta-data/")\n',
        encoding="utf-8",
    )
    report = scan_url_literals(src, _policy())
    assert report.n_urls == 1
    assert report.ok is False
    assert report.findings[0].code == "outbound_metadata_endpoint"


def test_scan_allows_whitelisted_url(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.py").write_text('URL = "https://example.com/cb"\n', encoding="utf-8")
    assert scan_url_literals(src, _policy()).ok is True


def test_enforce_scan_raises_on_blocked(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text('URL = "http://127.0.0.1/admin"\n', encoding="utf-8")
    with pytest.raises(OutboundBlocked):
        enforce_scan(src, _policy())


def test_real_source_tree_has_no_url_literals() -> None:
    """基线：当前 backend/src 没有 URL 字面量。

    这条断言的意义是"绊线本身可用且当前干净"。若将来出现字面量，它会失败并要求
    作者要么进白名单、要么改用受控入口——这正是设置绊线的目的。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src"
    report = scan_url_literals(root, _policy(hosts=("example.com",), ips=PUBLIC))
    assert report.ok, [f.to_dict() for f in report.findings if not f.allowed]
