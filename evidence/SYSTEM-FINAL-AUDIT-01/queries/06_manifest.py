# -*- coding: utf-8 -*-
"""06 产物清单：evidence/SYSTEM-FINAL-AUDIT-01 下每个文件的 SHA-256。

产物：06_文件清单与哈希.csv
"""
from __future__ import annotations

import csv
import hashlib
import pathlib

EVID = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-FINAL-AUDIT-01")
NOTES = {
    "00_依赖核对与基线.md": "五份工单依赖关系 + 就绪度 + 回执核验",
    "01_A关配置.md": "A 关结论：**01:27 版不通过**；对 01:40 后版本的翻转见 07/08",
    "02_B关风险.md": "B 关结论：不通过（分叉确证，两版均未修）",
    "03_C关自动运行.md": "C 关部分通过（C0 统一入口不通过）+ D 关未执行；仅对 paper_loop c4acf2137af7 有效",
    "04_回测一致性专项.md": "风险定量不一致；取整一致；费用/时间边界未验证",
    "05_最终交付.md": "五项最终交付（开头有必读前置：结论基于 01:27 冻结 SHA）",
    "out/05_自检.json": "报告数值机械复算（全量明细，80 条）",
    "07_版本漂移与复验.md": "被审代码 01:32~01:43 被改 5 个文件 + 新增 1 套件 + 生产账本被写(F-09) + A 关复验翻转",
    "08_契约测试独立复核.md": "独立执行反重力契约套件：v1 崩(no such table)/v2 6-6 通过；A 关证据补全，不作 C 关证据",
    "out/00b_漂移复采.json": "01:40 漂移复采（4 条）与生产四臂现状",
    "out/02b_A关复验.json": "对当前版的 A 关复验（S1/S2/S3 均被拒）",
    "out/08_契约测试_运行前SHA.json": "v1 执行前采样（6 文件 SHA），01:45:00",
    "out/08_契约测试_运行输出.txt": "v1 执行输出：RC=1，第 6 项 no such table: funding_history",
    "out/08b_契约测试_复跑.json": "v2 独立复跑：RC=0，6/6 通过，含 6 文件 SHA 与完整 stdout",
}


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    rows = []
    for p in sorted(EVID.rglob("*")):
        if not p.is_file() or p.name == "06_文件清单与哈希.csv":
            continue
        rel = p.relative_to(EVID).as_posix()
        rows.append({"文件": rel, "字节": p.stat().st_size, "sha256": sha(p),
                     "说明": NOTES.get(rel, "")})
    out = EVID / "06_文件清单与哈希.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["文件", "字节", "sha256", "说明"])
        w.writeheader()
        w.writerows(rows)
    print(f"产物 {len(rows)} 个，合计 {sum(r['字节'] for r in rows)/1024:.1f} KB → {out}")


if __name__ == "__main__":
    main()
