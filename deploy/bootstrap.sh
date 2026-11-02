#!/usr/bin/env bash
# 云主机一次性初始化（Ubuntu 22.04/24.04，Debian 12 亦可）。
#
# 幂等：重复运行只会补齐缺失项，不会重建已存在的用户/venv/timer。
#
# 用法（**以 root 执行**）：
#   sudo ./deploy/bootstrap.sh --repo /opt/deepquant [--enable-firewall]
#
# 为什么防火墙要显式开关：
#   在 SSH 端口不是 22 的机器上，`ufw enable` 会当场把当前会话切断，而重连需要
#   控制台权限。所以默认只**配置规则**不启用，把决定权留给操作者；加
#   --enable-firewall 时才启用，且启用前先从 sshd 配置里读出真实端口放行。
#
# 本机不做的事（写在这里防止以后被"顺手"加上）：
#   - 不安装、不配置任何券商客户端
#   - 不创建 live_authorization.json
#   - 不改任何策略参数
set -uo pipefail

REPO="/opt/deepquant"
ENABLE_FW=0
SERVICE_USER="deepquant"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --enable-firewall) ENABLE_FW=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 64 ;;
  esac
done

die() { echo "[fatal] $*" >&2; exit 1; }
say() { echo "[bootstrap] $*"; }

# ---------------- 0. 前置检查 ----------------
[ "$(id -u)" -eq 0 ] || die "必须以 root 运行（sudo）"
[ -f /etc/os-release ] || die "不是 Linux（找不到 /etc/os-release）"
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) say "发行版：$PRETTY_NAME" ;;
  *) die "仅支持 ubuntu/debian，当前 ${ID:-unknown}。**不要**在别的发行版上硬跑。" ;;
esac

[ -f "$REPO/STATUS.md" ] || die "仓库不在 $REPO（找不到 STATUS.md）。先把代码 rsync 上来：\n  rsync -az --exclude 'data/' --exclude 'var/' ./ user@host:$REPO/"
say "仓库就位：$REPO"

# ---------------- 1. 基础软件 ----------------
say "安装依赖包（python3-venv / chrony / rsync / ufw / logrotate / git）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential \
    chrony rsync curl ufw logrotate git ca-certificates >/dev/null

# ---------------- 2. 时区与校时 ----------------
# 交易日与心跳都按 Asia/Shanghai 判；机器在 UTC 时，17:10 会变成北京时间 01:10，
# 日更链会在半夜跑——数据没更新，月末判定必然失败。
timedatectl set-timezone Asia/Shanghai
say "时区：$(timedatectl show -p Timezone --value)"
systemctl enable --now chrony >/dev/null 2>&1 || systemctl enable --now chronyd >/dev/null 2>&1
say "校时：$(chronyc tracking 2>/dev/null | awk -F: '/System time/{print $2}' | head -1)"

# ---------------- 3. 服务用户 ----------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
  say "用户已存在：$SERVICE_USER"
else
  useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
  say "已创建系统用户：$SERVICE_USER"
fi
# 代码归 root、服务用户只读；只有 var/ 与 forward/ 需要可写（台账、日志、心跳）。
chown -R root:root "$REPO" 2>/dev/null
mkdir -p "$REPO/var/log" "$REPO/var/backup"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO/var"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO/research/stock_pick_2026-10-25/forward"
chmod 750 "$REPO/var"

# ---------------- 4. Python 环境 ----------------
if [ ! -x "$REPO/.venv/bin/python" ]; then
  say "创建虚拟环境"
  python3 -m venv "$REPO/.venv"
fi
say "安装依赖（backend/requirements.txt，版本已固定）"
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -r "$REPO/backend/requirements.txt"
"$REPO/.venv/bin/python" - <<'PY'
import pandas, numpy, akshare, sys
print(f"[bootstrap] python {sys.version.split()[0]}｜pandas {pandas.__version__}"
      f"｜numpy {numpy.__version__}｜akshare {akshare.__version__}")
PY

# ---------------- 5. 环境变量文件 ----------------
if [ ! -f "$REPO/env" ]; then
  cp "$REPO/deploy/env.example" "$REPO/env"
  chown root:"$SERVICE_USER" "$REPO/env"
  chmod 640 "$REPO/env"
  say "已生成 $REPO/env（**请编辑后填入备份与告警配置**）"
else
  say "$REPO/env 已存在，不覆盖"
fi

# ---------------- 6. systemd ----------------
say "安装 systemd 单元"
for u in deepquant-daily.service deepquant-daily.timer \
         deepquant-alert.service deepquant-alert.timer \
         deepquant-calendar.service deepquant-calendar.timer; do
  install -m 644 "$REPO/deploy/systemd/$u" "/etc/systemd/system/$u"
done
install -m 644 "$REPO/deploy/logrotate/deepquant" /etc/logrotate.d/deepquant
systemctl daemon-reload
systemctl enable --now deepquant-daily.timer deepquant-alert.timer deepquant-calendar.timer >/dev/null 2>&1
say "定时器："
systemctl list-timers 'deepquant-*' --no-pager 2>/dev/null | head -5

# ---------------- 7. 防火墙 ----------------
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')"
SSH_PORT="${SSH_PORT:-22}"
say "检测到 SSH 端口：$SSH_PORT"
ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT"/tcp >/dev/null
if [ "$ENABLE_FW" = "1" ]; then
  ufw --force enable >/dev/null
  say "防火墙已启用：仅放行 $SSH_PORT/tcp"
  say "⚠️ 请不要关闭当前会话，先另开一个窗口验证能登录"
else
  say "防火墙规则已配置但**未启用**（如需启用：ufw --force enable）"
  say "   先确认这条命令里的端口与你实际 SSH 端口一致：ufw status numbered"
fi

# ---------------- 8. 自检 ----------------
say "运行部署前自检（行情源连通性）"
( cd "$REPO" && "$REPO/.venv/bin/python" deploy/bin/preflight_sources.py ) || {
  echo "[bootstrap] ⚠️ 行情源自检未通过。**不要**继续，先按 R2 上报。" >&2
}

cat <<EOF

[bootstrap] 完成。接下来：
  1. 编辑 $REPO/env：填 DEEPQUANT_BACKUP_RSYNC / DEEPQUANT_HEARTBEAT_PING_URL / DEEPQUANT_ALERT_WEBHOOK
  2. 手工跑一次：sudo -u $SERVICE_USER $REPO/deploy/bin/daily_chain.sh --dry-run
  3. 手工跑一次备份：sudo -u $SERVICE_USER $REPO/deploy/bin/backup_offsite.sh
  4. 等到下一个交易日 17:10，检查：
       systemctl status deepquant-daily.timer
       journalctl -u deepquant-daily -n 50
       cat $REPO/research/stock_pick_2026-10-25/forward/heartbeat.json
EOF
