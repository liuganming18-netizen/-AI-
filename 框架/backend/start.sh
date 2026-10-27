#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# 框架后端统一启动与服务管理入口（SYSTEM-STARTUP-01）
#
# 用法：
#   sh start.sh                          # 默认安全启动：API (:8787) + Worker（不开放自动交易）
#   PORT=8788 sh start.sh                # 指定端口启动
#   ENABLE_PAPER_RUNTIME=1 sh start.sh   # 明确开启隔离模拟交易链 (unified_runtime)
#   sh start.sh --enable-paper           # 同上
#   sh start.sh status                   # 查看所有服务状态全景
#   sh start.sh stop                     # 安全停止本实例管理的进程
# ─────────────────────────────────────────────────────────────────────────────
set -eu

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$HERE/../.." && pwd -P)"

# 默认解释器与端口
PY="${PY:-/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python}"
if [ ! -f "$PY" ]; then
  PY="$(command -v python3 || echo "python3")"
fi
PORT="${PORT:-8787}"
ENABLE_PAPER="${ENABLE_PAPER_RUNTIME:-0}"
ARM="${PAPER_ARM:-3a}"
SYMBOL="${SYMBOL:-all}"
PID_DIR="${SYS_PID_DIR:-}"
LOG_DIR="${SYS_LOG_DIR:-}"
CONFIG_PATH="${EFFECTIVE_VIEW:-}"

ACTION="start"
EXTRA_ARGS=""

for a in "$@"; do
  case "$a" in
    start|stop|restart|status|diagnose-8787)
      ACTION="$a"
      ;;
    --enable-paper)
      ENABLE_PAPER="1"
      ;;
    --stop-collector)
      EXTRA_ARGS="$EXTRA_ARGS --stop-collector"
      ;;
    --json)
      EXTRA_ARGS="$EXTRA_ARGS --json"
      ;;
    *)
      # 其他参数透传
      EXTRA_ARGS="$EXTRA_ARGS $a"
      ;;
  esac
done

if [ "$ENABLE_PAPER" = "1" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --enable-paper"
fi

if [ -n "$PID_DIR" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --pid-dir $PID_DIR"
fi

if [ -n "$LOG_DIR" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --log-dir $LOG_DIR"
fi

if [ -n "$CONFIG_PATH" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --config-path $CONFIG_PATH"
fi

# 调用独立的统一服务管理器 service_manager.py
exec "$PY" "$HERE/service_manager.py" "$ACTION" \
  --port "$PORT" \
  --py "$PY" \
  --arm "$ARM" \
  --symbol "$SYMBOL" \
  $EXTRA_ARGS
