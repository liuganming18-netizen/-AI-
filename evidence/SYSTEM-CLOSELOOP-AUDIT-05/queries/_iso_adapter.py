# -*- coding: utf-8 -*-
"""隔离 adapter 启动器（跳过 main() 的预热与回测启动钩子，只起 HTTP 服务）。

用途：给 03 号探针一个「跑交付代码、但账本在 /tmp/wx_audit05」的实例。
生产 8787 与 数据/paper 全程不参与。
"""
from __future__ import annotations

import os
import socket
import sys
import threading

BACKEND = "/Users/boos/Desktop/威科夫因子共振趋势/框架/backend"
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import adapter as A                                        # noqa: E402

if __name__ == "__main__":
    specs = [("127.0.0.1", socket.AF_INET)]
    servers = []
    for host, fam in specs:
        servers.append(A._loopback_server(host, fam))
    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[iso-adapter] 监听 127.0.0.1:{A.PORT}  db={A.DB_PATH}", flush=True)
    servers[0].serve_forever()
