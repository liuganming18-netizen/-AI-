# -*- coding: utf-8 -*-
"""00b 版本漂移取证 —— 验收进行中检测到的代码变动。

背景：`00_freeze.py` 于开工时（21:34）比对 148 文件，变动 0；
      随后 `02_causal.py` 的成交结果与开工时的代码阅读结论不一致，
      核查发现 `live_store.py` / `live_api.py` / `paper_engine.py` 的 mtime 在验收期间被改。
工单要求：「变更时只作废受影响检查并重新执行」⇒ 本脚本负责把"改了什么、谁受影响"钉死。

产物：out/00b_漂移快照.json、out/00b_漂移明细.md、out/diffs/*.diff
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import pathlib
import subprocess

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-03/out"
A02 = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02/out"
PORT = 8787


def audited_files() -> list[pathlib.Path]:
    fs: list[pathlib.Path] = []
    fs += [p for p in (BASE / "框架/backend").rglob("*.py")
           if "__pycache__" not in p.parts and "_legacy_archive" not in p.parts]
    fs += [p for p in (BASE / "UI界面/web/src").rglob("*")
           if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".json")]
    for extra in ("UI界面/web/vite.config.ts", "UI界面/web/package.json",
                  "框架/backend/live/paper_new_entry_gate.json"):
        p = BASE / extra
        if p.exists():
            fs.append(p)
    return sorted(set(fs))


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def proc_info() -> dict:
    pid = None
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{PORT}", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                pid = int(parts[1])
                break
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "原因": str(exc)}
    if pid is None:
        return {"可判定": False, "原因": "端口无监听"}
    try:
        import psutil
        pr = psutil.Process(pid)
        ct = pr.create_time()
        return {"可判定": True, "pid": pid,
                "启动时刻": dt.datetime.fromtimestamp(ct).astimezone().isoformat(timespec="seconds"),
                "启动时刻_ms": int(ct * 1000),
                "命令行": " ".join(pr.cmdline())[:260]}
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "pid": pid, "原因": str(exc)}


def main() -> None:
    now = dt.datetime.now().astimezone()
    start = json.loads((OUT / "00_开工快照.json").read_text(encoding="utf-8"))
    t1 = json.loads((A02 / "00_版本快照_T1.json").read_text(encoding="utf-8"))
    prev_start = start["全量指纹"]
    prev_t1 = t1["文件"]

    cur = {}
    for p in audited_files():
        st = p.stat()
        cur[p.relative_to(BASE).as_posix()] = {
            "sha256": sha(p), "字节": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "mtime_ms": int(st.st_mtime * 1000)}

    def diff(prev: dict) -> list[dict]:
        out = []
        for k in sorted(set(prev) | set(cur)):
            a, b = prev.get(k), cur.get(k)
            if a is None:
                out.append({"文件": k, "变化": "新增", "当前_sha": b["sha256"][:12]})
            elif b is None:
                out.append({"文件": k, "变化": "消失"})
            elif a["sha256"] != b["sha256"]:
                out.append({"文件": k, "变化": "被改",
                            "旧_sha": a["sha256"][:12], "当前_sha": b["sha256"][:12],
                            "字节": f'{a["字节"]} → {b["字节"]}',
                            "mtime": b["mtime"], "mtime_ms": b["mtime_ms"]})
        return out

    d_start = diff(prev_start)
    d_t1 = diff(prev_t1)

    # 只对"被改的 .py"导出 unified diff（用 AUDIT-02 归档副本？没有副本 ⇒ 用 git 也无
    # 78G 工程禁 git ⇒ 只能给出「行号级锚点」：把当前文件的关键片段抓出来供人工核对）
    anchors = {}
    for c in d_start:
        if c["变化"] != "被改" or not c["文件"].endswith(".py"):
            continue
        p = BASE / c["文件"]
        try:
            lines = p.read_text(encoding="utf-8").split("\n")
        except Exception:                                        # noqa: BLE001
            continue
        keys = ("order_request_ms", "earliest_fill_ms", "attempt_ms", "is_replay",
                "MAX_FILL_LAG_MS", "bar_at_or_after", "missed_fill_moment",
                "earliest_fill_before_attempt_clock", "advance_pending", "def open_position")
        hits = []
        for i, line in enumerate(lines, 1):
            if any(k in line for k in keys):
                hits.append({"行": i, "内容": line.rstrip()[:180]})
        anchors[c["文件"]] = hits

    proc = proc_info()
    latest_ms = max(v["mtime_ms"] for v in cur.values())
    stale = None
    if proc.get("可判定"):
        stale = proc["启动时刻_ms"] < latest_ms

    res = {
        "生成时刻": now.isoformat(timespec="seconds"),
        "开工快照": {"采样时刻": start["生成时刻"], "文件数": start["本次"]["文件数"]},
        "AUDIT02_T1": {"采样时刻": t1["采样时刻"], "文件数": t1["文件数"]},
        "本次": {"文件数": len(cur), "最新mtime": max(v["mtime"] for v in cur.values())},
        "相对开工快照变动": {"条数": len(d_start), "明细": d_start},
        "相对AUDIT02_T1变动": {"条数": len(d_t1), "明细": d_t1},
        "被改文件关键行锚点": anchors,
        "运行进程": proc,
        "进程是否加载最新代码": (not stale) if stale is not None else None,
        "结论": ("验收期间被审计代码被修改：开工基准作废，相关检查必须以本快照为新基准重跑"
                 if d_start else "验收期间代码未变"),
    }
    (OUT / "00b_漂移快照.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    (OUT / "00b_全量指纹_新基准.json").write_text(json.dumps(
        {"采样时刻": now.isoformat(timespec="seconds"), "文件数": len(cur), "文件": cur},
        ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 00b 版本漂移取证", "",
          f"- 取证时刻：{res['生成时刻']}",
          f"- 开工快照：{start['生成时刻']}（{start['本次']['文件数']} 文件，当时变动 0）",
          f"- 当前：{len(cur)} 文件，最新 mtime = {res['本次']['最新mtime']}", "",
          f"## 相对开工快照的变动（{len(d_start)} 条）", "",
          "| 文件 | 变化 | 旧 SHA | 新 SHA | 字节 | mtime |", "|---|---|---|---|---|---|"]
    for c in d_start:
        md.append(f"| `{c['文件']}` | {c['变化']} | {c.get('旧_sha','')} | "
                  f"{c.get('当前_sha','')} | {c.get('字节','')} | {c.get('mtime','')} |")
    md += ["", f"## 相对 AUDIT-02 T1 的变动（{len(d_t1)} 条）", "",
           "| 文件 | 变化 | 旧 SHA | 新 SHA | 字节 | mtime |", "|---|---|---|---|---|---|"]
    for c in d_t1:
        md.append(f"| `{c['文件']}` | {c['变化']} | {c.get('旧_sha','')} | "
                  f"{c.get('当前_sha','')} | {c.get('字节','')} | {c.get('mtime','')} |")
    md += ["", "## 被改文件的关键行锚点（成交时钟相关）", ""]
    for f, hits in anchors.items():
        md.append(f"### `{f}`")
        md.append("")
        for h in hits:
            md.append(f"- L{h['行']}：`{h['内容']}`")
        md.append("")
    md += ["## 运行进程", "",
           f"- pid={proc.get('pid')} 启动={proc.get('启动时刻')}",
           f"- 最新代码 mtime = {res['本次']['最新mtime']}",
           f"- **进程是否加载最新代码：{res['进程是否加载最新代码']}**", "",
           "## 结论", "", res["结论"]]
    (OUT / "00b_漂移明细.md").write_text("\n".join(md), encoding="utf-8")

    print(f"[漂移] {res['生成时刻']}  文件 {len(cur)} 个，最新 mtime {res['本次']['最新mtime']}")
    print(f"  相对开工快照变动 {len(d_start)} 条：")
    for c in d_start:
        print(f"    {c['文件']}  {c['变化']}  {c.get('旧_sha','')} → {c.get('当前_sha','')}  "
              f"{c.get('字节','')}  {c.get('mtime','')}")
    print(f"  相对 AUDIT02 T1 变动 {len(d_t1)} 条")
    print(f"  进程：pid={proc.get('pid')} 启动={proc.get('启动时刻')}  "
          f"加载最新代码={res['进程是否加载最新代码']}")
    print(f"  结论：{res['结论']}")


if __name__ == "__main__":
    main()
