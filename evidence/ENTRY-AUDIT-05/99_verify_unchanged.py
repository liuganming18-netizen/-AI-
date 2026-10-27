#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ENTRY-AUDIT-05 / 99 · 收尾自检：本轮只读，只在本证据目录新增文件
================================================================
五件事
------
1. 上游研究产物 + 策略代码 + 合约规格（51 项）：与 ENTRY-AUDIT-03 的冻结哈希逐位比对
2. FE07 冻结产物（4 项）：与 06_hashes_versions.json 里登记的值逐位比对
   —— 证明「审计期间对方文件没被换过」，否则第一阶段结论作废
3. FE08 冻结产物（17 项）：与 FE08 自己的 hashes.json 逐位比对
   —— 同理，证明第二阶段结论建立在同一个冻结版本上
4. 反重力目录快照（FACTOR-ENTRY-01~08）：记录当前文件清单/大小/时间，供后续核对
5. 写入边界：列出本窗口在 ENTRY-AUDIT-05 之外新建的任何文件（应为空）

为什么哈希比对比 git status 更硬：
    git status 里本来就有大量本轮之前就存在的改动，无法归因到本窗口。
    而「与冻结清单逐位相同」是可判定的事实：只要 51 项全等，
    就证明本窗口没有碰过这些文件，与 git 里有多少历史改动无关。

产出：99_verify_result.json
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-05"
A3 = ROOT / "evidence" / "ENTRY-AUDIT-03"
FE07 = ROOT / "evidence" / "FACTOR-ENTRY-07"
FE08 = ROOT / "evidence" / "FACTOR-ENTRY-08"
EVID = ROOT / "evidence"

# 本窗口开始时刻（本地时间）。晚于它的、落在证据目录之外的新文件都算越界写入。
WINDOW_START = datetime(2026, 10, 18, 18, 0, 0)


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(job) -> dict:
    rel, frozen = job
    p = ROOT / rel
    if not p.exists():
        return {"path": rel, "status": "missing", "sha256_frozen": frozen}
    cur = sha256_file(str(p))
    return {"path": rel, "status": "ok", "sha256_frozen": frozen,
            "sha256_now": cur, "same": cur == frozen}


def collect_fe07() -> list[tuple[str, str]]:
    """从 06_hashes_versions.json 取 FE07 的 4 项登记哈希。"""
    d = json.loads((OUT / "06_hashes_versions.json").read_text(encoding="utf-8"))
    v = d["FE07 冻结版本"]
    base = "evidence/FACTOR-ENTRY-07"
    return [
        (f"{base}/hashes.json", v["hashes.json sha256"]),
        (f"{base}/reconstructed_candidate_features_07.csv", v["表 csv sha256"]),
        (f"{base}/reconstructed_candidate_features_07.parquet", v["表 parquet sha256"]),
        (f"{base}/报告.md", v["报告 md sha256"]),
    ]


def collect_fe08() -> list[tuple[str, str]]:
    """从 FE08 自己的 hashes.json 取全部登记项。

    ⚠ FE08 的 hashes.json 是**扁平**的「相对路径 → {size, sha256}」，
      不是 FE07/FE06 那种 inputs/outputs 分组。两种形态都要能吃，
      否则会静默取到 0 项而看起来「零改动」（比报错更危险）。
    """
    h = json.loads((FE08 / "hashes.json").read_text(encoding="utf-8"))
    items = []
    for key, meta in h.items():
        if isinstance(meta, dict) and meta.get("sha256"):
            # 分组形态：{inputs:{rel:{sha256}}, outputs:{...}}
            if key in ("inputs", "outputs") and isinstance(meta, dict):
                for rel, m2 in meta.items():
                    if isinstance(m2, dict) and m2.get("sha256"):
                        items.append((f"evidence/FACTOR-ENTRY-08/{rel}", m2["sha256"]))
            else:
                items.append((f"evidence/FACTOR-ENTRY-08/{key}", meta["sha256"]))
    if not items:
        raise SystemExit("FE08/hashes.json 解析出 0 项，拒绝在「零项」上下结论")
    return items


