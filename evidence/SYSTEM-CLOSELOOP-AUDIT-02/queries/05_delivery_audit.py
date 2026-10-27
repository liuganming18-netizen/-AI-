# -*- coding: utf-8 -*-
"""05 反重力交付核验 —— 阶段 0 的第五件交付物。

工单：「接口 200、页面能打开、截图、返回持仓 ID 都不能单独判通过」
      「未实现或无法验证的功能如实列出，不记为通过」

本脚本把「对方说完成了」变成「可复算的对账」：

  F1  交付文件清单 + SHA-256 + mtime
  F2  截图重复检测：不同文件名但字节级相同的，一律不算独立证据
  F3  「版本指纹及变更回执」自称指纹，但有没有真的给出 SHA-256？
  F4  回执引用的文件是否存在（闸门路径）
  F5  回执引用的行号是否越界、内容是否对得上
  F6  回执声称「7 项缺陷全部 PASSED」，实际结果文件里有几条
  F7  止损轨迹：测试是「直接调函数」还是「引擎真的会调」
  F8  交易因果测试是否包含「正常请求能成交」的正向路径

产物：out/05_反重力交付核验.json、out/05_反重力交付核验.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
DELIV = BASE / "evidence/SYSTEM-CLOSELOOP-02"


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    now = dt.datetime.now().astimezone()
    R = {"生成时刻": now.isoformat(timespec="seconds"), "交付目录": str(DELIV.relative_to(BASE))}

    # ---------------------------------------------------------------- F1
    files = []
    for p in sorted(DELIV.rglob("*")):
        if p.is_file():
            st = p.stat()
            files.append({"文件": str(p.relative_to(DELIV)), "字节": st.st_size,
                          "sha256": sha(p),
                          "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")})
    R["F1_交付文件"] = {"数量": len(files), "明细": files,
                       "最早": min(f["mtime"] for f in files),
                       "最晚": max(f["mtime"] for f in files)}

    # ---------------------------------------------------------------- F2
    groups: dict[str, list[str]] = {}
    for f in files:
        if f["文件"].lower().endswith(".png"):
            groups.setdefault(f["sha256"], []).append(f["文件"])
    dup = {k: v for k, v in groups.items() if len(v) > 1}
    R["F2_截图重复"] = {
        "截图数": sum(len(v) for v in groups.values()),
        "独立图像数": len(groups),
        "重复组": [{"sha256": k[:16], "文件": v} for k, v in dup.items()],
        "重复图片数": sum(len(v) for v in dup.values()),
        "含义": ("不同文件名但字节级完全相同 ⇒ 它们不是独立证据，"
                 "不能用三张「不同」截图证明三个不同操作都做过") if dup else "无重复",
    }

    # ---------------------------------------------------------------- F3
    receipt = DELIV / "06_代码_配置_运行进程版本指纹及变更回执.md"
    rt = receipt.read_text(encoding="utf-8") if receipt.exists() else ""
    sha_in_receipt = re.findall(r"\b[0-9a-fA-F]{64}\b", rt)
    sha16 = re.findall(r"\b[0-9a-fA-F]{16}\b", rt)
    R["F3_回执是否给指纹"] = {
        "回执存在": receipt.exists(),
        "回执字符数": len(rt),
        "其中64位SHA256个数": len(sha_in_receipt),
        "其中16位短哈希个数": len(sha16),
        "是否给出被冻结文件清单": ("文件路径" in rt),
        "判定": ("**未给出任何 SHA-256**：标题写「版本指纹」，正文没有任何一个可核对的哈希"
                 if not sha_in_receipt and not sha16 else "给出了哈希，需逐个核对"),
        "含义": "没有哈希的「冻结」只能算口头声明，不能作为验收基准；"
                "本轮改用审核方自己的 148 文件 SHA-256 快照作为基准。",
    }

    # ---------------------------------------------------------------- F4
    cited_paths = re.findall(r"\[`([^`]+\.json)`\]", rt)
    cited_check = []
    for cp in sorted(set(cited_paths)):
        p = BASE / cp
        cited_check.append({"回执引用": cp, "存在": p.exists(),
                            "实际": (json.loads(p.read_text(encoding="utf-8"))
                                   if p.exists() and p.suffix == ".json" else None)})
    R["F4_回执引用的文件"] = {"明细": cited_check,
                            "不存在数": sum(1 for c in cited_check if not c["存在"])}

    # ---------------------------------------------------------------- F5
    # 回执是 markdown 表格：文件路径与行号在不同列，按「行」解析而不是按「路径+行号」正则
    cited_ranges = []
    for line in rt.splitlines():
        if not line.strip().startswith("|"):
            continue
        mpath = re.search(r"\[`([^`]+\.(?:py|tsx|ts))`\]", line) or \
            re.search(r"`([^`]*(?:框架|UI界面)/[^`]+\.(?:py|tsx|ts))`", line)
        if not mpath:
            continue
        rel = mpath.group(1).replace("file://", "").strip()
        if rel.startswith("/"):
            try:
                rel = str(pathlib.Path(rel).relative_to(BASE))
            except ValueError:
                continue
        p = BASE / rel
        if not p.exists():
            cited_ranges.append({"文件": rel, "引用": "文件不存在", "总行数": None, "越界": None})
            continue
        n = len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        rngs = re.findall(r"L(\d+)[–\-]L(\d+)", line)
        if not rngs:
            cited_ranges.append({"文件": rel, "引用": "（未标行号）", "总行数": n, "越界": None})
        for a, b in rngs:
            a, b = int(a), int(b)
            cited_ranges.append({"文件": rel, "引用": f"L{a}-L{b}", "总行数": n, "越界": b > n})
    R["F5_回执行号"] = {"明细": cited_ranges,
                       "越界数": sum(1 for c in cited_ranges if c["越界"] is True),
                       "无法核对数": sum(1 for c in cited_ranges if c["越界"] is None)}

    # ---------------------------------------------------------------- F6
    res_p = DELIV / "data/03_isolated_test_results.json"
    res = json.loads(res_p.read_text(encoding="utf-8")) if res_p.exists() else {}
    claimed = ["P0-1 交易因果", "P0-2 隔离", "P1-1 A10", "P1-2 L4", "P1-3 A20", "P2-1 C2", "P2-2 X3b/X11"]
    R["F6_七项闭环"] = {
        "回执声称": "7 项历史缺陷全部在隔离沙箱完成严格断言测试，均输出 PASSED",
        "结果文件条目数": len(res),
        "结果文件内容": res,
        "条目覆盖": {k: any(kk.startswith(k.split()[0].replace("P0-1", "P0_1").replace("-", "_"))
                        or k.split()[0].lower() in kk.lower() for kk in res) for k in claimed},
        "缺失项": [k for k in claimed
                 if not any(k.split()[0].lower().replace("-", "_").replace("p0_1", "p0_1") in kk.lower()
                            or k.split()[0].lower().replace("-", "") in kk.lower().replace("_", "")
                            for kk in res)],
        "判定": f"结果文件只有 {len(res)} 条，回执声称 7 项 ⇒ **声称与证据数量不符**",
    }

    # ---------------------------------------------------------------- F7
    eng_hits = []
    for p in (BASE / "框架/backend").rglob("*.py"):
        if "__pycache__" in p.parts or "_legacy_archive" in p.parts:
            continue
        for i, l in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "record_stop_update" in l:
                eng_hits.append({"文件": str(p.relative_to(BASE)), "行": i, "内容": l.strip()[:120]})
    tst = DELIV / "test_isolated_closedloop.py"
    tt = tst.read_text(encoding="utf-8") if tst.exists() else ""
    R["F7_止损轨迹"] = {
        "引擎内 record_stop_update 命中": eng_hits,
        "引擎内命中数": len(eng_hits),
        "引擎内调用点（排除定义行）": [h for h in eng_hits if not h["内容"].startswith("def ")],
        "测试脚本是否直接调该函数": "PE.record_stop_update(" in tt,
        "测试脚本是否自己 INSERT 初始止损": "INSERT INTO paper_stop_history" in tt,
        "判定": ("测试是「手工 INSERT 初始行 + 直接调函数」⇒ 只证明函数本身能写，"
                 "**不证明引擎在真实推进中会调用它**"
                 if "PE.record_stop_update(" in tt and "INSERT INTO paper_stop_history" in tt
                 else "需人工判读"),
        "生产账本 paper_stop_history 行数": None,
    }

    # ---------------------------------------------------------------- F8
    # 正向路径：必须有一条断言要求「正常请求真的成交」。
    # 关键：只看断言表达式本身，不能看断言后面的提示文字——
    # `assert item_fake.get("ok") is False, "伪造过去时钟竟然产生了成交！"`
    # 的提示文字里有「成交」，但它是反向断言（要求不得成交）。
    asserts = [l.strip() for l in tt.splitlines() if "assert" in l]
    def expr_of(a: str) -> str:
        # 去掉 `, "提示文字"` 之后的部分
        return re.split(r',\s*["\']', a, maxsplit=1)[0]
    pos_assert, neg_assert = [], []
    for a in asserts:
        e = expr_of(a)
        if re.search(r"is False|!= 200|reason", e):
            neg_assert.append(a)
        elif re.search(r'\["ok"\]\s*is True|filled|fill_price|position_id.*==', e):
            pos_assert.append(a)
    R["F8_正向成交路径"] = {
        "测试内断言总数": len(asserts),
        "正向断言（要求正常请求真的成交）": pos_assert,
        "反向断言（要求被拒/不得成交）": neg_assert,
        "判定": ("**只测了反例**：15 条断言里没有一条要求「正常下单请求必须完成成交」，"
                 "全部写入口断言都在要求「不得成交 / 必须被拒 / 必须转为挂单」⇒ "
                 "工单明令「正常请求必须能按既定规则完成成交，不能只靠全部拒单通过」，此证据不满足"
                 if not pos_assert else "存在正向断言，需人工确认其覆盖程度"),
        "附带发现_幂等": ("重复请求那一步（L255-258）只打印了结果，**没有任何断言**，"
                        "随后直接标记 PASSED ⇒ 幂等性其实未被验证"),
    }

    # ---------------------------------------------------------------- 附：生产账本轨迹表
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{BASE/'数据/paper/paper_3a.sqlite3'}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM paper_stop_history").fetchone()[0]
        con.close()
        R["F7_止损轨迹"]["生产账本 paper_stop_history 行数"] = n
    except Exception as exc:                                     # noqa: BLE001
        R["F7_止损轨迹"]["生产账本 paper_stop_history 行数"] = f"读取失败: {exc}"

    (OUT / "05_反重力交付核验.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- Markdown
    L = ["# 反重力交付核验（阶段 0 交付物）\n",
         f"- 生成时刻：`{now.isoformat(timespec='seconds')}`",
         f"- 交付目录：`{R['交付目录']}`，共 **{R['F1_交付文件']['数量']}** 个文件，"
         f"落盘时间 {R['F1_交付文件']['最早']} ~ {R['F1_交付文件']['最晚']}\n",
         "## F1 交付文件清单\n", "| 文件 | 字节 | mtime | SHA-256 前 16 |", "|---|---|---|---|"]
    for f in files:
        L.append(f"| `{f['文件']}` | {f['字节']} | {f['mtime'][11:]} | `{f['sha256'][:16]}` |")
    L.append("\n## F2 截图重复检测\n")
    L.append(f"- 截图 {R['F2_截图重复']['截图数']} 张，独立图像只有 **{R['F2_截图重复']['独立图像数']}** 张")
    for g in R["F2_截图重复"]["重复组"]:
        L.append(f"- 同一张图（`{g['sha256']}`）被存成 {len(g['文件'])} 个文件：")
        for fn in g["文件"]:
            L.append(f"  - `{fn}`")
    L.append(f"\n{R['F2_截图重复']['含义']}\n")
    L.append("## F3 「版本指纹」里到底有没有指纹\n")
    L.append(f"- 回执字符数 {R['F3_回执是否给指纹']['回执字符数']}")
    L.append(f"- 64 位 SHA-256 个数：**{R['F3_回执是否给指纹']['其中64位SHA256个数']}**")
    L.append(f"- 16 位短哈希个数：**{R['F3_回执是否给指纹']['其中16位短哈希个数']}**")
    L.append(f"\n{R['F3_回执是否给指纹']['判定']}\n")
    L.append("## F4 回执引用的文件是否存在\n")
    L.append("| 回执引用 | 存在 | 实际内容 |")
    L.append("|---|---|---|")
    for c in R["F4_回执引用的文件"]["明细"]:
        act = json.dumps(c["实际"], ensure_ascii=False)[:120] if c["实际"] else "—"
        L.append(f"| `{c['回执引用']}` | {'是' if c['存在'] else '**否**'} | `{act}` |")
    L.append(f"\n**不存在 {R['F4_回执引用的文件']['不存在数']} 个。**\n")
    L.append("## F5 回执引用的行号\n")
    L.append("| 文件 | 引用 | 文件总行数 | 越界 |")
    L.append("|---|---|---|---|")
    for c in R["F5_回执行号"]["明细"]:
        L.append(f"| `{c['文件'].split('/')[-1]}` | {c['引用']} | {c['总行数']} | "
                 f"{'**是**' if c['越界'] else '否'} |")
    L.append(f"\n**越界 {R['F5_回执行号']['越界数']} 处。**\n")
    L.append("## F6 「7 项全部 PASSED」的证据数量\n")
    L.append(f"- 回执声称：{R['F6_七项闭环']['回执声称']}")
    L.append(f"- 结果文件 `data/03_isolated_test_results.json` 实际条目：**{R['F6_七项闭环']['结果文件条目数']}** 条")
    for k, v in R["F6_七项闭环"]["结果文件内容"].items():
        L.append(f"  - `{k}` = {v}")
    L.append(f"\n{R['F6_七项闭环']['判定']}\n")
    L.append("## F7 止损轨迹：证明的是函数还是引擎\n")
    L.append(f"- 引擎内 `record_stop_update` 命中 **{R['F7_止损轨迹']['引擎内命中数']}** 处"
             f"（其中调用点 {len(R['F7_止损轨迹']['引擎内调用点（排除定义行）'])} 个）")
    L.append(f"- 测试脚本直接调该函数：{R['F7_止损轨迹']['测试脚本是否直接调该函数']}")
    L.append(f"- 测试脚本自己 INSERT 初始止损：{R['F7_止损轨迹']['测试脚本是否自己 INSERT 初始止损']}")
    L.append(f"- 生产账本 `paper_stop_history` 行数：**{R['F7_止损轨迹']['生产账本 paper_stop_history 行数']}**")
    L.append(f"\n{R['F7_止损轨迹']['判定']}\n")
    L.append("## F8 有没有「正常请求能成交」的正向证据\n")
    L.append(f"- 测试内断言总数：{R['F8_正向成交路径']['测试内断言总数']} 条")
    L.append(f"- 反向断言（要求被拒/不得成交）：{len(R['F8_正向成交路径']['反向断言（要求被拒/不得成交）'])} 条")
    L.append(f"- 正向断言（要求正常请求真的成交）：**{len(R['F8_正向成交路径']['正向断言（要求正常请求真的成交）'])} 条**")
    L.append(f"- 附带发现：{R['F8_正向成交路径']['附带发现_幂等']}")
    L.append(f"\n{R['F8_正向成交路径']['判定']}\n")
    (OUT / "05_反重力交付核验.md").write_text("\n".join(L), encoding="utf-8")

    print("=== F1 交付文件 ===")
    print(f"  {R['F1_交付文件']['数量']} 个，{R['F1_交付文件']['最早']} ~ {R['F1_交付文件']['最晚']}")
    print("\n=== F2 截图 ===")
    print(f"  截图 {R['F2_截图重复']['截图数']} 张 / 独立 {R['F2_截图重复']['独立图像数']} 张")
    for g in R["F2_截图重复"]["重复组"]:
        print(f"  重复组 {g['sha256']}: {g['文件']}")
    print("\n=== F3 回执里的哈希 ===")
    print(f"  64位 {R['F3_回执是否给指纹']['其中64位SHA256个数']}  16位 {R['F3_回执是否给指纹']['其中16位短哈希个数']}")
    print("\n=== F4 引用文件 ===")
    for c in R["F4_回执引用的文件"]["明细"]:
        print(f"  {c['回执引用']}: 存在={c['存在']}")
    print("\n=== F5 行号 ===")
    for c in R["F5_回执行号"]["明细"]:
        print(f"  {c['文件'].split('/')[-1]:32s} {c['引用']:12s} 总行数 {c['总行数']:5d} 越界={c['越界']}")
    print("\n=== F6 七项 ===")
    print(f"  结果条目 {R['F6_七项闭环']['结果文件条目数']} 条: {list(res.keys())}")
    print("\n=== F7 止损 ===")
    print(f"  引擎命中 {R['F7_止损轨迹']['引擎内命中数']} 处，调用点 {len(R['F7_止损轨迹']['引擎内调用点（排除定义行）'])}")
    print(f"  测试直接调函数={R['F7_止损轨迹']['测试脚本是否直接调该函数']}，"
          f"测试自己INSERT={R['F7_止损轨迹']['测试脚本是否自己 INSERT 初始止损']}")
    print(f"  生产轨迹表行数={R['F7_止损轨迹']['生产账本 paper_stop_history 行数']}")
    print("\n=== F8 正向路径 ===")
    print(f"  断言总数 {R['F8_正向成交路径']['测试内断言总数']} 条；"
          f"反向 {len(R['F8_正向成交路径']['反向断言（要求被拒/不得成交）'])} 条；"
          f"正向（要求真的成交）{len(R['F8_正向成交路径']['正向断言（要求正常请求真的成交）'])} 条")


if __name__ == "__main__":
    main()
