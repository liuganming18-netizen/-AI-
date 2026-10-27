#!/bin/bash
# DATA-CANVAS-01：单态驱动（构建隔离库 → 推进时间 → 起隔离后端 → 截图 → 收工）
# 用法：run_state.sh <state> <port> <stubPid>
set -u
STATE="$1"; PORT="$2"; STUB="$3"; FAULT="${4:-}"
ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
EV="$ROOT/evidence/DATA-CANVAS-01"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
NODE="/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node"
BASE="/tmp/dc01/$STATE"

cd "$EV" || exit 1
echo "== build $STATE $(date +%H:%M:%S)"
"$PY" queries/build_iso_env.py --state "$STATE" --stub-pid "$STUB" > "out/build_$STATE.json" 2>&1 || { tail -5 "out/build_$STATE.json"; exit 1; }
echo "== tick $STATE"
"$PY" queries/tick_iso.py --base "$BASE" --state "$STATE" --stub-pid "$STUB" > "out/tick_$STATE.json" 2>&1 || { tail -5 "out/tick_$STATE.json"; exit 1; }

BUSY=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')
if [ "$BUSY" != "0" ]; then echo "!! 端口 $PORT 已被占用，拒绝启动（避免连到上一轮的旧实例）"; lsof -nP -iTCP:$PORT -sTCP:LISTEN; exit 2; fi
echo "== adapter :$PORT $(date +%H:%M:%S)"
cd "$ROOT/框架/backend" || exit 1
PORT="$PORT" \
MARKET_DB="$BASE/market/market.db" MARKET_ARCHIVE="$BASE/archive" \
LIVE_DB="$BASE/live/live.db" LIVE_DIR="$BASE/live" \
PAPER_LEDGER_DIR="$BASE/paper" PAPER_LOCK_DIR="$BASE/locks" \
JOBS_DB="$BASE/jobs/jobs.sqlite3" SYS_PID_DIR="$BASE/pid" SYS_LOG_DIR="$BASE/logs" \
ISOLATED_MODE=1 DATA_HUB_PROJECT="$ROOT" \
PYTHONPATH="$ROOT/框架:$ROOT/框架/backend:$ROOT" \
NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1 \
HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= \
"$PY" adapter.py > "$BASE/logs/adapter.out" 2>&1 &
ADAPTER_PID=$!
echo "adapter pid=$ADAPTER_PID"

for i in $(seq 1 40); do
  code=$(curl -s --noproxy '*' -o /dev/null -m 3 -w "%{http_code}" "http://127.0.0.1:$PORT/api/v1/health" || true)
  [ "$code" = "200" ] && break
  sleep 1
done
echo "health=$code"
OWNER=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | awk '{print $2}' | sort -u | tr '\n' ',')
echo "listener pid=$OWNER (started $ADAPTER_PID)"
if [ "$OWNER" != "$ADAPTER_PID," ]; then echo "!! 监听进程与本次启动的 pid 不一致 ⇒ 中止"; kill "$ADAPTER_PID" 2>/dev/null; exit 3; fi

# 账本 schema（隔离目录内建表；空库时接口会 unavailable）
cd "$ROOT/框架/backend" || exit 1
MARKET_DB="$BASE/market/market.db" LIVE_DB="$BASE/live/live.db" LIVE_DIR="$BASE/live" \
PAPER_LEDGER_DIR="$BASE/paper" PAPER_LOCK_DIR="$BASE/locks" PAPER_LEDGER_ALLOW_INIT=1 \
PYTHONPATH="$ROOT/框架:$ROOT/框架/backend" NO_PROXY=127.0.0.1 \
"$PY" -c "import live_store as L; L.ensure_live_schema(); [L.paper_conn(a) for a in ('3a','3b','3c','3d')]; print('ledger schema ok')" \
  >> "$BASE/logs/adapter.out" 2>&1

if [ "$STATE" != "stopped" ]; then
  echo "== tick_loop $STATE"
  "$PY" "$EV/queries/tick_loop.py" --base "$BASE" --state "$STATE" --stub-pid "$STUB" > "$BASE/logs/tick_loop.log" 2>&1 &
  TICK_PID=$!
fi

echo "== point proxy to :$PORT"
# 请求日志是追加写 ⇒ 必须先清空，否则会把上一轮的记录混进本轮（"同一次运行"就不成立）
rm -f "$EV/out/req_$STATE.jsonl"
curl -s --noproxy '*' -G --data-urlencode "url=http://127.0.0.1:$PORT" "http://127.0.0.1:8912/__target" > /dev/null
curl -s --noproxy '*' -G --data-urlencode "path=$EV/out/req_$STATE.jsonl" "http://127.0.0.1:8912/__log" > /dev/null
curl -s --noproxy '*' "http://127.0.0.1:8912/__fault?clear=1" > /dev/null

echo "== shot $STATE $(date +%H:%M:%S)"
cd "$EV" || exit 1
NODE_PATH="$ROOT/UI界面/web/node_modules" "$NODE" queries/shot_canvas.mjs \
  "http://127.0.0.1:8912/system-flow.html" "screenshots/state_$STATE.png" "out/state_$STATE.json" 9000 "$FAULT" \
  > "out/shot_$STATE.log" 2>&1
echo "shot rc=$? $(tail -1 "out/shot_$STATE.log")"

[ -n "${TICK_PID:-}" ] && kill "$TICK_PID" 2>/dev/null
kill "$ADAPTER_PID" 2>/dev/null
sleep 1
echo "== done $STATE $(date +%H:%M:%S) (adapter stopped)"
