#!/bin/sh
# 只启动已验收页面；8790 使用本机既有只读网关。
set -eu
code_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$code_root"
export PORT=3010
export GM_DATA_API_URL=http://127.0.0.1:8790
exec node server.js
