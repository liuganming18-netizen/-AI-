#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-UI02-REAUDIT-01｜阶段一 · 截图与操作覆盖独立复算

对应工单：
  第 2 条「独立复算新版16张截图及操作矩阵。哈希不同不能代替操作覆盖通过。」
  第 3 条「检查测试脚本是否实际点击原定页签、执行原定操作；逐项核查截图、DOM、
          同一次 API 响应及源数据。」

本脚本只做三件可机械复算的事：
  A. 对 evidence/SYSTEM-UI-02/ 下全部 PNG 重算 SHA-256，按哈希分组，找出重复。
  B. 对「应当不同」与「应当相同」的图组做像素差异量化，判断内容是否真的变了，
     而不是只看哈希是否不同（哈希不同 ≠ 操作覆盖通过，哈希相同 = 操作一定没生效）。
  C. 记录每张图的 mtime，用写入时序反推脚本里「注入 → 截图」之间有没有留出重渲染时间。

纪律：
  * 只读 evidence/SYSTEM-UI-02/，不修改对方任何文件。
  * 不做全盘扫描；只遍历该目录下的 *.png。
  * 像素差异是**必要非充分**判据：差异大 ⇒ 画面确实变了；差异≈0 ⇒ 一定没变。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime

from PIL import Image, ImageChops

TARGET = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-UI-02")
OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

# 需要量化的图组：
#   ("组名", [文件...], "期望", "说明")
GROUPS = [
    ("数据中心三页签", ["view_08_datacenter_overview.png", "view_09_datacenter_matrix.png",
                        "view_10_datacenter_gaps.png"], "应显著不同",
     "报告称真实点击了 3 个不同页签"),
    ("策略管理四页签", ["view_11_strategy_list.png", "view_12_strategy_rules.png",
                        "view_13_strategy_deps.png", "view_14_strategy_versions.png"], "应显著不同",
     "报告称真实点击了 4 个不同页签"),
    ("交易工作台同面板三视图", ["view_05_position_panel_and_daemon.png", "view_06_stop_trail.png",
                                "view_07_gate_and_pending.png"], "应显著不同",
     "三者都用 .workbench-position-panel 定位，只能靠面板内变化区分"),
    ("信号三态", ["state_signal_normal.png", "state_signal_empty.png",
                  "state_signal_failed.png"], "应三态互不相同",
     "正常/空态/500 失败三种互斥呈现"),
    ("持仓三态", ["state_position_normal.png", "state_position_empty.png",
                  "state_position_failed.png"], "应三态互不相同",
     "正常/空态/500 失败三种互斥呈现"),
    ("止损轨迹三态", ["state_stop_trail_normal.png", "state_stop_trail_failed.png",
                      "view_06_stop_trail.png"], "应三态互不相同",
     "报告 CSV 06 把 view_06 当作止损空态，另两张当作有数据与失败态"),
]

# 交叉重叠：报告声称不同用途但可能同一张图
CROSS = [
    ("state_signal_normal.png", "view_02_signal_list_panel.png", "信号正常态应等于 View02"),
    ("state_position_normal.png", "view_05_position_panel_and_daemon.png", "持仓正常态应等于 View05"),
    ("state_stop_trail_normal.png", "view_06_stop_trail.png", "止损正常态 vs View06（报告称 View06 为空态）"),
]

# 截图顶部的比对高度（页签内容差异集中在这里；整图过高的图裁掉下半截避免误判）
CROP_H = 900
COMPARE_W = 600


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(p: pathlib.Path) -> Image.Image:
    im = Image.open(p).convert("L")
    w, h = im.size
    im = im.crop((0, 0, w, min(CROP_H, h)))
    ratio = COMPARE_W / float(w)
    im = im.resize((COMPARE_W, max(1, int(im.size[1] * ratio))), Image.BILINEAR)
    return im


def pixel_diff(a: pathlib.Path, b: pathlib.Path) -> dict:
    """返回差异像素占比（阈值 16/255）。尺寸不同先各自归一化。"""
    ia, ib = fingerprint(a), fingerprint(b)
    # 高度可能因原图比例不同而不同，裁到公共高度
    hh = min(ia.size[1], ib.size[1])
    ia = ia.crop((0, 0, COMPARE_W, hh))
    ib = ib.crop((0, 0, COMPARE_W, hh))
    diff = ImageChops.difference(ia, ib)
    hist = diff.histogram()
    total = COMPARE_W * hh
    changed = sum(hist[16:])          # 差值 >= 16 视为真实像素变化
    return {
        "差异像素占比": round(changed / float(total), 6),
        "比对区域": f"{COMPARE_W}x{hh}",
        "原图尺寸": [list(Image.open(a).size), list(Image.open(b).size)],
    }


