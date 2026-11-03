#!/usr/bin/env bash
# 云主机一次性初始化。支持两族发行版：
#   - Debian 系：Ubuntu 22.04/24.04、Debian 12（apt）
#   - RHEL 系  ：TencentOS Server 3/4、CentOS Stream、Rocky、AlmaLinux（dnf）
#
# 幂等：重复运行只会补齐缺失项，不会重建已存在的用户/venv/timer。
#
# 用法（**以 root 执行**）：
#   ./deploy/bootstrap.sh --repo /opt/quant [--enable-firewall]
#
# 为什么防火墙要显式开关：
#   在 SSH 端口不是 22 的机器上，启用防火墙会当场把当前会话切断，而重连需要
#   控制台权限。所以默认只**配置规则**不启用，把决定权留给操作者；加
#   --enable-firewall 时才启用，且启用前先从 sshd 配置里读出真实端口放行。
#
# 本机不做的事（写在这里防止以后被"顺手"加上）：
#   - 不安装、不配置任何券商客户端
#   - 不创建 live_authorization.json
#   - 不改任何策略参数
#   - **不碰同机其他服务**（TencentOS 上同机有 /opt/vdl 的 media-downloader：
#     不重启 docker 守护进程、不 prune、不占 8000 端口）
set -uo pipefail

REPO="/opt/quant"
ENABLE_FW=0
SERVICE_USER="deepquant"
# 国内机器直连 pypi.org 会超时/重置。默认走清华镜像，可用 PIP_INDEX_URL 覆盖。
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --enable-firewall) ENABLE_FW=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 64 ;;
  esac
done

die() { echo "[fatal] $*" >&2; exit 1; }
say() { echo "[bootstrap] $*"; }

# ---------------- 0. 前置检查 ----------------
[ "$(id -u)" -eq 0 ] || die "必须以 root 运行（sudo）"
[ -f /etc/os-release ] || die "不是 Linux（找不到 /etc/os-release）"
. /etc/os-release

FAMILY=""
case "${ID:-}" in
  ubuntu|debian) FAMILY="debian"; say "发行版：$PRETTY_NAME（apt 系）" ;;
  tencentos|centos|rhel|rocky|almalinux|openeuler|anolis) FAMILY="rhel"; say "发行版：$PRETTY_NAME（dnf 系）" ;;
  *)
    # ID_LIKE 兜底：衍生发行版常常只写 ID_LIKE
    case "${ID_LIKE:-}" in
      *debian*|*ubuntu*) FAMILY="debian" ;;
      *rhel*|*fedora*|*centos*) FAMILY="rhel" ;;
    esac
    [ -n "$FAMILY" ] || die "不支持 ${ID:-unknown}（ID_LIKE=${ID_LIKE:-无}）。**不要**在未知发行版上硬跑。"
    say "发行版：$PRETTY_NAME（按 $FAMILY 系处理）"
    ;;
esac

[ -f "$REPO/STATUS.md" ] || die "仓库不在 $REPO（找不到 STATUS.md）。先把代码传上来：\n  scp code.tar.gz root@HOST:$REPO/var/ && tar xzf $REPO/var/code.tar.gz -C $REPO"
say "仓库就位：$REPO"

