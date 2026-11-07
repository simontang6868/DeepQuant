# 给另一个 AI Agent：为什么这套系统能一直定时发出邮件

> 直接把本文发给那个 Agent 即可，自包含，不需要访问我的仓库。
> 本文不含任何真实凭证、IP 或邮箱；全部用占位符。

---

## 0. 一句话结论

**发信的不是 AI Agent，是操作系统级的定时器（systemd timer / cron）。**
Agent 只在最初把脚本、凭证文件和排程写好，之后**完全不参与**：会话关闭、电脑关机、Agent 不在线，邮件照发。
如果你的实现是"Agent 进程里 `while True: sleep(...)` 或 `asyncio` 定时任务 + 到点调 SMTP"，
那么**只要那个进程/会话/沙箱被回收，定时就永远失效**——这是最根本的原因，换任何邮件库都救不了。

---

## 1. 我这边实际跑的架构（可照抄的形状）

```
常开的 Linux 云主机（7×24 在线，非用户笔记本）
 ├─ /etc/systemd/system/<name>.timer     OnCalendar=Mon..Fri 17:10 / Persistent=true
 │    └─ <name>.service                 Type=oneshot / EnvironmentFile=/srv/app/env
 │         └─ daily_chain.sh           跑业务 → 最后调 notify_mail.py
 ├─ /etc/systemd/system/alert.timer     17:40 发「每日状态邮件」= 死信开关
 ├─ /etc/cron.d/weekly                 周一 09:30 → weekly_offsite.sh → 同一发信脚本
 ├─ /srv/app/env                       600 / 属主=运行用户 / **不在 git 里**
 └─ 外部心跳（healthchecks.io）        成功才 ping；整机宕机时由外部发现「没来信」
```

关键点：

1. **定时器属于 OS，不属于 Agent。** `systemctl enable --now <name>.timer` 之后，触发由 init 系统负责。
2. **凭证由 service 的 `EnvironmentFile=` 注入**；cron 里则必须自己 `set -a; . /srv/app/env; set +a`。
   cron 的环境变量极简（PATH 常常只有 `/usr/bin:/bin`），**不显式加载 env 是定时发信失败的第一大原因**——手动在 shell 里跑能成功，因为你的交互 shell 里有变量。
3. **发信脚本是纯 `smtplib.SMTP_SSL`，一次调用、发完即退出**，不常驻、不重试循环、不依赖任何 Agent 框架。
4. **`Persistent=true`**：机器在触发时刻休眠/关机，下次开机立刻补跑。没有它，断电一次就永久跳过那期。
5. **失败必须非零退出**：配置缺失 exit 2、附件过大 exit 3、SMTP 异常 exit 4。
   "每天都成功退出但收件箱没有信"是最危险的形态，三个月后才会被发现。
6. **超时按附件大小算**：`timeout = max(180, 60 + 8 × 附件MB数)`。
   固定 120s 在 13MB 时能过、25MB 时间歇性断连——**这种「有时能发有时不能」最难查**，而且很像"定时任务不稳定"。
7. **死信开关**：每个交易日 17:40 固定发一封状态邮件（正常/告警都在标题）。**没收到 = 出问题**。
   再加一个外部心跳服务在等出站信号，才能覆盖「整机宕机」这种本机没有进程能报警的情况。

---

## 2. 最小可运行模板（Linux / 有 systemd 权限）

### `/srv/app/env`（权限必须是 600，属主 = 运行用户，绝不进 git）

```bash
SMTP_HOST=smtp.example.com          # QQ 邮箱: smtp.qq.com
SMTP_PORT=465                       # 465 = SSL；587 是 STARTTLS，代码不同，别混
SMTP_USER=you@example.com
SMTP_PASS=<邮箱「授权码」，不是登录密码>
MAIL_TO=receiver@example.com
```

```bash
chmod 600 /srv/app/env && chown appuser:appuser /srv/app/env
printf '/srv/app/env\n' >> /srv/app/.gitignore
```

### `send_mail.py`（无框架依赖，失败一律非零退出）

```python
import os, ssl, smtplib, sys
from email.message import EmailMessage

need = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_TO"]
missing = [k for k in need if not os.environ.get(k)]
if missing:                                  # 不打印值，只打印键名
    print("缺少配置:", missing, file=sys.stderr); sys.exit(2)

m = EmailMessage()
m["From"] = os.environ["SMTP_USER"]
m["To"]   = os.environ["MAIL_TO"]
m["Subject"] = "定时测试 " + __import__("datetime").datetime.now().isoformat(timespec="seconds")
m.set_content("如果你收到这封，说明 OS 定时器 + 凭证 + SMTP 三者都通了。")

attach_mb = 0.0
timeout = max(180.0, 60.0 + 8.0 * attach_mb)   # 有附件时按大小推算
try:
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]),
                          context=ssl.create_default_context(), timeout=timeout) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(m)
except Exception as e:
    print(f"发送失败: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(4)
print("已发送")
```

