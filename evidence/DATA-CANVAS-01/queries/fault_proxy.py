#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-CANVAS-01 取证代理（只读）。

作用（三件事，全部只读、只在本机回环）：
  1. 同源：把画布页面 `UI界面/web/public/system-flow.html` 与后端 `/api/v1/*` 放在**同一个 origin**，
     这样画布里的相对路径请求与工作台是同一组接口（不另设数据通道、不跨域、不加头）。
  2. 取证：把每一次转发的 方法/路径/状态码/耗时/字节数 追加到 requests.jsonl，
     用于“截图、响应、时间绑定同一次运行”。
  3. 故障注入：`/__fault?path=/api/v1/paper/status&mode=500` 让**指定路径**开始返回 500，
     `/__fault?clear=1` 恢复。用于验收第 4 态（接口失败），不触碰后端进程本身。

纪律：
  · 只转发 GET/HEAD；写方法一律 405（本工单不写任何生产状态）。
  · 不做缓存、不改写响应体（除了故障注入时的错误体）。
  · 只绑定 127.0.0.1。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CANVAS = os.path.join(PROJECT, "UI界面", "web", "public", "system-flow.html")

STATE = {"faults": {}, "log": None}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕开 HTTP_PROXY


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "dc01-proxy/1.0"

    def log_message(self, *a):  # 静默，日志自己写
        pass

    # ── 工具 ──────────────────────────────────────────────
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _log(self, path, code, ms, nbytes, fault=None):
        rec = {"ts": round(time.time(), 3), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "method": self.command, "path": path, "status": code, "ms": round(ms, 1),
               "bytes": nbytes}
        if fault:
            rec["fault"] = fault
        if STATE["log"]:
            with open(STATE["log"], "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _fault_for(self, path):
        for prefix, mode in STATE["faults"].items():
            if path.startswith(prefix):
                return prefix, mode
        return None, None

    # ── 路由 ──────────────────────────────────────────────
    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self._send(405, b'{"error":"read-only proxy"}')

    def do_GET(self):
        t0 = time.time()
        parts = urlsplit(self.path)
        path = parts.path

        # 控制面
        if path == "/__fault":
            q = parse_qs(parts.query)
            if "clear" in q:
                STATE["faults"] = {}
            else:
                p = (q.get("path") or [""])[0]
                mode = (q.get("mode") or ["500"])[0]
                if p:
                    STATE["faults"][p] = mode
            body = json.dumps({"faults": STATE["faults"]}, ensure_ascii=False).encode()
            self._send(200, body)
            self._log(path + ("?" + parts.query if parts.query else ""), 200, (time.time() - t0) * 1000, len(body))
            return
        if path == "/__target":
            q = parse_qs(parts.query)
            u = (q.get("url") or [""])[0]
            if u:
                STATE["target"] = u.rstrip("/")
            body = json.dumps({"target": STATE["target"]}, ensure_ascii=False).encode()
            self._send(200, body)
            return
        if path == "/__log":
            q = parse_qs(parts.query)
            n = (q.get("path") or [""])[0]
            if n:
                STATE["log"] = os.path.abspath(n)
                open(STATE["log"], "a", encoding="utf-8").close()
            body = json.dumps({"log": STATE["log"]}, ensure_ascii=False).encode()
            self._send(200, body)
            return
        if path == "/__state":
            body = json.dumps({"faults": STATE["faults"], "canvas": CANVAS,
                               "canvas_mtime": os.path.getmtime(CANVAS),
                               "target": STATE["target"], "log": STATE["log"]},
                              ensure_ascii=False).encode()
            self._send(200, body)
            return

        # 页面
        if path in ("/", "/system-flow.html", "/index.html"):
            with open(CANVAS, "rb") as fh:
                body = fh.read()
            self._send(200, body, "text/html; charset=utf-8")
            self._log(path, 200, (time.time() - t0) * 1000, len(body))
            return

        # 故障注入
        prefix, mode = self._fault_for(path)
        if prefix:
            if mode == "500":
                body = json.dumps({"missing_reason": "注入故障：上游接口失败（DATA-CANVAS-01 验收第 4 态）",
                                   "quality": {"status": "error", "note": "fault_injected"}},
                                  ensure_ascii=False).encode()
                self._send(500, body)
            elif mode == "close":
                self.close_connection = True
                try:
                    self.wfile.close()
                except Exception:
                    pass
                body = b""
            else:
                body = b'{"missing_reason":"fault_injected_unknown_mode"}'
                self._send(503, body)
            self._log(path, 500 if mode == "500" else 503, (time.time() - t0) * 1000, len(body), fault=mode)
            return

        # 转发
        target = STATE["target"] + path + (("?" + parts.query) if parts.query else "")
        req = urllib.request.Request(target, method="GET")
        try:
            with OPENER.open(req, timeout=120) as r:
                body = r.read()
                code = r.status
                ctype = r.headers.get("Content-Type", "application/json; charset=utf-8")
        except urllib.error.HTTPError as e:
            body = e.read()
            code = e.code
            ctype = e.headers.get("Content-Type", "application/json; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"missing_reason": f"proxy_upstream_error: {exc}"},
                              ensure_ascii=False).encode()
            code = 502
            ctype = "application/json; charset=utf-8"
        self._send(code, body, ctype)
        self._log(path + (("?" + parts.query) if parts.query else ""), code,
                  (time.time() - t0) * 1000, len(body))


class S(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    address_family = socket.AF_INET


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8911)
    ap.add_argument("--target", default="http://127.0.0.1:8787")
    ap.add_argument("--log", default=os.path.join(HERE, "..", "out", "requests.jsonl"))
    args = ap.parse_args()
    STATE["target"] = args.target.rstrip("/")
    os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
    STATE["log"] = os.path.abspath(args.log)
    print(f"[proxy] 127.0.0.1:{args.port} → {STATE['target']}  canvas={CANVAS}", flush=True)
    S(("127.0.0.1", args.port), H).serve_forever()


if __name__ == "__main__":
    main()