# ---------------- 1. 基础软件 ----------------
say "安装依赖包（python3 / chrony / rsync / logrotate）"
if [ "$FAMILY" = "debian" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip \
      chrony rsync curl logrotate git ca-certificates >/dev/null
else
  # RHEL 系的 python3 自带 venv；不装 build-essential（pandas/numpy/pyarrow
  # 都有 cp311 manylinux 轮子，编译工具只会在镜像缺轮子时才需要）
  dnf install -y -q python3 python3-pip chrony rsync curl logrotate git ca-certificates >/dev/null
fi
python3 -V >/dev/null 2>&1 || die "python3 不可用"

# ---------------- 2. 时区与校时 ----------------
# 交易日与心跳都按 Asia/Shanghai 判；机器在 UTC 时，17:10 会变成北京时间 01:10，
# 日更链会在半夜跑——数据没更新，月末判定必然失败。
timedatectl set-timezone Asia/Shanghai
say "时区：$(timedatectl show -p Timezone --value)"
# 两族的服务名不同：Debian 是 chrony，RHEL 是 chronyd
systemctl enable --now chrony >/dev/null 2>&1 || systemctl enable --now chronyd >/dev/null 2>&1
say "校时：$(timedatectl show -p NTPSynchronized --value 2>/dev/null)（chronyc：$(systemctl is-active chrony 2>/dev/null || systemctl is-active chronyd 2>/dev/null)）"

# ---------------- 3. 服务用户 ----------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
  say "用户已存在：$SERVICE_USER"
else
  useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
  say "已创建系统用户：$SERVICE_USER"
fi
# 代码归 root、服务用户只读；下列目录**必须可写**：
#   var/        台账之外的运行时产物（日志、备份、更新报告、清单）
#   forward/    台账、心跳、观测表——证据本体
#   data/       增量更新写 exec_all；ADJ 重建写 qfq_stage 再原子替换 qfq_all
#   .../data    月度面板缓存（build_panel 在 import 期就会 makedirs）
chown -R root:root "$REPO" 2>/dev/null
mkdir -p "$REPO/var/log" "$REPO/var/backup" "$REPO/logs"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO/var"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO/research/stock_pick_2026-10-25/forward"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO/data"
for d in "$REPO/research/stock_pick_2026-10-25/data" \
         "$REPO/research/stock_pick_2026-10-25/out" \
         "$REPO/research/stock_pick_2026-10-25/sp5_out"; do
  [ -d "$d" ] && chown -R "$SERVICE_USER:$SERVICE_USER" "$d"
done
chmod 750 "$REPO/var"

# ---------------- 4. Python 环境 ----------------
# ⚠️ 解释器版本下限由 requirements 钉死：numpy==2.5.4 要求 Python>=3.12。
#    TencentOS 4 系统自带 3.11 ⇒ 必须从 EPOL 装 python3.12，**不许**把版本
#    降到旧 numpy 来迁就系统解释器（那等于换了一套没跑过回归的依赖组合）。
pick_python() {
  for cand in python3.13 python3.12 python3; do
    command -v "$cand" >/dev/null 2>&1 || continue
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}

PYBIN="$(pick_python || true)"
if [ -z "$PYBIN" ]; then
  if [ "$FAMILY" = "rhel" ]; then
    say "系统 Python < 3.12，从 EPOL 安装 python3.12（numpy 2.5.4 的硬下限）"
    dnf install -y -q python3.12 python3.12-pip >/dev/null || die "python3.12 安装失败"
    PYBIN="$(pick_python)" || die "安装后仍找不到 >=3.12 的解释器"
  else
    die "找不到 Python>=3.12（numpy==2.5.4 的硬下限）。**不要**降级依赖版本，先装新解释器。"
  fi
fi
say "解释器：$PYBIN ($("$PYBIN" -V 2>&1))"

if [ ! -x "$REPO/.venv/bin/python" ]; then
  say "创建虚拟环境"
  "$PYBIN" -m venv "$REPO/.venv" || {
    # RHEL 系 venv 缺 ensurepip 时的显式出路（而不是一句含糊的报错）
    "$PYBIN" -m venv --without-pip "$REPO/.venv" && \
      curl -sS -m 60 https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && \
      "$REPO/.venv/bin/python" /tmp/get-pip.py -q -i "$PIP_INDEX_URL" || \
      die "venv 创建失败（ensurepip 缺失且 get-pip 兜底也失败）"
  }
else
  # venv 已存在但解释器不对（例如先用系统 3.11 建过）⇒ 重建。
  # venv 完全可重建，删掉没有数据损失；留着才会让"装的是哪套依赖"变得说不清。
  VENV_MM="$("$REPO/.venv/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)"
  PY_MM="$("$PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  if [ "$VENV_MM" != "$PY_MM" ]; then
    say "venv 是 Python $VENV_MM，与选定解释器 $PY_MM 不符 ⇒ 重建（venv 可重建，无数据损失）"
    rm -rf "$REPO/.venv"
    "$PYBIN" -m venv "$REPO/.venv"
  fi
fi
say "安装依赖（backend/requirements.txt，版本已固定；index=$PIP_INDEX_URL）"
"$REPO/.venv/bin/pip" install -q --upgrade pip -i "$PIP_INDEX_URL"
"$REPO/.venv/bin/pip" install -q -r "$REPO/backend/requirements.txt" -i "$PIP_INDEX_URL" || {
  # 换源失败时**不要**静默降级到默认源：报出来，让人决定
  echo "[bootstrap] ⚠️ 依赖安装失败（index=$PIP_INDEX_URL）。先确认网络，不要跳过。" >&2
}
"$REPO/.venv/bin/python" - <<'PY'
import pandas, numpy, sys
print(f"[bootstrap] python {sys.version.split()[0]}｜pandas {pandas.__version__}"
      f"｜numpy {numpy.__version__}｜venv={sys.prefix}")
