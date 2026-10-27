# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜00 生成「改前基线」与「本单差异」。

为什么不直接用 `git show HEAD:`：仓库 HEAD 落后工作树很远（前几单的改动都没提交），
`git show HEAD:框架/backend/live_store.py` 拿到的是一份**前几单之前**的旧文件，
拿它当基线会把别人几十处改动算进本单。这里改用「反向还原本单的 3 处编辑」，
并逐条断言还原标记确实存在（标记不在就报错，不静默通过）。

产物：
  baseline/live_store.py.before_04b   —— 本单改前副本
  baseline/live_store_04b.diff        —— 本单差异（只有 3 处）
  baseline/git_head_note.json         —— 说明为什么不用 git HEAD
"""
from __future__ import annotations

import difflib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

TARGET = C.BACKEND / "live_store.py"
BASE = C.EVID / "baseline"

# ── 本单的 3 处编辑（(改后片段, 改前片段)）─────────────────────────────────
EDITS = [
    # ① 新增 RESEARCH_ARMS 常量块
    ('''ARMS = ("3a", "3b", "3c", "3d")
#: **研究臂**（ACCUM-EARLY-04-B）：只在显式隔离的账本目录里存在。
#: 为什么不直接扩 `ARMS`：`ARMS` 被生产汇总、迁移清单、页面、`paper_conns()` 共用，
#: 扩它会把这些研究账户算进生产视图。这里只放开「路径解析」，并且**强制**要求
#: `PAPER_LEDGER_DIR` 显式给出（默认目录 = 生产 ⇒ 研究臂一律拒绝建/读）。
RESEARCH_ARMS = ("4a", "4b", "4c")
''',
     '''ARMS = ("3a", "3b", "3c", "3d")
'''),
    # ② ledger_path：研究臂分支 + 说明
    ('''    """某臂账本**权威**路径（不做任何相对推导）。

    ACCUM-EARLY-04-B 追加：研究臂（`RESEARCH_ARMS`）只允许在**显式**的
    `PAPER_LEDGER_DIR` 下解析；未显式指定 ⇒ 直接拒绝，绝不让研究账户落进生产目录。
    生产四臂的行为逐字未变；未知臂仍然报错。
    """
    if arm in RESEARCH_ARMS:
        if not os.environ.get("PAPER_LEDGER_DIR"):
            raise ValueError(
                "研究臂 %r 必须显式设置 PAPER_LEDGER_DIR（拒绝落在生产账本目录 %s）"
                % (arm, get_ledger_dir()))
        return get_ledger_dir() / f"paper_{arm}.sqlite3"
    if arm not in ARMS:''',
     '''    """某臂账本**权威**路径（不做任何相对推导）。"""
    if arm not in ARMS:'''),
    # ③ paper_conn：缺库提示里的 ARMS.index 保护
    ('            _mig = (f"M00{ARMS.index(arm) + 2}" if arm in ARMS else "研究臂无迁移清单")',
     '            _mig = f"M00{ARMS.index(arm) + 2}"'),
]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    cur = src
    applied = []
    for i, (after, before) in enumerate(EDITS, 1):
        n = cur.count(after)
        if n != 1:
            raise SystemExit(f"拒绝：第 {i} 处改后片段在文件里出现 {n} 次（应为 1 次），"
                             f"说明文件已被别处改动，反向还原不可靠")
        cur = cur.replace(after, before)
        applied.append({"edit": i, "reverted": True, "bytes_removed": len(after) - len(before)})
    if "RESEARCH_ARMS" in cur:
        raise SystemExit("拒绝：还原后仍出现 RESEARCH_ARMS，说明还有第 4 处未登记")

    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / "live_store.py.before_04b").write_text(cur, encoding="utf-8")
    diff = list(difflib.unified_diff(
        cur.splitlines(True), src.splitlines(True),
        fromfile="框架/backend/live_store.py（本单改前）",
        tofile="框架/backend/live_store.py（本单改后）", n=4))
    (BASE / "live_store_04b.diff").write_text("".join(diff), encoding="utf-8")

    head = subprocess.run(["git", "show", "HEAD:框架/backend/live_store.py"],
                          cwd=str(C.PROJECT), capture_output=True, text=True)
    note = {
        "why_not_git_head": ("仓库 HEAD 落后工作树很远（前几单改动未提交）；"
                             "用 `git show HEAD:` 当基线会把别人几十处改动算进本单"),
        "git_head_readable": head.returncode == 0,
        "git_head_bytes": len(head.stdout.encode("utf-8")) if head.returncode == 0 else 0,
        "current_bytes": len(src.encode("utf-8")),
        "baseline_bytes": len(cur.encode("utf-8")),
        "edits": applied,
        "diff_hunks": sum(1 for ln in diff if ln.startswith("@@")),
        "diff_added_lines": sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++")),
        "diff_removed_lines": sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---")),
    }
    (BASE / "git_head_note.json").write_text(
        __import__("json").dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    print(__import__("json").dumps({"baseline": str(BASE), **{k: note[k] for k in
                                  ("diff_hunks", "diff_added_lines", "diff_removed_lines")}},
                                 ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
