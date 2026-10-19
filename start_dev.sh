#!/usr/bin/env bash
# DeepQuant 一键启动（后端 API + 前端工作台）
#
# 用法（Git Bash）：
#   bash start_dev.sh          # 启动
#   bash start_dev.sh stop     # 停止
#   bash start_dev.sh status   # 查看状态
#
# 注意：不要用 `... | head` 的方式启动长驻服务，管道关闭会杀掉进程。
# 本脚本一律用日志重定向，不使用管道。

set -u

REPO="D:/DeepQuant power by txm/DeepQuant-main"
BACKEND_PORT=8123
FRONTEND_PORT=5173
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_demo/Scripts/python.exe"
NODE="C:/Users/77436/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
NODE_DIR="C:/Users/77436/.workbuddy/binaries/node/versions/22.22.2-6"

LOG_DIR="${REPO}/.devlogs"
mkdir -p "$LOG_DIR"

kill_port() {
  local port="$1"
  local pids
  pids=$(netstat -ano 2>/dev/null | grep ":${port} " | grep LISTENING |
         awk '{print $NF}' | sort -u)
  for pid in $pids; do
    echo "  kill PID $pid (port $port)"
    taskkill //PID "$pid" //F >/dev/null 2>&1
  done
}

wait_up() {
  local url="$1" name="$2" i=0
  while [ $i -lt 40 ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null)
    if [ "$code" = "200" ]; then echo "  [OK] $name -> $url"; return 0; fi
    sleep 1; i=$((i+1))
  done
  echo "  [FAIL] $name 未能在 40s 内就绪 ($url)"
  return 1
}

do_stop() {
  echo "停止 DeepQuant 服务..."
  kill_port "$BACKEND_PORT"
  kill_port "$FRONTEND_PORT"
  echo "已停止。"
}

do_status() {
  for p in "$BACKEND_PORT:后端" "$FRONTEND_PORT:前端"; do
    port="${p%%:*}"; name="${p##*:}"
    if netstat -ano 2>/dev/null | grep ":${port} " | grep -q LISTENING; then
      echo "  [运行中] $name  $port"
    else
      echo "  [已停止] $name  $port"
    fi
  done
}

do_start() {
  echo "启动 DeepQuant..."
  echo "== 清理旧进程 =="
  kill_port "$BACKEND_PORT"
  kill_port "$FRONTEND_PORT"
  sleep 2

  echo "== 后端 API (:$BACKEND_PORT) =="
  ( cd "${REPO}/backend" && \
    DEEPQUANT_DATA_ROOT="${REPO}/data" \
    PYTHONPATH="${REPO}/backend/src" \
    "$PY" -m uvicorn deepquant.api.app:app \
      --host 127.0.0.1 --port "$BACKEND_PORT" \
      > "${LOG_DIR}/backend.log" 2>&1 ) &
  wait_up "http://127.0.0.1:${BACKEND_PORT}/health" "后端"

  echo "== 前端工作台 (:$FRONTEND_PORT) =="
  ( cd "${REPO}/frontend" && \
    PATH="${NODE_DIR}:$PATH" \
    "$NODE" node_modules/vite/bin/vite.js --port "$FRONTEND_PORT" \
      > "${LOG_DIR}/frontend.log" 2>&1 ) &
  wait_up "http://localhost:${FRONTEND_PORT}/" "前端"

  echo "== 联调检查 =="
  curl -s --max-time 5 "http://localhost:${FRONTEND_PORT}/api/health" 2>/dev/null || echo "  (代理未响应)"

  echo
  echo "完成："
  echo "  工作台  http://localhost:${FRONTEND_PORT}"
  echo "  API     http://127.0.0.1:${BACKEND_PORT}/docs"
  echo "  日志    ${LOG_DIR}/"
}

case "${1:-start}" in
  start)  do_start ;;
  stop)   do_stop ;;
  status) do_status ;;
  *) echo "用法: bash start_dev.sh [start|stop|status]" ; exit 1 ;;
esac