### `/etc/systemd/system/mymail.service`

```ini
[Unit]
Description=Daily mail
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=appuser
WorkingDirectory=/srv/app
EnvironmentFile=/srv/app/env
ExecStart=/usr/bin/python3 /srv/app/send_mail.py
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal
```

### `/etc/systemd/system/mymail.timer`

```ini
[Unit]
Description=Daily mail timer

[Timer]
OnCalendar=Mon..Fri 17:10:00
Persistent=true
Unit=mymail.service

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now mymail.timer
```

### 用 cron 时（等价写法，注意 env 与 PATH）

```cron
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
10 17 * * 1-5 appuser /usr/bin/bash -c 'set -a; . /srv/app/env; set +a; /usr/bin/python3 /srv/app/send_mail.py' >> /srv/app/logs/mail.log 2>&1
```

---

## 3. 按可能性排序的排查清单

| # | 症状 | 最可能原因 | 怎么证 |
| --- | --- | --- | --- |
| 1 | 手动跑成功，到点从不发 | **定时靠 Agent 进程/sleep，进程被回收** | `ps` 看到底有没有常驻进程；`systemctl list-timers` 有没有你的 timer |
| 2 | 手动成功、cron 失败 | **cron 环境里没有凭证 / PATH 太窄** | 在 cron 里加一行 `env > /tmp/cronenv` 看真实环境 |
| 3 | 登录被拒 `535` / `Authentication failed` | 用了**登录密码而非授权码**；或没开 SMTP 服务 | 邮箱网页端确认已开启 SMTP 并生成授权码 |
| 4 | 连接超时 / 立即断开 | 465(SSL) 与 587(STARTTLS) 混用；出网 25/465 被云厂商封 | `openssl s_client -connect host:465 -quiet` 测连通 |
| 5 | 小附件能发、大附件时好时坏 | **固定 timeout 撞线**（base64 膨胀 4/3，DATA 阶段很慢） | 打印每封的字节数与耗时；改 `max(180, 60+8×MB)` |
| 6 | 偶发整周没发 | timer 缺 `Persistent=true`，触发时机器休眠 | `systemctl show <timer> -p Persistent` |
| 7 | 时间总差 8 小时 | 云主机 UTC，人按北京时间排 | `timedatectl`；统一设 `Asia/Shanghai` 或 timer 里写清时区 |
| 8 | 日志 rc=0 但没收到 | **异常被吞 / 静默跳过 / 发件被判垃圾** | 让脚本失败必非零；查垃圾箱；看 SMTP 会话实际返回码 |
| 9 | 第一次成功，之后再也收不到 | 频率限制 / 单日发信额度 / 被风控 | 换收件域测试；控制频率 |
| 10 | 完全不知道什么时候断的 | **没有死信开关** | 固定每天发一封状态信；「没收到」本身就是告警 |

---

## 4. 验收标准（别用「我手动跑通了一次」当验收）

1. `systemctl list-timers` 能看到 NEXT 触发时间，且是**下一次**而非过去。
2. 手动 `systemctl start mymail.service` 收到信；`journalctl -u mymail.service -n 30` 有 `已发送`。
3. **把 Agent 会话完全关掉**，等过一次真实触发时刻，再开发收件箱确认。
   —— 这一步才是「定时」二字的验收，前两步只证明「能发」。
4. 故意删掉 env 里一个变量触发一次，确认退出码非 0 且日志写明缺哪个键（**不打印值**）。
5. 故意把机器时间/休眠模拟一次，确认 `Persistent=true` 会补跑。

---

## 5. 如果你根本没有一台常开的机器

Agent 运行环境通常会被回收，此时**不要试图在 Agent 里做定时**，改用外部调度器让它到点调用你的发信脚本：

- GitHub Actions `on: schedule: - cron: '10 9 * * 1-5'`（注意 cron 用 UTC，北京时间 17:10 = UTC 09:10）；
- 云函数 / 云托管的定时触发器；
- 一台最便宜的常开 VPS 上挂 systemd timer（本文方案，最可控）。

外部心跳服务（如 healthchecks.io）**只能监控、不能替你发信**，别把它当调度器用。

---

## 6. 千万别做的三件事

- 别把凭证写进代码、命令行参数、日志、邮件正文或 git；只放 `600` 的 env 文件，日志里只以 `<set>`/`<unset>` 出现。
- 别用「发不出去也返回 0」换报表好看——那等于备份通道静默死亡。
- 别用预测/模拟结果代替真实触发：定时发信的唯一证据是**会话关闭后真实到点收到的那封信**。
