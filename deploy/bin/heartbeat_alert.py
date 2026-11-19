#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""机内告警 + 每日状态邮件：每个交易日在收盘流程之后检查心跳是否新鲜。

## 它解决什么、不解决什么

**解决**：机器醒着、但日更链没跑成（数据源挂了、脚本报错、磁盘满）。
**不解决**：整机宕机 / 断电 / 网络断开——那时没有任何进程会去说"我没跑"。
后者只能靠**外部**监控在等我们的出站信号，见 ``daily_chain.sh`` 的 4b 段。

## 每日状态邮件（用户选定的"死信开关"方案）

设 ``DEEPQUANT_MAIL_STATUS=1`` 且 SMTP 配置齐全时，本脚本**每天发一封状态邮件**
（正常/告警都在标题里）。死信逻辑：**当天没收到邮件 = 出了问题**——要么日更链没跑，
要么邮件通道挂了（后者也会以 rc=5 暴露在 systemd 里）。它弱于 healthchecks.io
（要靠人注意到"没来信"），但不需要用户去配置任何外部服务。

## 判定（按顺序，第一条命中即返回）

1. 心跳文件不存在 → CRITICAL（从未成功跑过，或被人删了）
2. 今天是**非交易日** → OK（不要求非交易日有心跳；但会检查上一交易日有没有）
3. 心跳日期不是今天 → CRITICAL（今天没跑）
4. ``ok == false`` → CRITICAL（跑了但失败，带 mode）
5. 其余 → OK

⚠️ 第 2 条之后仍要检查上一交易日：只在交易日检查会让"周五漏跑、周一正常"
看起来毫无问题——而漏的那期永远补不回来。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
SP5 = REPO / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5"
HB = REPO / "research" / "stock_pick_2026-10-25" / "forward" / "heartbeat.json"
#: R8 §2：更正台账旁路哈希链（链头随每日状态邮件外发，形成第二条时点锚）。
CHAIN = (REPO / "research" / "stock_pick_2026-10-25" / "forward"
         / "corrections.hashchain.jsonl")
#: U16-EXEC-3 断链 A：旁证 sha256 随 11-30 告警邮件外发（tamper-evidence 锚）。
CORROB = (REPO / "research" / "stock_pick_2026-10-25" / "forward"
          / "corroboration" / "20261130.json")
CORROB_SIDECAR = pathlib.Path(str(CORROB) + ".sha256")
sys.path.insert(0, str(SP5))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _mail_channel():
    """邮件通道（可选）。返回 (cfg, None) 或 (None, 原因)。"""
    if os.environ.get("DEEPQUANT_MAIL_STATUS", "").strip() != "1":
        return None, "DEEPQUANT_MAIL_STATUS 未开启"
    import notify_mail
    cfg = notify_mail.read_config(dict(os.environ))
    miss = notify_mail.missing_keys(cfg)
    if miss:
        return None, f"SMTP 配置缺失：{', '.join(miss)}"
    return cfg, None


def _mail_status(cfg, subject: str, body: str) -> bool:
    """发状态邮件。失败只返回 False，由调用方决定退出码——邮件挂了不改变检查结论。"""
    import notify_mail
    try:
        msg = notify_mail.build_message(cfg, subject, body, [])
        notify_mail.send(msg, cfg)
        print(f"[mail] 状态邮件已发送：{subject}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[mail] ✗ 状态邮件发送失败：{type(e).__name__}: {e}", file=sys.stderr)
        return False


