#!/bin/sh
# 热转冷导出 启动器
#
#   start   幂等启动常驻导出进程（每 600 秒一轮，已在跑就复用）
#   stop    停止常驻进程
#   status  看进程与最近一轮
#   once    只跑一轮就退出（默认）
#
#   这个脚本只做一件事：把热库里已闭合的 K 线 / 持仓量增量导到冷层 parquet。
#   不下载、不采集、不改历史分片、不碰策略。
#
#   换解释器：PY=/path/to/python ./框架/backend/exporter.sh start
set -eu

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$HERE/../.." && pwd -P)"

# 需要 pyarrow + duckdb 的解释器；项目内没有自带，默认用 WorkBuddy 的隔离环境
PY="${PY:-/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python}"
PIDFILE="$PROJECT_ROOT/数据/市场数据/parquet/_export.pid"
LOG="$PROJECT_ROOT/回测日志/热转冷导出.log"
INTERVAL="${INTERVAL:-600}"

if [ ! -x "$PY" ]; then
  echo "找不到解释器：$PY" >&2
  echo "请设置 PY 环境变量指向装有 pyarrow/duckdb 的 python" >&2
  exit 1
fi

ACTION="${1:-once}"
[ $# -gt 0 ] && shift

case "$ACTION" in
  once)
    exec "$PY" "$PROJECT_ROOT/工具_热转冷导出.py" --once "$@"
    ;;

  start)
    if [ -f "$PIDFILE" ]; then
      PID="$(cat "$PIDFILE" 2>/dev/null || true)"
      if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "已运行（复用）：导出进程 pid=$PID，每 ${INTERVAL} 秒一轮"
        exit 0
      fi
    fi
    mkdir -p "$(dirname "$LOG")"
    NEWPID="$("$PY" "$HERE/spawn_detached.py" --pid "$PIDFILE" --log "$LOG" \
              --cwd "$PROJECT_ROOT" -- "$PY" "$PROJECT_ROOT/工具_热转冷导出.py" --loop "$INTERVAL")"
    sleep 2
    if kill -0 "$NEWPID" 2>/dev/null; then
      echo "已启动导出进程 pid=${NEWPID}（每 ${INTERVAL} 秒一轮）"
      echo "  日志: $LOG"
    else
      echo "错误：进程未能存活，见 $LOG" >&2
      exit 4
    fi
    ;;

  stop)
    if [ -f "$PIDFILE" ]; then
      PID="$(cat "$PIDFILE" 2>/dev/null || true)"
      if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        echo "已停止导出进程 pid=$PID"
      else
        echo "进程不存在，清理 pid 文件"
      fi
      rm -f "$PIDFILE"
    else
      echo "未在运行（无 pid 文件）"
    fi
    ;;

  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
      echo "运行中 pid=$(cat "$PIDFILE")"
    else
      echo "未运行"
    fi
    echo "--- 最近日志 ---"
    tail -6 "$LOG" 2>/dev/null || echo "（无日志）"
    ;;

  *)
    echo "用法：$0 {start|stop|status|once} [参数...]" >&2
    exit 1
    ;;
esac
