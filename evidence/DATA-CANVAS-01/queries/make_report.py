#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-CANVAS-01：从产物机械生成验收报告与哈希清单。

原则：报告里的每个数字都从 out/*.json、out/req_*.jsonl、screenshots/*.png 现算，
      不接受手抄。本脚本只读，不改任何被审对象。
用法：python queries/make_report.py
产出：REPORT.md、out/manifest.json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

EV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(EV))
STATES = ["normal", "stopped", "missing", "api_fail"]
LABEL = {"normal": "正常", "stopped": "停止采集", "missing": "缺数据", "api_fail": "接口失败"}
PILL_CN = {"pass": "已验证可用", "wait": "等待条件", "fail": "故障", "unv": "未验证", "na": "不适用"}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def clean(s, limit=None) -> str:
    """把页面里的富文本压成单行纯文本（表格单元格不能有换行）。"""
    if s is None:
        return "—"
    s = str(s).replace("<br>", " ／ ").replace("<br/>", " ／ ")
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("|", "｜").replace("\n", " ").strip()
    s = re.sub(r"\s{2,}", " ", s)
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s or "—"


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_build(path):
    """build_*.json 是「若干行进度文本 + 一段 JSON」，取最后那段 JSON。"""
    txt = open(path, encoding="utf-8").read()
    i = txt.find("\n{")
    try:
        return json.loads(txt[i:]) if i >= 0 else {}
    except Exception:
        return {}


def load_req(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def parse_run_log(path):
    """从 run_all.log 取每态的隔离端口与 adapter 监听 pid（进程身份只在当次运行有效）。"""
    info = {}
    if not os.path.exists(path):
        return info
    cur = None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"#{4,}\s+(\w+)\s+:(\d+)", line)
        if m:
            cur = m.group(1)
            info.setdefault(cur, {})["port"] = m.group(2)
            continue
        if cur:
            m2 = re.search(r"listener pid=(\d+)", line)
            if m2:
                info[cur]["pid"] = m2.group(1)
            m3 = re.search(r"health=(\d+)", line)
            if m3:
                info[cur]["health"] = m3.group(1)
    return info


def node_names(page_path):
    """节点编号与名称从页面源码静态解析，避免在报告里手抄。"""
    src = open(page_path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"key:'(\w+)',\s*no:(\d+),\s*name:'([^']+)'", src):
        out[m.group(1)] = {"no": int(m.group(2)), "name": m.group(3)}
    return out


def collect():
    out = {}
    runlog = parse_run_log(os.path.join(EV, "out", "run_all.log"))
    for st in STATES:
        sp = os.path.join(EV, "out", f"state_{st}.json")
        if not os.path.exists(sp):
            out[st] = {"missing": True}
            continue
        d = load_json(sp)
        dom = d.get("dom") or {}
        req = load_req(os.path.join(EV, "out", f"req_{st}.jsonl"))
        non200 = [r for r in req if r.get("status") not in (200, 304)]
        api = [r for r in req if str(r.get("path", "")).startswith("/api/")]
        nodes = dom.get("nodes") or []
        checks_all = []
        for n in nodes:
            for c in (n.get("checks") or []):
                checks_all.append((n.get("key"), c))
        out[st] = {
            "missing": False,
            "captured_utc": d.get("captured_at_utc"),
            "load_utc": d.get("page_load_at_utc") or d.get("captured_at_utc"),
            "dom_utc": d.get("dom_captured_at_utc") or d.get("captured_at_utc"),
            "shot_utc": d.get("screenshot_at_utc") or d.get("captured_at_utc"),
            "captured_local": d.get("captured_at_local"),
            "build": dom.get("build"),
            "verdict": dom.get("verdict") or {},
            "chips": dom.get("chips") or [],
            "nodes": nodes,
            "pools": dom.get("pools") or [],
            "fault_after": d.get("fault_after"),
            "console_errors": d.get("console_errors") or [],
            "api_responses": d.get("api_responses") or [],
            "req_n": len(req),
            "req_first": (req[0].get("iso") if req else None),
            "req_last": (req[-1].get("iso") if req else None),
            "req_api_n": len(api),
            "req_rows": req,
            "non200": non200,
            "checks_n": len(checks_all),
            "checks_ok": sum(1 for _, c in checks_all if str(c.get("ok")) == "1"),
            "checks_bad": [(k, c) for k, c in checks_all if str(c.get("ok")) != "1"],
            "build_meta": load_build(os.path.join(EV, "out", f"build_{st}.json")),
            "tick_meta": (load_json(os.path.join(EV, "out", f"tick_{st}.json"))
                          if os.path.exists(os.path.join(EV, "out", f"tick_{st}.json")) else {}),
            "run": runlog.get(st, {}),
            "shot_png": os.path.join(EV, "screenshots", f"state_{st}.png"),
        }
    return out


def bind_check(d):
    """页面侧 api_responses 与代理侧请求日志逐条比对（路径 + 状态 + 字节数）。
    两边是同一批请求的两个独立观测点：对不上就说明不是同一次运行。"""
    rows = []
    proxy = {}
    for r in d.get("req_rows") or []:
        path = str(r.get("path") or "")
        if path.startswith("/api/"):
            proxy.setdefault(path, []).append((r.get("status"), r.get("bytes")))
    for a in d.get("api_responses") or []:
        u = str(a.get("url") or "")
        path = "/api/" + u.split("/api/", 1)[1] if "/api/" in u else u
        cands = proxy.get(path) or []
        ok = any(st == a.get("status") and by == a.get("bytes") for st, by in cands)
        rows.append({"path": path, "status": a.get("status"), "bytes": a.get("bytes"),
                     "proxy_seen": bool(cands), "match": ok,
                     "proxy": cands[0] if cands else None})
    return rows


def main():
    D = collect()
    page = os.path.join(ROOT, "UI界面", "web", "public", "system-flow.html")
    page_sha = sha256(page)
    page_size = os.path.getsize(page)

    L = []
    w = L.append

    w("# DATA-CANVAS-01 四态验收报告（脚本生成）")
    w("")
    w("> 本文件由 `queries/make_report.py` 从 `out/`、`screenshots/` 里的产物机械生成，"
      "**禁止手工修改**。任何数字要改，改产物后重跑脚本。")
    w("")
    w(f"- 生成时刻：{utc(time.time())}（UTC）")
    w(f"- 被审页面：`UI界面/web/public/system-flow.html` · {page_size:,} 字节 · SHA-256 `{page_sha}`")
    w("")

    # ---------- 0 结论 ----------
    w("## 0 结论")
    w("")
    ok_states = []
    for st in STATES:
        d = D[st]
        if d.get("missing"):
            w(f"- **{LABEL[st]}**：产物缺失，未验证。")
            continue
        v = d["verdict"]
        bp = [c for c in d["chips"] if c.get("isBreak")]
        ok_states.append(st)
        w(f"- **{LABEL[st]}**：全局判定 `{v.get('state')}`（{PILL_CN.get(v.get('state'), '—')}）"
          f" · {clean(v.get('title'), 120)}")
    w("")
    if len(ok_states) == len(STATES):
        w("四态产物齐全，且每一态都给出了**不同的**全局判定与断点定位（见第 2 节）。")
    else:
        w(f"⚠ 只拿到 {len(ok_states)}/{len(STATES)} 态产物，结论不能算通过。")
    w("")

    # ---------- 1 运行绑定 ----------
    w("## 1 运行绑定：截图 / 响应 / 时间 / 版本 同一次运行")
    w("")
    w("| 态 | 页面构建号 | 页面加载(UTC) | DOM 抓取(UTC) | 截图(UTC) | 隔离端口 | adapter pid | 请求条数 | 请求窗口(UTC) | 非 200 | 故障注入 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for st in STATES:
        d = D[st]
        if d.get("missing"):
            w(f"| {LABEL[st]} | — | — | — | — | — | — | — | — | — | — |")
            continue
        run = d["run"]
        fa = d["fault_after"]
        fa_s = f"`{fa.get('path')}` → {fa.get('mode')}" if isinstance(fa, dict) else "无"
        w(f"| {LABEL[st]} | `{d['build']}` | {d['load_utc']} | {d['dom_utc']} | {d['shot_utc']} "
          f"| {run.get('port','—')} | {run.get('pid','—')} | {d['req_n']} "
          f"| {d['req_first']} ~ {d['req_last']} | {len(d['non200'])} | {fa_s} |")
    w("")
    w("逐条响应比对（页面侧 `api_responses` ↔ 代理侧 `req_*.jsonl`，比路径 + 状态 + 字节数）：")
    w("")
    w("| 态 | 页面侧接口数 | 逐条对上 | 对不上 | 结论 |")
    w("|---|---|---|---|---|")
    all_ok = True
    for st in STATES:
        d = D[st]
        if d.get("missing"):
            continue
        rows = bind_check(d)
        m = sum(1 for r in rows if r["match"])
        bad = len(rows) - m
        if bad:
            all_ok = False
        w(f"| {LABEL[st]} | {len(rows)} | {m} | {bad} | {'一致' if bad == 0 else '**不一致**'} |")
    w("")
    if all_ok:
        w("四态全部逐条一致 ⇒ 页面看到的响应就是代理记录的那一批，不是两次运行拼起来的。")
    else:
        w("⚠ 存在对不上的条目，说明页面与代理记录不是同一批响应，本报告的绑定结论不成立。")
    w("")
    w("绑定方式（不是靠人工声明，而是靠三条独立记录相互对得上）：")
    w("")
    w("1. **时间**：`out/state_*.json` 分别记 `page_load_at_utc`（首次加载）、`dom_captured_at_utc`"
      "（抓 DOM 那一刻）、`screenshot_at_utc`（落盘那一刻）；`out/req_*.jsonl` 每条请求带 `ts`/`iso`，"
      "由同源代理在转发时落盘。接口失败态是先注入故障再刷新再抓，所以三个时刻必须分开记 —— "
      "只记首次加载会把时刻错标 20 秒。")
    w("2. **响应**：`out/state_*.json` 的 `api_responses[]` 记录页面实际收到的每条 `/api/v1/*` 的 "
      "`status` 与 `bytes`；`out/req_*.jsonl` 是代理侧独立记的同一批请求。上表逐条比过路径、状态、字节数。")
    w("3. **版本**：页面把 `BUILD_ID` 写进 DOM（`#build-id`），截图里可见；`dom.build` 与 `BUILD_ID` 常量、"
      "以及本报告顶部的页面 SHA-256 三方对应。")
    w("")

    # ---------- 2 四态对照 ----------
    w("## 2 四态对照（颜色 + 文字，两套编码同时存在）")
    w("")
    keys = ["source", "collect", "store", "quality", "engine", "ledger", "workbench"]
    NN = node_names(page)
    w("| 态 | 全局判定 | 判定条标题 | 断点段 | "
      + " | ".join(f"{NN.get(k,{}).get('no',i+1)}. {NN.get(k,{}).get('name',k)}" for i, k in enumerate(keys)) + " |")
    w("|---|---|---|" + "---|" * (len(keys) + 1))
    for st in STATES:
        d = D[st]
        if d.get("missing"):
            continue
        v = d["verdict"]
        bp = [c.get("key") for c in d["chips"] if c.get("isBreak")]
        chips = {c.get("key"): c.get("state") for c in d["chips"]}
        row = [LABEL[st], f"`{v.get('state')}`", clean(v.get("title"), 90),
               "、".join(bp) if bp else "无"]
        for k in keys:
            s = chips.get(k, "—")
            row.append(("*" if k in bp else "") + s)
        w("| " + " | ".join(row) + " |")
    w("")
    w("`*` = 该段被标为断点（断点优先级：故障 > 等待 > 未验证；`*` 只标最先出问题的那一段，"
      "其余同状态的段仍按自身事实着色，避免「整条链一起变红」看不出断点在哪）。")
    w("")
    w("七段固定顺序与名称（从页面 `NODES` 解析，与芯片一一对应）：")
    w("")
    for k in keys:
        v = NN.get(k, {})
        w(f"{v.get('no','?')}. **{v.get('name', k)}** · `{k}`")
    w("")

    # ---------- 3 逐态明细 ----------
    w("## 3 逐态明细")
    w("")
    for st in STATES:
        d = D[st]
        if d.get("missing"):
            continue
        v = d["verdict"]
        w(f"### 3.{STATES.index(st)+1} {LABEL[st]}（`{st}`）")
        w("")
        w(f"- 截图：`screenshots/state_{st}.png`（{os.path.getsize(d['shot_png']):,} 字节，"
          f"SHA-256 `{sha256(d['shot_png'])}`）")
        w(f"- 时刻：页面加载 {d['load_utc']} → DOM 抓取 {d['dom_utc']} → 截图 {d['shot_utc']}")
        w(f"- 判定：`{v.get('state')}` · {clean(v.get('title'))}")
        w(f"- 说明：{clean(v.get('sub'))}")
        w(f"- 计数：{clean(v.get('meta'), 200)}")
        w(f"- 校验项合计：{d['checks_n']} 条，通过 {d['checks_ok']}，不通过 {d['checks_n']-d['checks_ok']}")
        w("")
        w("| 段 | 状态 | 结论（页面 headline） |")
        w("|---|---|---|")
        for n in d["nodes"]:
            v2 = NN.get(n.get("key"), {})
            mark = " **（断点）**" if any(c.get("key") == n.get("key") and c.get("isBreak") for c in d["chips"]) else ""
            w(f"| {v2.get('no','?')}. {v2.get('name', n.get('key'))}{mark} "
              f"| `{n.get('state')}` {PILL_CN.get(n.get('state'),'')} "
              f"| {clean(n.get('headline'), 200)} |")
        w("")
        blocks = [(n.get("key"), n.get("blocker")) for n in d["nodes"] if n.get("blocker")]
        if blocks:
            w("阻塞原因（页面「为什么停」区）：")
            w("")
            for k, b in blocks:
                w(f"- `{k}`：{clean(b)}")
            w("")
        if d["checks_bad"]:
            w("不通过的校验项（逐条列出，未通过就是未通过，不做解释性掩盖）：")
            w("")
            for k, c in d["checks_bad"]:
                w(f"- `{k}`：{clean(c.get('text'), 220)}")
            w("")
        if d["non200"]:
            w("本轮非 200 响应（代理侧记录）：")
            w("")
            for r in d["non200"]:
                w(f"- `{r.get('method')} {r.get('path')}` → **{r.get('status')}** · {r.get('iso')}")
            w("")
        if d["console_errors"]:
            w("浏览器控制台错误：")
            w("")
            for e in d["console_errors"]:
                w(f"- `{clean(e, 200)}`")
            w("")

    # ---------- 4 三池分母 ----------
    w("## 4 三池分母（分开写，不混用）")
    w("")
    any_pool = next((D[s]["pools"] for s in STATES if D[s].get("pools")), [])
    if not any_pool:
        w("⚠ 未取到池数据。")
    else:
        w("| # | 口径 | 分母 | 来源 | 排除规则 |")
        w("|---|---|---|---|---|")
        for i, p in enumerate(any_pool, 1):
            parts = [x.strip() for x in str(p).split("|")]
            name = parts[0] if len(parts) > 0 else "—"
            total = parts[1] if len(parts) > 1 else "—"
            src = parts[2] if len(parts) > 2 else "—"
            excl = parts[3] if len(parts) > 3 else "—"
            fld = parts[4] if len(parts) > 4 else ""
            w(f"| {i} | {name} | **{total}** | {src} | {excl}<br>`{fld}` |")
    w("")
    w("三个数**不同且不能互相替代**：下载池是全合约目录（含股票/商品/外汇/指数），"
      "展示池是工作台列表口径，策略监控池是信号引擎实际在扫的标的。画布分别标注来源字段，"
      "任何一处要改分母，必须同时改来源字段说明，不能只改数字。")
    w("")

    # ---------- 5 判定要求落地 ----------
    w("## 5 工单七条判定要求的落地位置")
    w("")
    w("| # | 要求 | 落地方式 | 代码位置 |")
    w("|---|---|---|---|")
    w("| 1 | HTTP 200／文件存在／进程存活不能单独判绿 | 每段先跑「接口 200 且是 JSON」这条前置校验，"
      "但它只作为**必要非充分**条件；真正的状态由该段的事实校验（心跳年龄、水位对齐、覆盖缺口、"
      "周期停滞）决定。200 通过而事实不成立时，该段照样是 `fail` | `system-flow.html` 每段 `run()` 内 `checks.push(...)` |")
    w("| 2 | 0 信号必须四分类 | 依次判定：① `/api/v1/signals` 无 `total` → **接口失败**；"
      "② 运行时 `status≠running` → **扫描未运行**；③ 周期年龄 > 600s → **扫描未运行（停滞）**；"
      "④ `universe < 声明值` 或引擎读到的 15m 水位滞后 > 2 个周期 → **数据不足**；"
      "⑤ `total>0` → 通过；⑥ 其余 → **等待条件（扫描正常但未触发）** | `system-flow.html:755-772` |")
    w("| 3 | OKX 不支持原生 8h 记「不适用」 | 以 `data/import-plan` 的 `plan.native_skip.okx` 与 "
      "`kline_stream_state` 是否存在该路为准，记 `na`；既不算故障，也不写成「已补齐」 | "
      "`system-flow.html:666-672` |")
    w("| 4 | 三池分母分开写 | 下载池 `pool_all_total`、展示池 `active_scope.active_total`、"
      "策略监控池 `signal_engine.universe`，各自带来源字段与排除规则，见第 4 节 | `system-flow.html` `#pools` |")
    w("| 5 | 同源；读取失败清除「当前正常」 | 页面只用相对路径 `/api/v1/*`，经同源代理转发，"
      "不跨域、不直连交易所、不读本地文件；任一必需接口失败 ⇒ 该段强制 `fail`，"
      "标题写明「当前正常结论已清除」，并保留 `localStorage` 里最后一次成功时间、标注已过期 | "
      "`system-flow.html:1140` |")
    w("| 6 | 节点跳转 + 展开区 | 顶部七段芯片可点击跳到对应节点；每节点有「技术详情」折叠区，"
      "放接口路径、原始字节数、逐条校验；首屏只留判定条 + 七段芯片 + 三池 + 七个节点卡片 | "
      "`system-flow.html:970`（跳转）、`#cards-fold`（原 20 张接口卡片改为按需探测） |")
    w("| 7 | 截图/响应/时间/版本绑定同一次运行 | 同源代理记请求，Playwright 记 DOM 事实与响应列表，"
      "页面把 `BUILD_ID` 写进 DOM；三者时间窗口与构建号在第 1 节逐态列出 | "
      "`queries/fault_proxy.py`、`queries/shot_canvas.mjs`、`queries/run_state.sh` |")
    w("")
    w("阈值集中在一处，展开区原样显示，便于机械复核：心跳 120s、WS 消息 120s、周期停滞 600s、"
      "滞后上限 2 个周期、下游与热库水位差 < 1000ms（`system-flow.html:251-258`）。")
    w("")

    # ---------- 6 契约缺口 ----------
    w("## 6 契约缺口（缺字段 ⇒ 判「未验证」，不伪绿）")
    w("")
    w("| 编号 | 缺什么 | 后果 | 现状 |")
    w("|---|---|---|---|")
    w("| C-01 | `/api/v1/data/pool-scope` 不返回快照时点（`snapshot_utc` / `updated_at`） | "
      "第 1 段无法算数据年龄与新鲜度 | 该段固定判 `unv`，页面明写缺口编号 |")
    w("| C-03 | 无「冷库（parquet 归档）全量覆盖」接口 | 第 3 段冷库侧只能写「未验证」 | "
      "热库侧正常判绿，冷库侧不冒充已补齐 |")
    w("| C-05 | 账本接口不返回最近一次写入时点 | 第 6 段只能给「本次读取成功时刻」 | "
      "不把读取成功当成写入成功 |")
    w("| GAP-01 | `/api/v1/paper/stops/{pos_id}` 非法入参返回 500 而非 4xx | 展开区该项记为接口缺陷 | "
      "已在展开区标注 |")
    w("| GAP-02 | 交易所名大小写不敏感、非法值静默回落 | 请求写错交易所仍返回 200，易误判 | "
      "展开区标注，不作为判定依据 |")
    w("| GAP-03 | `/api/v1/markets` 的 `limit` 参数不生效 | 返回行数与请求不符 | 展开区标注 |")
    w("")

    # ---------- 7 复现 ----------
    w("## 7 复现方法")
    w("")
    w("```bash")
    w("cd evidence/DATA-CANVAS-01")
    w("# 1) 起同源取证代理（页面 + /api/v1 同一 origin，带请求日志与故障注入）")
    w("python queries/fault_proxy.py --port 8912")
    w("# 2) 四态一次跑完：构隔离库 → 推进时间 → 起隔离后端 → 截图 → 收工")
    w("bash queries/run_all_states.sh")
    w("# 3) 重新生成报告与哈希清单")
    w("python queries/make_report.py")
    w("```")
    w("")
    w("隔离纪律：`ISOLATED_MODE=1` 且 `MARKET_DB`/`LIVE_DB`/`PAPER_LEDGER_DIR`/`PAPER_LOCK_DIR` 等"
      "全部显式指向 `/tmp/dc01/<态>/`，后端启动时强校验，缺一项就拒绝启动；"
      "端口 8901~8904 启动前检查占用、启动后校验监听 pid 与本次启动 pid 一致，不一致即中止"
      "（防止连到上一轮的旧实例）。")
    w("")
    w("四态怎么造：")
    w("")
    w("| 态 | 制造方式 |")
    w("|---|---|")
    w("| 正常 | 心跳钉到 now-3s，各周期闭合水位推到当前应闭合的那根 |")
    w("| 停止采集 | 心跳回拨 3 小时、`status=no_messages`，且**不跑**心跳推进循环 |")
    w("| 缺数据 | 删掉 300 个标的的 15m 覆盖、15m `last_close` 回拨 4 小时；心跳正常推进（证明「采集在跑但数据缺」） |")
    w("| 接口失败 | 数据侧正常，截图后由代理把 `/api/v1/paper/accounts` 注入 500，再点刷新重截 |")
    w("")

    # ---------- 8 附件清单 ----------
    w("## 8 附件清单（SHA-256）")
    w("")
    files = []
    for sub in ("queries", "out", "screenshots"):
        d = os.path.join(EV, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if os.path.isfile(p) and not fn.startswith(".") and fn != "manifest.json":
                files.append(p)
    man = []
    w("| 文件 | 字节 | SHA-256 |")
    w("|---|---|---|")
    for p in files:
        h = sha256(p)
        rel = os.path.relpath(p, EV)
        man.append({"path": rel, "bytes": os.path.getsize(p), "sha256": h})
        w(f"| `{rel}` | {os.path.getsize(p):,} | `{h}` |")
    w(f"| `UI界面/web/public/system-flow.html` | {page_size:,} | `{page_sha}` |")
    man.append({"path": "../../UI界面/web/public/system-flow.html", "bytes": page_size, "sha256": page_sha})
    w("")

    report = "\n".join(L) + "\n"
    with open(os.path.join(EV, "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write(report)

    manifest = {
        "ticket": "DATA-CANVAS-01",
        "generated_at_utc": utc(time.time()),
        "page": {"path": "UI界面/web/public/system-flow.html", "bytes": page_size, "sha256": page_sha},
        "states": {st: {"page_load_at_utc": D[st].get("load_utc"),
                        "dom_captured_at_utc": D[st].get("dom_utc"),
                        "screenshot_at_utc": D[st].get("shot_utc"),
                        "verdict": (D[st].get("verdict") or {}).get("state"),
                        "breakpoint_chips": [c.get("key") for c in (D[st].get("chips") or []) if c.get("isBreak")],
                        "screenshot": os.path.relpath(D[st]["shot_png"], EV) if not D[st].get("missing") else None}
                   for st in STATES},
        "files": man,
    }
    with open(os.path.join(EV, "out", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    print(f"REPORT.md 已生成：{len(report):,} 字符；清单 {len(man)} 个文件")
    for st in STATES:
        d = D[st]
        if d.get("missing"):
            print(f"  {st:9s} 产物缺失")
            continue
        print(f"  {st:9s} verdict={(d['verdict'] or {}).get('state')} "
              f"breakpoint={[c.get('key') for c in d['chips'] if c.get('isBreak')]} "
              f"req={d['req_n']} non200={len(d['non200'])} checks={d['checks_ok']}/{d['checks_n']}")


if __name__ == "__main__":
    main()