def _corrections_chain_head() -> str:
    """R8 §2：读旁路链链头；任何异常都**显式标注 CHAIN_UNAVAILABLE**（失败关闭，不静默）。

    ⚠️ 链头只是时点锚，**不是**更正完整性证明；完整性须由
    ``corrections_hashchain.py --verify`` 独立复算，且云端同步完成前
    **不得**用备份包证明完整性（R8 §2）。
    """
    try:
        rows = [x for x in CHAIN.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not rows:
            raise ValueError("空链")
        rec = json.loads(rows[-1])
        h = str(rec.get("chain_head") or "")
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
            raise ValueError("链头格式非法")
        return (f"更正台账链头（第 {rec.get('seq')} 行）：{h}\n"
                "链头是时点锚，不构成更正完整性证明；完整性以 --verify 独立复算为准。")
    except Exception as e:  # noqa: BLE001
        return (f"更正台账链头：CHAIN_UNAVAILABLE（{type(e).__name__}）\n"
                "⚠️ 链不可用，本邮件**不构成**更正完整性证明，请查 "
                "corrections.hashchain.jsonl 与同步状态。")


def _with_chain(body: str) -> str:
    """R8 §2：把链头追加到状态邮件正文（正常／告警都带）。可单测，不依赖 SMTP。"""
    return body + "\n" + _corrections_chain_head()


def _with_corroboration(body: str) -> str:
    """U16-EXEC-3：把旁证 sha256 + commit 追加到状态邮件（tamper-evidence 锚）。

    旁证文件存在才附带（预期 11-30 出证后）；不存在是正常的（其他交易日），
    不标注、不改 rc。旁证存在但侧车缺失/不符 ⇒ 显式标注 ANCHOR_UNAVAILABLE，
    但不改心跳检查结论（与 corrections 链头同口径：邮件外发不干扰告警语义）。
    """
    if not CORROB.is_file():
        return body
    try:
        if not CORROB_SIDECAR.is_file():
            raise FileNotFoundError("SIDECAR_MISSING")
        want = CORROB_SIDECAR.read_text(encoding="utf-8").split(maxsplit=1)[0]
        got = __import__("hashlib").sha256(CORROB.read_bytes()).hexdigest()
        if want != got:
            raise ValueError("SIDECAR_MISMATCH")
        rec = json.loads(CORROB.read_text(encoding="utf-8"))
        return (body + "\n"
                f"旁证 sha256（U16-EXEC-3，11-30 出证）：{want}\n"
                f"旁证 commit：{rec.get('commit', '')}\n"
                f"旁证文件数：{rec.get('n_files', '')}\n"
                "该 sha 由 11-30 告警邮件外发，12-02 归档邮件对验（事后可检测）。")
    except Exception as e:  # noqa: BLE001
        return (body + "\n"
                f"旁证锚：ANCHOR_UNAVAILABLE（{type(e).__name__}）\n"
                "⚠️ 旁证存在但 sha 不可用，本邮件不构成旁证完整性锚，请查 "
                "corroboration/20261130.json 及侧车。")


def _load_hb() -> dict | None:
    if not HB.exists():
        return None
    try:
        return json.loads(HB.read_text(encoding="utf-8"))
    except Exception:
        return {"_broken": True}


def _is_trading_day(day: str) -> bool | None:
    """None 表示日历不可用（此时不当成非交易日——那会让告警静默）。"""
    try:
        import trade_calendar as tcal
        days = tcal.load(today=day)
        return day in set(days)
    except Exception:
        return None


def _notify(text: str, webhook: str | None) -> bool:
    """把告警送出去。**没有 webhook 就不算送出**——只在 stderr 打印。

    stderr 会进 journald，但没人会天天翻 journal。这一点必须显式暴露：
    返回值 False 时脚本以非零退出，让 systemd 的 OnFailure 或外部监控接住。
    """
    print(f"[ALERT] {text}", file=sys.stderr)
    if not webhook:
        return False
    try:
        body = json.dumps({"text": text, "source": "deepquant-heartbeat"},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(webhook, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:
        print(f"[ALERT] 通知发送失败：{type(e).__name__}: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=None, help="覆盖今天（测试用）")
    ap.add_argument("--max-age-hours", type=float, default=20.0)
    ap.add_argument("--webhook", default=os.environ.get("DEEPQUANT_ALERT_WEBHOOK"))
    ap.add_argument("--hb", default=None, help="心跳文件路径（默认 forward/heartbeat.json）")
    args = ap.parse_args(argv)

    today = args.today or time.strftime("%Y%m%d")
    global HB
    if args.hb:
        HB = pathlib.Path(args.hb)
    hb = _load_hb()
    mail_cfg, mail_why = _mail_channel()
    if mail_cfg is None:
        print(f"[mail] 状态邮件未启用（{mail_why}）")

    def finish(subject: str, body: str, rc: int) -> int:
        """收口：发状态邮件（每日一封，缺信=没跑），再按检查结论退出。"""
        # R8 §2：链头写进**每一封**状态邮件（正常／告警都带）。
        # 链缺失只标注、不改 rc：状态邮件的 rc 语义仍由心跳检查结论决定
        # （沿用本模块既有设计「邮件挂了不改变检查结论」），避免新增旁路证据
        # 把正常日更误报成告警。链完整性另由 --verify 独立复算。
        body = _with_chain(body)
        # U16-EXEC-3：旁证 sha256 随邮件外发（不干扰 rc 语义，旁证不存在时 no-op）
        body = _with_corroboration(body)
        if mail_cfg is not None:
            # 邮件发送失败不改变 rc 的告警语义：告警时本来就非 0；
            # 但"一切正常却发不出邮件"必须非 0——否则人会以为每天有信。
            ok_mail = _mail_status(mail_cfg, subject, body)
            if rc == 0 and not ok_mail:
                rc = 5
        return rc

    if hb is None:
        return finish("【DeepQuant 告警】心跳文件不存在",
                      "心跳文件不存在：日更链从未成功运行，或文件被删除。", 1)
    if hb.get("_broken"):
        return finish("【DeepQuant 告警】心跳文件不可解析",
                      "心跳文件不可解析：可能被截断（磁盘满/写入中断）。", 1)

    ts = str(hb.get("ts") or "")
    hb_day = ts[:10].replace("-", "")
    trading = _is_trading_day(today)

    if trading is False:
        # 非交易日：不要求今天有心跳，但仍要说清楚最后一条是哪天的。
        print(f"[ok] 今天 {today} 非交易日；最后心跳 {ts}（mode={hb.get('mode')}）")
        return finish(f"【DeepQuant 正常】{today} 非交易日（最后心跳 {ts}）",
                      f"今天 {today} 非交易日；最后心跳 {ts}，mode={hb.get('mode')}。", 0)
    if trading is None:
        print("[warn] 交易日历不可用，无法判断今天是否交易日；按交易日处理")

    if hb_day != today:
        return finish("【DeepQuant 告警】今天没有心跳",
                      f"今天 {today} 没有心跳；最后一条是 {ts}（mode={hb.get('mode')}）。", 1)

    try:
        t = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        age_h = (dt.datetime.now() - t).total_seconds() / 3600.0
    except Exception:
        age_h = None
    if age_h is not None and age_h > args.max_age_hours:
        return finish("【DeepQuant 告警】心跳过旧",
                      f"心跳过旧：{age_h:.1f} 小时前（上限 {args.max_age_hours}）。", 1)

    if hb.get("ok") is not True:
        return finish("【DeepQuant 告警】日更链失败",
                      f"日更链失败：mode={hb.get('mode')} note={hb.get('stage_note') or hb.get('reason')}。", 1)

    print(f"[ok] 心跳正常：{ts} mode={hb.get('mode')} decision={hb.get('decision')}")
    return finish(f"【DeepQuant 正常】{today} 日更链正常",
                  f"心跳 {ts}，mode={hb.get('mode')}，decision={hb.get('decision')}。", 0)


if __name__ == "__main__":
    sys.exit(main())