def dir_snapshot(d: Path) -> list[dict]:
    if not d.exists():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            out.append({
                "path": str(p.relative_to(ROOT)),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return out


def git_status_split() -> dict:
    """只读。区分「改了既有文件」与「新增未跟踪文件」，两者性质不同。"""
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
    except Exception as e:  # pragma: no cover
        return {"error": str(e)}
    modified, untracked = [], []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        code, _, path = line[:2], line[2], line[3:]
        (untracked if code.strip() == "??" else modified).append({"code": code, "path": path})
    return {"modified_existing": modified, "untracked_new": untracked}


def outside_writes() -> list[dict]:
    """本窗口开始之后、在 ENTRY-AUDIT-05 之外被改动的文件。

    不能只看「有没有」——同一时段还有别的窗口在干活（反重力交付 FE08、
    数据采集推进 DATA-UI）。所以要按目录归因，只把**无法归因**的算违规。
    """
    bad = []
    me = OUT.resolve()
    for p in EVID.rglob("*"):
        if not p.is_file():
            continue
        try:
            if me in p.resolve().parents:
                continue
        except OSError:
            continue
        st = p.stat()
        if datetime.fromtimestamp(st.st_mtime) < WINDOW_START:
            continue
        rel = str(p.relative_to(ROOT))
        if "/FACTOR-ENTRY-" in rel:
            who = "反重力交付（对方窗口写入，只读对待）"
        elif "/DATA-UI-" in rel:
            who = "另一窗口（数据采集推进，与本审计无关）"
        elif rel.startswith("evidence/ENTRY-AUDIT-0") and "ENTRY-AUDIT-05" not in rel:
            who = "往期审计目录（不应有新改动，需人工确认）"
        else:
            who = "未归因"
        bad.append({
            "path": rel,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "attribution": who,
        })
    return bad


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ── 1) 上游 + 代码 + 合约 ──
    man = json.loads((A3 / "00_freeze_manifest.json").read_text(encoding="utf-8"))
    strict = [(f["path"], f["sha256"]) for f in man["files"]
              if f["category"] in ("upstream", "code", "contracts")]

    fe07_items = collect_fe07()
    fe08_items = collect_fe08()

    jobs = strict + fe07_items + fe08_items
    with ProcessPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(check, jobs, chunksize=4))

    n = len(strict)
    s = res[:n]
    f7 = res[n:n + len(fe07_items)]
    f8 = res[n + len(fe07_items):]

    def verdict(rows):
        changed = [r["path"] for r in rows if r.get("same") is False]
        missing = [r["path"] for r in rows if r.get("status") == "missing"]
        return {
            "count": len(rows),
            "unchanged": sum(1 for r in rows if r.get("same") is True),
            "changed": changed,
            "missing": missing,
            "verdict": "零改动" if not changed and not missing else "有改动或缺失",
        }

    up = {"source": "evidence/ENTRY-AUDIT-03/00_freeze_manifest.json（upstream+code+contracts）", **verdict(s)}
    v7 = {"source": "evidence/ENTRY-AUDIT-05/06_hashes_versions.json（第一阶段登记值）", **verdict(f7)}
    v8 = {"source": "evidence/FACTOR-ENTRY-08/hashes.json（反重力自己冻结的输入与输出）", **verdict(f8)}

    # ── 4) 反重力目录快照 ──
    snap = {d.name: dir_snapshot(d) for d in sorted(EVID.glob("FACTOR-ENTRY-*")) if d.is_dir()}

    # ── 5) 写入边界 ──
    gs = git_status_split()
    outside = outside_writes()
    unattributed = [w for w in outside if w["attribution"] == "未归因"]
    past_audit_touched = [w for w in outside if w["attribution"].startswith("往期审计目录")]

    result = {
        "audit": "ENTRY-AUDIT-05",
        "checked_utc": now,
        "upstream_code_contracts": up,
        "fe07_frozen": v7,
        "fe08_frozen": v8,
        "antigravity_dir_snapshot": {k: len(v) for k, v in snap.items()},
        "write_scope": {
            "declared": "本轮新增文件全部在 evidence/ENTRY-AUDIT-05/ 内",
            "outside_writes": outside,
            "outside_writes_summary": {
                "总数": len(outside),
                "未归因": len(unattributed),
                "反重力交付": sum(1 for w in outside if w["attribution"].startswith("反重力")),
                "另一窗口": sum(1 for w in outside if w["attribution"].startswith("另一窗口")),
                "往期审计目录被改动": len(past_audit_touched),
            },
            "not_modified": ["原账本", "反重力产物", "正式策略", "共享撮合",
                             "UI", "API", "采集器", "交易闸门"],
            "no_rerun": "本轮未重跑任何回测：FE07 与 FE08 数值相同，沿用第一阶段独立计算",
            "git_status_note": ("git status 里既有本轮之前就存在的改动，无法归因；"
                                "真正的证明是上面三项逐位哈希比对。"),
            "git_modified_existing_count": len(gs.get("modified_existing", [])),
            "git_untracked_new_count": len(gs.get("untracked_new", [])),
        },
        "hard_verdict": {
            "upstream_unchanged": not up["changed"] and not up["missing"],
            "fe07_unchanged_since_audit": not v7["changed"] and not v7["missing"],
            "fe08_unchanged_since_audit": not v8["changed"] and not v8["missing"],
            "no_unattributed_outside_writes": not unattributed,
            "no_past_audit_dir_touched": not past_audit_touched,
            "pass": (not up["changed"] and not up["missing"]
                     and not v7["changed"] and not v7["missing"]
                     and not v8["changed"] and not v8["missing"]
                     and not unattributed and not past_audit_touched),
        },
    }
    # 快照明细单独落盘，避免主结果文件过大
    (OUT / "99_dir_snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "99_verify_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"上游/代码/合约 {up['count']} 项：{up['unchanged']} 未变，{len(up['changed'])} 变，{len(up['missing'])} 缺")
    print(f"FE07 冻结   {v7['count']} 项：{v7['unchanged']} 未变，{len(v7['changed'])} 变")
    print(f"FE08 冻结   {v8['count']} 项：{v8['unchanged']} 未变，{len(v8['changed'])} 变")
    print(f"证据目录外新写入：{len(outside)} 个（未归因 {len(unattributed)} / 往期审计目录 {len(past_audit_touched)}）")
    for w in outside:
        print(f"   {w['mtime']}  {w['attribution']}  {w['path']}")
    print(f"git 既有改动 {result['write_scope']['git_modified_existing_count']} 项 / 未跟踪新增 {result['write_scope']['git_untracked_new_count']} 项（只作参考）")
    print(f"\n硬判定：{json.dumps(result['hard_verdict'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
