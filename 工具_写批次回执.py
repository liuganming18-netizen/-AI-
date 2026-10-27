# -*- coding: utf-8 -*-
"""
批次回执工具：为一次批量回测补一份「谁、何时、用什么代码、跑了什么、产出什么」的记录。

背景：
    回测本身已经留下痕迹（每个运行目录有 manifest.json 的文件哈希、
    inputs.json 里的代码指纹），但缺一层「操作者 + 时间 + 目的」的记录，
    导致事后无法回答「这批结果是谁在什么时候因为什么跑出来的」。

用法：
    python 工具_写批次回执.py --summary 汇总文件.json --operator "AI 助手" --note "说明"

输出：
    在汇总文件同目录下生成 回执_<时间戳>.json，并打印其路径。
"""

import argparse
import datetime
import hashlib
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_receipt(summary_path, operator, note, extra=None):
    summary_path = os.path.abspath(summary_path)
    if not os.path.exists(summary_path):
        raise FileNotFoundError(summary_path)

    # 代码指纹：证明这次用的是哪一份代码
    try:
        from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
        fp = EventTrendRunner().code_fingerprint()
        code = {"combined_sha256": fp.get("combined_sha256"),
                "file_count": len(fp.get("files") or {}),
                "missing": fp.get("missing")}
    except Exception as exc:
        code = {"error": str(exc)}

    # 版本库状态：证明改动是否已提交
    vcs = {}
    try:
        import subprocess
        vcs["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True).stdout.strip() or None
        raw = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True).stdout
        lines = [l for l in raw.splitlines() if l.strip()]
        # 改动可能上百条，只记数量、摘要哈希与前若干条，避免回执本身变成垃圾场
        vcs["dirty_count"] = len(lines)
        vcs["dirty_list_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        vcs["dirty_sample"] = lines[:20]
    except Exception as exc:
        vcs = {"error": str(exc)}

    now = datetime.datetime.now(datetime.timezone.utc)
    receipt = {
        "生成时间": now.isoformat(),
        "操作者": operator,
        "说明": note,
        "汇总文件": os.path.basename(summary_path),
        "汇总文件sha256": sha256_of(summary_path),
        "代码指纹": code,
        "版本库状态": vcs,
    }
    if extra:
        receipt["补充"] = extra

    out = os.path.join(os.path.dirname(summary_path),
                       "回执_%s.json" % now.strftime("%Y%m%dT%H%M%SZ"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    return out


def append_batch_log(*, window_start, window_end, symbol_count, trade_count,
                     net_pnl, violation_legs, runs_subdir, operator,
                     summary_path, detail_lines=None):
    """把一次批次追加进「回测日志/批次运行日志.md」（只增不删）。"""
    log_path = os.environ.get("BATCH_LOG_PATH") or os.path.join(
        PROJECT_ROOT, "回测日志", "批次运行日志.md")
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    row = "| %s | %s ~ %s | %d | %d | %+.2f | %d | %s | %s |\n" % (
        stamp, window_start, window_end, symbol_count, trade_count,
        net_pnl, violation_legs, runs_subdir, operator)

    if not os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("# 批次运行日志\n\n> 每次批量回测自动追加一条。只增不删。\n\n"
                    "| 时间(UTC) | 区间 | 标的数 | 成交笔数 | 净盈亏(U) | 越界腿 | 运行目录 | 操作者 |\n"
                    "|---|---|---|---|---|---|---|---|\n")
    with open(log_path, "r", encoding="utf-8") as f:
        text = f.read()

    # 表格行插到表头最后一行之后
    marker = "|---|---|---|---|---|---|---|---|\n"
    if marker in text:
        head, rest = text.split(marker, 1)
        # 表格行紧跟在表头后面
        lines = rest.split("\n")
        i = 0
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
        lines.insert(i, row.rstrip("\n"))
        text = head + marker + "\n".join(lines)
    else:
        text += row

    # 明细追加到文末
    detail = "\n### %s — %s ~ %s，%d 标的\n\n" % (stamp, window_start, window_end, symbol_count)
    detail += "- 汇总：`%s`\n" % os.path.relpath(summary_path, PROJECT_ROOT)
    detail += "- 成交 %d 笔，净盈亏 %+.2f U，越界腿 %d\n" % (trade_count, net_pnl, violation_legs)
    for line in (detail_lines or []):
        detail += "- %s\n" % line
    text = text.rstrip("\n") + "\n" + detail

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(text)
    return log_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--operator", default="AI 助手")
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    out = write_receipt(args.summary, args.operator, args.note)
    print("回执已写入：%s" % out)


if __name__ == "__main__":
    main()