def main() -> int:
    if not TARGET.is_dir():
        print(f"[FATAL] 目标目录不存在: {TARGET}")
        return 2

    pngs = sorted(p for p in TARGET.glob("*.png"))
    report: dict = {
        "生成时刻": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "用途": "阶段一｜截图哈希复算 + 像素差异量化 + 写入时序",
        "审核对象": str(TARGET),
        "图片总数": len(pngs),
        "逐图": {},
        "哈希分组": {},
        "重复组": [],
        "组内差异": {},
        "交叉重叠": [],
        "时序": {},
    }

    # ---- A. 哈希 ----
    groups: dict = {}
    for p in pngs:
        h = sha256(p)
        st = os.stat(p)
        report["逐图"][p.name] = {
            "sha256": h,
            "bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "尺寸": list(Image.open(p).size),
        }
        groups.setdefault(h, []).append(p.name)

    report["哈希分组"] = {h: names for h, names in groups.items()}
    for h, names in groups.items():
        if len(names) > 1:
            report["重复组"].append({"sha256": h, "文件": names, "数量": len(names)})

    # ---- B. 组内像素差异 ----
    for name, files, expect, note in GROUPS:
        exist = [TARGET / f for f in files if (TARGET / f).exists()]
        pairs = []
        for i in range(len(exist) - 1):
            for j in range(i + 1, len(exist)):
                d = pixel_diff(exist[i], exist[j])
                d["对"] = [exist[i].name, exist[j].name]
                d["同哈希"] = sha256(exist[i]) == sha256(exist[j])
                pairs.append(d)
        report["组内差异"][name] = {
            "期望": expect, "说明": note, "文件数": len(exist), "两两差异": pairs,
            "最小差异占比": min([p["差异像素占比"] for p in pairs]) if pairs else None,
        }

    # ---- B2. 交叉重叠 ----
    for a, b, note in CROSS:
        pa, pb = TARGET / a, TARGET / b
        if pa.exists() and pb.exists():
            report["交叉重叠"].append({
                "说明": note, "文件": [a, b],
                "同哈希": sha256(pa) == sha256(pb),
                "差异像素占比": pixel_diff(pa, pb)["差异像素占比"],
            })

    # ---- C. 时序 ----
    seq = sorted(
        ((v["mtime"], k) for k, v in report["逐图"].items())
    )
    report["时序"] = {
        "最早": seq[0] if seq else None,
        "最晚": seq[-1] if seq else None,
        "按秒聚簇": {},
    }
    for mt, name in seq:
        report["时序"]["按秒聚簇"].setdefault(mt, []).append(name)

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "00_截图复算.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台摘要 ----
    print("=" * 78)
    print("A. 哈希复算")
    print("=" * 78)
    print(f"图片总数 {report['图片总数']} ／ 唯一哈希 {len(groups)} ／ 重复组 {len(report['重复组'])}")
    for g in report["重复组"]:
        print(f"  x{g['数量']}  {g['sha256'][:16]}  {' | '.join(g['文件'])}")

    print()
    print("=" * 78)
    print("B. 组内像素差异（差异≈0 说明画面根本没变）")
    print("=" * 78)
    for name, r in report["组内差异"].items():
        print(f"[{name}] {r['文件数']} 图 / {len(r['两两差异'])} 对 / 最小差异 {r['最小差异占比']}")
        for p in r["两两差异"]:
            flag = "同哈希" if p["同哈希"] else "  "
            print(f"   {p['差异像素占比']:.6f}  {flag}  {p['对'][0]}  vs  {p['对'][1]}")

    print()
    print("=" * 78)
    print("C. 交叉重叠")
    print("=" * 78)
    for c in report["交叉重叠"]:
        print(f"   同哈希={c['同哈希']}  差异={c['差异像素占比']:.6f}  {c['说明']}")

    print()
    print("=" * 78)
    print("D. 写入时序（同秒成簇 ⇒ 中间没有等待重渲染）")
    print("=" * 78)
    for mt, names in report["时序"]["按秒聚簇"].items():
        print(f"   {mt}  ({len(names)} 张)  {' | '.join(names)}")

    print(f"\n已落盘: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