PY

# ---------------- 5. 环境变量文件 ----------------
# 模板里的 __REPO__ 在安装时替换成真实路径：这样仓库里不写死任何绝对路径，
# 换目录部署不用改文件。
if [ ! -f "$REPO/env" ]; then
  sed "s#__REPO__#$REPO#g" "$REPO/deploy/env.example" > "$REPO/env"
  # R3-1：env 含 SMTP 授权码等凭证 ⇒ 600 且属主为**运行用户**（root 无需可读）。
  # 该文件只存在于云主机、不在任何 git 仓库内；备份清单（backup_offsite.sh）刻意不含它。
  chown "$SERVICE_USER:$SERVICE_USER" "$REPO/env"
  chmod 600 "$REPO/env"
  say "已生成 $REPO/env（**请编辑后填入备份与告警配置**）"
else
  say "$REPO/env 已存在，不覆盖"
fi

# ---------------- 6. systemd ----------------
say "安装 systemd 单元（路径替换 __REPO__ → $REPO）"
for u in deepquant-daily.service deepquant-daily.timer \
         deepquant-alert.service deepquant-alert.timer \
         deepquant-calendar.service deepquant-calendar.timer; do
  sed "s#__REPO__#$REPO#g" "$REPO/deploy/systemd/$u" > "/etc/systemd/system/$u"
  chmod 644 "/etc/systemd/system/$u"
done
sed "s#__REPO__#$REPO#g" "$REPO/deploy/logrotate/deepquant" > /etc/logrotate.d/deepquant
chmod 644 /etc/logrotate.d/deepquant
systemctl daemon-reload
systemctl enable --now deepquant-daily.timer deepquant-alert.timer deepquant-calendar.timer >/dev/null 2>&1
say "定时器："
systemctl list-timers 'deepquant-*' --no-pager 2>/dev/null | head -5

# ---------------- 7. 防火墙 ----------------
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')"
SSH_PORT="${SSH_PORT:-22}"
say "检测到 SSH 端口：$SSH_PORT"
if [ "$FAMILY" = "debian" ] && command -v ufw >/dev/null 2>&1; then
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
  fi
elif [ "$FAMILY" = "rhel" ] && systemctl is-active firewalld >/dev/null 2>&1; then
  say "检测到 firewalld 在运行"
  if firewall-cmd --query-service=ssh >/dev/null 2>&1; then
    say "ssh 服务已放行（firewalld），不做改动"
  else
    say "⚠️ firewalld 未放行 ssh —— 但不在这里自动改：云厂商安全组通常已管理入站，"
    say "   贸然 reload 可能切断当前会话。需要时手工执行：firewall-cmd --add-service=ssh"
  fi
else
  say "本机未启用 ufw/firewalld：入站由云厂商安全组管理（见交接文档），跳过"
fi

# ---------------- 8. 自检 ----------------
say "运行部署前自检（行情源连通性）"
( cd "$REPO" && "$REPO/.venv/bin/python" deploy/bin/preflight_sources.py ) || {
  echo "[bootstrap] ⚠️ 行情源自检未通过。**不要**继续，先按 R2 上报。" >&2
}

cat <<EOF

[bootstrap] 完成。接下来：
  1. 编辑 $REPO/env：填 DEEPQUANT_BACKUP_RSYNC / DEEPQUANT_HEARTBEAT_PING_URL / DEEPQUANT_ALERT_WEBHOOK
  2. 数据校验：sudo -u $SERVICE_USER $REPO/.venv/bin/python $REPO/deploy/bin/migrate_data.py verify \\
       --base $REPO --manifest $REPO/var/migrate_manifest.json
  3. 手工跑一次：sudo -u $SERVICE_USER $REPO/deploy/bin/daily_chain.sh --dry-run
  4. 等到下一个交易日 17:10，检查：
       systemctl status deepquant-daily.timer
       journalctl -u deepquant-daily -n 50
       cat $REPO/research/stock_pick_2026-10-25/forward/heartbeat.json
EOF
