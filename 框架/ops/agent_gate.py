#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wyckoff V3.1 多代理门禁：暂存区校验与参数保护

设计来源
--------
分支 ``implement_agent_guardrails`` 的 ``框架/ops/agent_gate.py``（239 行）。
本文件只**复用设计思路**，未合并、未切换、未拣选该分支；旧实现与本次适配的差异见下。

本次适配（AGENT-GATE-ADAPT-01）与旧实现的差异
--------------------------------------------
1. 默认不启用：``verify`` 不带 ``--strict`` 时只打印「门禁未启用」并返回 0。
   不读状态文件、不校验角色、不校验版本，绝不阻断现有 pre-commit。
2. 范围只看 HEAD → 暂存区：``git diff --cached --name-status -z``，
   不再用 ``git status --porcelain`` 把工作区改动和未跟踪文件混进提交范围。
3. 零字节分隔解析，覆盖新增 / 修改 / 删除 / 重命名两端；路径按**目录段边界**匹配，
   避免 ``evidence/`` 放行 ``evidence_evil/`` 这类前缀误匹配。
4. 参数保护对比 ``HEAD:project_state.json`` 与 ``:project_state.json``，**不读工作区**。
   「HEAD 确实没有该文件」与「Git 读取失败」严格区分，读取失败一律拒绝。
5. 授权依据取 HEAD 已提交版本；暂存区版本不得自我扩大路径、角色、基线、任务编号、保护清单。
6. 删除旧实现的 ``USER_APPROVED`` 环境变量旁路：环境变量不构成用户批准。
7. 首次引入状态文件要求外部信任锚（规范化内容哈希），待审文件不能自证已获批准。
8. 基线历史逐提交核查**覆盖合并提交**：不再用 ``--no-merges`` 跳过合并，
   也不再用裸 ``diff-tree``（它对合并提交默认不输出任何内容）。合并提交按 ``-c``
   组合差异判定，只把「合并结果与所有父提交都不同」的路径算作本次合并的改动；
   正常合并（内容与某个父提交相同）不会被误判。

退出码
------
0 = 通过（或默认模式未启用）
2 = 拦截
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = Path(__file__).resolve().parents[2]
STATE_REL = "project_state.json"

PLACEHOLDER = "REPLACE_WITH_CURRENT_HEAD_SHA"
ROLES = ("Codex", "Work", "Antigravity")
VALID_STATUSES = ("pending", "in_progress", "pending_acceptance", "completed", "blocked")
WORK_STATUSES = ("in_progress",)
AUDITOR_STATUSES = ("in_progress", "pending_acceptance")


class GateError(Exception):
    """拦截原因。顶层统一打印为 [GATE BLOCKED]。"""


def fail(message: str) -> None:
    raise GateError(message)


# ── Git 调用 ────────────────────────────────────────────────────────────────


def _run(repo: Path, args, text: bool = True):
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=false"] + list(args),
            cwd=str(repo),
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
        )
    except OSError as exc:
        raise GateError("Git 读取失败：无法在 %s 执行 git（%s）" % (repo, exc))


def git_ok(repo: Path, *args) -> bool:
    return _run(repo, args).returncode == 0


def git_text(repo: Path, *args) -> str:
    return _run(repo, args).stdout.strip()


def repo_is_git(repo: Path) -> bool:
    res = _run(repo, ["rev-parse", "--git-dir"])
    return res.returncode == 0


def current_branch(repo: Path) -> str:
    res = _run(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def head_sha(repo: Path) -> str:
    res = _run(repo, ["rev-parse", "HEAD"])
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _parse_state_blob(raw: bytes, where: str):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail("Git 读取失败：%s 不是 UTF-8 编码（%s）" % (where, exc))
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        fail("%s 不是合法 JSON：%s" % (where, exc))
    if not isinstance(obj, dict):
        fail("%s 顶层必须是 JSON 对象，实际是 %s" % (where, type(obj).__name__))
    return obj


def read_head_state(repo: Path):
    """返回 (kind, obj)：kind ∈ {"ok", "absent", "error"}；error 时 obj 为原因字符串。

    「absent」= HEAD 上确实没有 project_state.json；
    「error」= Git 本身读不了（非仓库、HEAD 坏、blob 读失败、JSON 非法）——
    二者绝不能混为一谈，否则「读取失败」会被当成「没有变化」。
    """
    if not git_ok(repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"):
        return "error", "Git 读取失败：无法解析 HEAD 提交（可能不在 Git 仓库内，或仓库尚无提交）"

    res = _run(repo, ["rev-parse", "--verify", "--quiet", "HEAD:" + STATE_REL])
    if res.returncode != 0:
        # 能解析 HEAD 却取不到该路径 ⇒ 该路径确实不存在于 HEAD
        return "absent", None
    sha = res.stdout.strip()
    if not sha:
        return "absent", None

    blob = _run(repo, ["cat-file", "blob", sha], text=False)
    if blob.returncode != 0:
        return "error", "Git 读取失败：无法读取 HEAD:%s 的 blob %s" % (STATE_REL, sha)
    try:
        obj = _parse_state_blob(blob.stdout, "HEAD:" + STATE_REL)
    except GateError as exc:
        return "error", str(exc)
    return "ok", obj


def read_index_state(repo: Path):
    """返回 (kind, obj, raw)：kind ∈ {"ok", "absent", "error"}。"""
    res = _run(repo, ["rev-parse", "--verify", "--quiet", ":" + STATE_REL])
    if res.returncode != 0:
        return "absent", None, b""
    sha = res.stdout.strip()
    if not sha:
        return "absent", None, b""

    blob = _run(repo, ["cat-file", "blob", sha], text=False)
    if blob.returncode != 0:
        return "error", "Git 读取失败：无法读取暂存区 %s 的 blob %s" % (STATE_REL, sha), b""
    try:
        obj = _parse_state_blob(blob.stdout, "暂存区 " + STATE_REL)
    except GateError as exc:
        return "error", str(exc), b""
    return "ok", obj, blob.stdout


def staged_changes(repo: Path):
    """HEAD → 暂存区 的改动。返回 (changes, error)。

    changes 为 [(status, old_path, new_path)]；新增时 old_path 为 None，
    删除时 new_path 为 None，重命名两端都给出。
    """
    has_head = git_ok(repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    args = ["diff", "--cached", "--name-status", "-z", "--find-renames"]
    if has_head:
        args.append("HEAD")
    res = _run(repo, args, text=False)
    if res.returncode != 0:
        err = _decode(res.stderr).strip()
        return None, "Git 读取失败：无法读取暂存区改动（%s）" % (err or "git diff 返回非零")

    tokens = res.stdout.split(b"\0")
    out = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token:
            i += 1
            continue
        status = _decode(token)
        code = status[:1]
        if code in ("R", "C"):
            old = _decode(tokens[i + 1]) if i + 1 < len(tokens) else ""
            new = _decode(tokens[i + 2]) if i + 2 < len(tokens) else ""
            out.append((status, old, new))
            i += 3
        else:
            path = _decode(tokens[i + 1]) if i + 1 < len(tokens) else ""
            if code == "D":
                out.append((status, path, None))
            else:
                out.append((status, None, path))
            i += 2
    return out, None


def commits_between(repo: Path, baseline: str, head: str):
    """枚举 baseline..HEAD 的**全部**提交（含合并提交），并带出每个提交的父提交。

    返回 [(commit, [parent, ...])]；父列表为空表示根提交。

    刻意不加 ``--no-merges``：合并提交本身也要被检查，否则「只在合并结果里出现」
    的改动（解决冲突时的手工修改、evil merge 夹带文件）没有任何提交为其负责。
    也不加 ``--first-parent``：侧分支上的提交同样要逐个看到，它们才是合并带进来的
    那部分内容的出处。
    """
    res = _run(repo, ["rev-list", "--parents", "%s..%s" % (baseline, head)])
    if res.returncode != 0:
        fail("Git 读取失败：无法枚举基线 %s 到 HEAD %s 之间的提交" % (baseline[:12], head[:12]))
    entries = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        entries.append((parts[0], parts[1:]))
    return entries


def commit_touched_paths(repo: Path, commit: str, parents) -> list:
    """单个提交相对其父提交改动了哪些路径。

    父提交比较规则
    --------------
    * 普通提交（1 个父，或根提交）：``diff-tree <commit>``，等价于 ``<parent>..<commit>``。
    * 合并提交（>=2 个父）：**不能**用裸 ``diff-tree``——它默认对合并提交不输出任何内容，
      会把整次合并静默跳过；也不能用 ``-m``，那是对每个父各做一次两两 diff，会把
      侧分支带进来的全部内容重复算到合并头上（正常合并被误判）。
      这里用 ``-c``（组合差异）：只列出「合并结果与**每一个**父提交都不相同」的路径。
      这正是本次合并自己带进来的内容——解决冲突时的手工修改、evil merge 夹带的文件、
      合并时顺手删掉的文件。

      与某个父提交完全相同的路径不算本次合并的贡献：那部分内容由产生它的那条分支提交
      负责，而该提交同样落在 baseline..HEAD 的枚举范围内，逐提交检查时会被抓到。
      所以「只查合并自身贡献」不会漏掉分支内容，也不会把正常合并一律当违规。
    """
    args = ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z"]
    if len(parents) >= 2:
        args.append("-c")
    args.append(commit)
    res = _run(repo, args, text=False)
    if res.returncode != 0:
        fail("Git 读取失败：无法读取提交 %s 的改动清单" % commit[:12])
    return [p for p in (_decode(x) for x in res.stdout.split(b"\0")) if p]


# ── 路径匹配 ────────────────────────────────────────────────────────────────


def _norm(path) -> str:
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def path_allowed(path: str, prefixes) -> bool:
    """按目录段边界判断，而不是裸前缀匹配。"""
    target = _norm(path)
    for prefix in prefixes:
        base = _norm(prefix)
        if not base:
            continue
        if target == base or target.startswith(base + "/"):
            return True
    return False


def prefix_within(prefix: str, prefixes) -> bool:
    return path_allowed(prefix, prefixes)


def change_paths(change):
    status, old, new = change
    return [p for p in (old, new) if p]


# ── 结构校验 ────────────────────────────────────────────────────────────────


def _require(mapping, key, where):
    if key not in mapping:
        fail("%s 缺少必要字段 '%s'（身份信息不完整，拒绝）" % (where, key))
    return mapping[key]


def validate_structure(state, where: str) -> None:
    if not isinstance(state, dict):
        fail("%s 结构异常：顶层不是对象" % where)

    project = _require(state, "project", where)
    if not isinstance(project, str) or not project.strip():
        fail("%s 结构异常：project 必须是非空字符串" % where)

    version = _require(state, "state_version", where)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        fail("%s 结构异常：state_version 必须是 >= 1 的整数" % where)

    baseline = _require(state, "git_baseline", where)
    if not isinstance(baseline, dict):
        fail("%s 结构异常：git_baseline 必须是对象" % where)
    branch = _require(baseline, "branch", where + ".git_baseline")
    if not isinstance(branch, str) or not branch.strip():
        fail("%s 结构异常：git_baseline.branch 必须是非空字符串" % where)
    sha = _require(baseline, "baseline_sha", where + ".git_baseline")
    if not isinstance(sha, str) or not sha.strip():
        fail("%s 结构异常：git_baseline.baseline_sha 必须是非空字符串" % where)

    task = _require(state, "current_task", where)
    if not isinstance(task, dict):
        fail("%s 结构异常：current_task 必须是对象" % where)
    task_id = _require(task, "task_id", where + ".current_task")
    if not isinstance(task_id, str) or not task_id.strip():
        fail("%s 结构异常：current_task.task_id 必须是非空字符串" % where)
    status = _require(task, "status", where + ".current_task")
    if status not in VALID_STATUSES:
        fail("%s 结构异常：current_task.status=%r 不是合法状态 %s" % (where, status, list(VALID_STATUSES)))
    assigned = _require(task, "assigned_to", where + ".current_task")
    if assigned not in ROLES:
        fail("%s 结构异常：current_task.assigned_to=%r 不是合法角色 %s" % (where, assigned, list(ROLES)))

    allowed = _require(task, "allowed_paths", where + ".current_task")
    if not isinstance(allowed, list) or not allowed:
        fail("%s 结构异常：current_task.allowed_paths 必须是非空数组" % where)
    for item in allowed:
        if not isinstance(item, str) or not item.strip():
            fail("%s 结构异常：allowed_paths 含非字符串或空项 %r" % (where, item))
        norm = _norm(item)
        if norm.startswith("/") or norm.startswith("../") or "/../" in norm:
            fail("%s 结构异常：allowed_paths 含越界或绝对路径 %r" % (where, item))

    protected = task.get("protected_state_files", [])
    if protected is not None:
        if not isinstance(protected, list) or any(not isinstance(x, str) or not x.strip() for x in protected):
            fail("%s 结构异常：protected_state_files 必须是非空字符串数组" % where)

    params = _require(state, "parameters", where)
    if not isinstance(params, dict):
        fail("%s 结构异常：parameters 必须是对象" % where)
    for name, entry in params.items():
        if not isinstance(entry, dict):
            fail("%s 结构异常：参数 '%s' 的条目不是对象" % (where, name))
        if "approved" not in entry:
            fail("%s 结构异常：参数 '%s' 缺少 approved 字段" % (where, name))
        if not isinstance(entry["approved"], bool):
            fail("%s 结构异常：参数 '%s' 的 approved 必须是布尔值" % (where, name))
        if "value" not in entry:
            fail("%s 结构异常：参数 '%s' 缺少 value 字段" % (where, name))
        if entry["approved"]:
            source = entry.get("approval_source")
            if not isinstance(source, str) or not source.strip():
                fail(
                    "%s 结构异常：参数 '%s' 标为已批准，但缺少 approval_source。"
                    "批准必须有可追溯的用户决策出处。" % (where, name)
                )
            if not _norm(source).startswith("docs/用户约定/"):
                fail(
                    "%s 结构异常：参数 '%s' 的 approval_source=%r 必须指向统一用户决策记录"
                    "（docs/用户约定/…）；代码位置不能作为批准依据。" % (where, name, source)
                )


def canonical_digest(raw: bytes) -> str:
    """状态文件规范化哈希：键排序、紧凑分隔、保留非 ASCII。"""
    obj = json.loads(raw.decode("utf-8"))
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ── 各项检查 ────────────────────────────────────────────────────────────────


def check_branch(repo: Path, state, where: str) -> str:
    branch = current_branch(repo)
    if not branch or branch == "HEAD":
        fail("当前处于游离 HEAD 或无提交状态，无法校验分支身份")
    declared = state["git_baseline"]["branch"]
    if declared != branch:
        fail("分支不匹配：%s 声明 branch=%r，当前实际为 %r" % (where, declared, branch))
    return branch


def check_baseline(repo: Path, state, where: str) -> str:
    sha = head_sha(repo)
    if not sha:
        fail("Git 读取失败：无法读取当前 HEAD")
    baseline = state["git_baseline"]["baseline_sha"].strip()
    if baseline == PLACEHOLDER:
        fail("基线 SHA 尚未初始化（仍是占位符 %s），严格模式拒绝" % PLACEHOLDER)
    if not re.fullmatch(r"[0-9a-f]{40}", baseline):
        fail("基线 SHA 格式非法：%r（应为 40 位十六进制）" % baseline)
    if baseline == sha:
        return baseline

    if not git_ok(repo, "cat-file", "-e", baseline + "^{commit}"):
        fail("基线 SHA %s 在仓库中不存在（过期或伪造的基线）" % baseline[:12])
    if not git_ok(repo, "merge-base", "--is-ancestor", baseline, sha):
        fail("基线 %s 不是当前 HEAD %s 的祖先，历史已分叉" % (baseline[:12], sha[:12]))

    # 必须逐提交检查，不能只比 baseline 与 HEAD 的端点差异：
    # 中间若有提交改了普通文件、后续又改回原样，端点 diff 会为空，等于放行。
    # 合并提交同样在枚举范围内（不跳过），其改动按「与所有父提交都不同」判定，
    # 见 commit_touched_paths 的父提交比较规则。
    entries = commits_between(repo, baseline, sha)

    protected = set(state["current_task"].get("protected_state_files") or [])
    offenders = []
    for commit, parents in entries:
        files = commit_touched_paths(repo, commit, parents)
        extra = [p for p in files if p not in protected]
        if extra:
            label = commit[:12]
            if len(parents) >= 2:
                label += "(合并 %d 父)" % len(parents)
            offenders.append((label, extra[:5]))

    if offenders:
        fail(
            "基线已过期：基线 %s 之后存在非授权提交（只允许改受保护状态文件）。\n"
            "      逐提交发现：%s\n"
            "      注意这是逐提交核查，中间改了又改回原样同样会被拦；"
            "合并提交也按「合并结果与所有父提交都不同」的组合差异核查，"
            "解决冲突时夹带的改动不会被放过。"
            "禁止旧窗口直接覆盖施工，请先同步最新状态。"
            % (baseline[:12], offenders[:3])
        )
    return baseline


def check_first_intro_baseline(repo: Path, state) -> str:
    """首次引入：基线必须就是当前 HEAD，不允许沿用「基线落后但只改状态文件」的宽松路径。"""
    sha = head_sha(repo)
    if not sha:
        fail("Git 读取失败：无法读取当前 HEAD")
    baseline = state["git_baseline"]["baseline_sha"].strip()
    if baseline == PLACEHOLDER:
        fail("基线 SHA 尚未初始化（仍是占位符 %s），严格模式拒绝" % PLACEHOLDER)
    if not re.fullmatch(r"[0-9a-f]{40}", baseline):
        fail("基线 SHA 格式非法：%r（应为 40 位十六进制）" % baseline)
    if baseline != sha and not git_ok(repo, "cat-file", "-e", baseline + "^{commit}"):
        fail("基线 SHA %s 在仓库中不存在（过期或伪造的基线）" % baseline[:12])
    if baseline != sha:
        fail("首次引入的基线必须等于当前 HEAD %s，实际为 %s" % (sha[:12], baseline[:12]))
    return baseline


def check_state_version(state, expected, where: str) -> None:
    if expected is None:
        return
    current = state["state_version"]
    if int(expected) != int(current):
        fail("状态版本不匹配：本窗口记录 v%s，%s 当前为 v%s（本窗口是旧窗口）" % (expected, where, current))


def check_identity(repo: Path, state, agent: str, task: str) -> dict:
    task_obj = state["current_task"]
    task_id = task_obj["task_id"]

    if task and task != task_id:
        fail("任务编号不匹配：本次声明 %r，当前唯一有效任务为 %r" % (task, task_id))

    status = task_obj["status"]
    if agent == "Work":
        if task_obj.get("assigned_to") != "Work":
            fail("当前任务未授权给 Work 施工（assigned_to=%r）" % task_obj.get("assigned_to"))
        if status not in WORK_STATUSES:
            fail("任务状态 %r 不允许施工，Work 仅可在 in_progress 状态下施工" % status)
    else:
        auditor = task_obj.get("auditor")
        coordinator = task_obj.get("coordinator")
        if not auditor and not coordinator:
            fail("%s 缺少 auditor / coordinator 字段，无法确认 %s 的角色授权" % (STATE_REL, agent))
        if agent not in (auditor, coordinator):
            fail("角色 %s 未被授权：当前 auditor=%r，coordinator=%r" % (agent, auditor, coordinator))
        if status not in AUDITOR_STATUSES:
            fail("任务状态 %r 不允许审计，仅 in_progress / pending_acceptance 可审计" % status)
    return task_obj


def check_no_escalation(old, new, changes, repo: Path) -> None:
    """暂存区版本不得相对 HEAD 版本自我扩大授权。"""
    old_task = old["current_task"]
    new_task = new["current_task"]

    if new_task["task_id"] != old_task["task_id"]:
        fail(
            "任务编号在同次提交中被更改（%r → %r）。任务编号变更必须另立任务，不能自改。"
            % (old_task["task_id"], new_task["task_id"])
        )

    for key in ("assigned_to", "auditor", "coordinator"):
        if new_task.get(key) != old_task.get(key):
            fail(
                "角色字段 %s 在同次提交中被更改（%r → %r）。角色变更必须另行走人工流程，"
                "不能由待审状态自行授予。" % (key, old_task.get(key), new_task.get(key))
            )

    # 施工权限以 HEAD 状态为准：已提交状态一旦离开 in_progress，暂存区不得把它改回来。
    if old_task.get("status") != "in_progress" and new_task.get("status") == "in_progress":
        fail(
            "任务状态在同次提交中从 %r 改回 in_progress（暂存区自行恢复施工权限）。"
            "施工权限只能由协调者在独立提交中授予。" % old_task.get("status")
        )

    old_allowed = old_task.get("allowed_paths") or []
    new_allowed = new_task.get("allowed_paths") or []
    widened = [p for p in new_allowed if not prefix_within(p, old_allowed)]
    if widened:
        fail(
            "授权范围在同次提交中被扩大：%s。新增授权必须由协调者在独立提交中给出，"
            "不能与被授权动作放在同一次提交里。" % widened
        )

    old_protected = set(old_task.get("protected_state_files") or [])
    new_protected = set(new_task.get("protected_state_files") or [])
    removed = sorted(old_protected - new_protected)
    if removed:
        fail("受保护文件清单被削弱：移除了 %s" % removed)

    old_baseline = old["git_baseline"]["baseline_sha"].strip()
    new_baseline = new["git_baseline"]["baseline_sha"].strip()
    if old_baseline != new_baseline:
        touched = set()
        for change in changes:
            touched.update(change_paths(change))
        others = sorted(touched - {STATE_REL})
        if others:
            fail(
                "同次提交既刷新基线又改动其他文件（%s）。刷新基线必须单独提交，"
                "不能与被授权动作混在一起以绕过检查。" % others
            )
        current = head_sha(repo)
        if new_baseline != current:
            fail(
                "新基线 %s 必须等于当前 HEAD %s；不允许把基线指向历史提交或未来提交。"
                % (new_baseline[:12], (current or "?")[:12])
            )


def check_parameters(old, new) -> None:
    old_params = old.get("parameters") or {}
    new_params = new.get("parameters") or {}

    for name, entry in new_params.items():
        if not isinstance(entry, dict):
            fail("参数结构异常：参数 %r 的条目不是对象" % name)
        if not isinstance(entry.get("approved"), bool):
            fail("参数结构异常：参数 %r 的 approved 必须是布尔值" % name)

    for name, old_entry in old_params.items():
        if name not in new_params:
            fail("参数 %r 被删除。参数清单不得在同次提交中被删减（删除即等于取消保护锁）。" % name)
        new_entry = new_params[name]
        if not old_entry.get("approved", False) and new_entry.get("approved", False):
            fail(
                "参数 %r 未经用户批准被改为 approved=true。门禁不提供 USER_APPROVED 之类"
                "的环境变量旁路；批准必须由用户在决策文档中明文确认后单独落地。" % name
            )
        if old_entry.get("approved", False) and not new_entry.get("approved", False):
            fail(
                "参数 %r 的批准被撤销（approved true→false）。撤销批准等于降低保护级别，"
                "同样需要独立授权，不能在普通施工提交里顺手改掉。" % name
            )
        if old_entry.get("approved", False) and old_entry.get("value") != new_entry.get("value"):
            fail(
                "已锁定参数 %r 的值被篡改：%r → %r"
                % (name, old_entry.get("value"), new_entry.get("value"))
            )

    for name in new_params:
        if name not in old_params and new_params[name].get("approved", False):
            fail("新增参数 %r 直接以 approved=true 引入，等于自证已获批准，拒绝。" % name)


def check_scope(state, agent: str, changes) -> list:
    task_obj = state["current_task"]
    allowed = list(task_obj.get("allowed_paths") or [])
    protected = set(task_obj.get("protected_state_files") or [])
    coordinator = task_obj.get("coordinator")

    touched = []
    for change in changes:
        touched.extend(change_paths(change))

    outside = [p for p in touched if not path_allowed(p, allowed)]
    if outside:
        fail("越权施工：以下文件不在授权范围 %s 内：%s" % (allowed, outside))

    if agent != coordinator:
        for path in touched:
            if path in protected and not any(_norm(path) == _norm(q) for q in allowed):
                fail(
                    "权限隔离：角色 %s 无权修改受保护文件 %s。"
                    "受保护文件必须在任务授权中被精确列出（目录前缀授权不够）。" % (agent, path)
                )
    return touched


# ── 严格校验主流程 ──────────────────────────────────────────────────────────


def run_strict(repo: Path, agent, task, state_version) -> int:
    if not repo.is_dir():
        fail("仓库目录不存在：%s" % repo)
    if not repo_is_git(repo):
        fail("Git 读取失败：%s 不是可用的 Git 仓库" % repo)
    if not agent:
        fail("缺少 --agent 身份信息。严格模式必须显式声明角色，拒绝隐式默认。")
    if agent not in ROLES:
        fail("非法角色 %r，仅允许 %s" % (agent, "/".join(ROLES)))
    if not task or not str(task).strip():
        fail("缺少 --task 任务编号。严格模式必须显式声明本次任务，缺少即拒绝。")
    if state_version is None:
        fail("缺少 --state-version 任务版本。严格模式必须显式声明本窗口记录的状态版本，缺少即拒绝。")

    head_kind, head_payload = read_head_state(repo)
    if head_kind == "error":
        fail(str(head_payload))

    idx_kind, idx_payload, idx_raw = read_index_state(repo)
    if idx_kind == "error":
        fail(str(idx_payload))

    changes, err = staged_changes(repo)
    if err:
        fail(err)

    if idx_kind == "absent" and head_kind == "absent":
        fail(
            "HEAD 与暂存区都没有 %s，门禁没有任何授权依据。"
            "首次引入必须把状态文件加入暂存区并走首次引入审核。" % STATE_REL
        )
    if head_kind == "ok" and idx_kind == "absent":
        fail(
            "暂存区缺少 %s：HEAD 中存在该文件，本次提交将其删除。"
            "状态文件不得通过删除来绕过参数与授权校验。" % STATE_REL
        )

    authoritative = head_payload if head_kind == "ok" else None
    candidate = idx_payload if idx_kind == "ok" else None
    first_intro = head_kind == "absent" and candidate is not None

    if candidate is not None:
        validate_structure(candidate, "暂存区 " + STATE_REL)
    if authoritative is not None:
        validate_structure(authoritative, "HEAD " + STATE_REL)

    basis = candidate if candidate is not None else authoritative
    basis_where = "暂存区 " + STATE_REL if candidate is not None else "HEAD " + STATE_REL

    branch = check_branch(repo, basis, basis_where)
    if first_intro:
        baseline = check_first_intro_baseline(repo, basis)
    else:
        # 基线判定必须用**已提交**的授权依据，不能用暂存区版本：
        # 否则暂存区往 protected_state_files 里加一项，就能把「别的窗口提交了普通文件」
        # 伪装成「只是状态文件更新」。
        baseline = check_baseline(
            repo,
            authoritative if authoritative is not None else basis,
            "HEAD " + STATE_REL if authoritative is not None else basis_where,
        )
    check_state_version(basis, state_version, basis_where)

    if candidate is not None and authoritative is not None:
        if int(candidate["state_version"]) < int(authoritative["state_version"]):
            fail(
                "状态版本回退：HEAD v%s → 暂存区 v%s。旧窗口不得覆盖新状态。"
                % (authoritative["state_version"], candidate["state_version"])
            )
        check_no_escalation(authoritative, candidate, changes, repo)

    # 首次引入：HEAD 确实没有状态文件，但暂存区有。
    # 在「可信审核凭据」方案确认前，严格模式一律拒绝，不接受调用者传回的哈希自证。
    if first_intro:
        approved = [k for k, v in candidate["parameters"].items() if v.get("approved", False)]
        digest = canonical_digest(idx_raw) if idx_raw else "（无法计算）"
        fail(
            "首次引入被拒绝：可信审核凭据方案尚未确认，严格模式不提供首次引入放行通道。\n"
            "      待引入文件规范化哈希 = %s\n"
            "      其中已批准参数 %s\n"
            "      请先用 audit-config 做完整配置审核，再由独立方给出审核凭据；"
            "在该机制落地前，本门禁不会凭调用者传入的哈希放行。"
            % (digest, approved if approved else "无")
        )

    # 施工/审计权限一律以 **HEAD 已提交状态** 判断，不看暂存区版本：
    # 否则 HEAD 已是 pending_acceptance / completed 时，暂存区把状态改回 in_progress
    # 就能自行恢复施工权限。
    auth_state = authoritative if authoritative is not None else basis
    task_obj = check_identity(repo, auth_state, agent, task)

    if candidate is not None and authoritative is not None:
        check_parameters(authoritative, candidate)

    touched = check_scope(basis, agent, changes)

    print("=" * 68)
    print("✅ 严格校验通过")
    print("   代理     : %s" % agent)
    print("   任务     : %s（%s）" % (task_obj["task_id"], task_obj["status"]))
    print("   分支/HEAD: %s / %s" % (branch, head_sha(repo)[:12]))
    print("   基线     : %s" % baseline[:12])
    print("   状态版本 : v%s" % basis["state_version"])
    print("   授权范围 : %s" % ", ".join(basis["current_task"]["allowed_paths"]))
    print("   暂存改动 : %d 项 %s" % (len(changes), [s for s, _, _ in changes]))
    if touched:
        print("   涉及文件 : %s" % touched)
    locked = [k for k, v in basis["parameters"].items() if v.get("approved", False)]
    print("   参数锁   : 共 %d 项，已批准 %d 项%s"
          % (len(basis["parameters"]), len(locked), ("（%s）" % locked) if locked else ""))
    print("=" * 68)
    return 0


# ── 子命令 ──────────────────────────────────────────────────────────────────


def cmd_verify(args) -> int:
    repo = Path(args.repo).resolve() if args.repo else DEFAULT_REPO

    if not args.strict:
        print("[门禁未启用] 默认模式不校验暂存区、参数与角色，本次提交按普通流程继续。")
        print("             当前阶段强制门禁保持关闭；如需强制校验请显式加 --strict。")
        print("             注意：本次输出不代表任何校验结论。")
        return 0

    try:
        return run_strict(repo, args.agent, args.task, args.state_version)
    except GateError as exc:
        print("[GATE BLOCKED] %s" % exc)
        return 2


def cmd_pre_exec(args) -> int:
    repo = Path(args.repo).resolve() if args.repo else DEFAULT_REPO
    if not args.strict:
        print("[门禁未启用] pre-exec 默认不做任何校验。")
        return 0
    try:
        return run_strict(repo, args.agent, args.task, args.state_version)
    except GateError as exc:
        print("[GATE BLOCKED] %s" % exc)
        return 2


def cmd_status(args) -> int:
    repo = Path(args.repo).resolve() if args.repo else DEFAULT_REPO
    print("=" * 68)
    print("📌 项目目录 : %s" % repo)
    if not repo.is_dir() or not repo_is_git(repo):
        print("🚨 Git 读取失败：不是可用的 Git 仓库")
        print("=" * 68)
        return 2

    head_kind, head_payload = read_head_state(repo)
    idx_kind, idx_payload, _ = read_index_state(repo)

    print("🌿 当前分支 : %s | HEAD: %s" % (current_branch(repo) or "?", head_sha(repo)[:12]))
    print("🔒 门禁状态 : 未启用（默认）；仅 verify --strict 才会强制校验")

    for label, kind, payload in (
        ("HEAD 状态文件", head_kind, head_payload),
        ("暂存区状态文件", idx_kind, idx_payload),
    ):
        if kind == "absent":
            print("📄 %-10s: 不存在" % label)
        elif kind == "error":
            print("📄 %-10s: 读取失败 — %s" % (label, payload))
        else:
            print("📄 %-10s: v%s | 任务 %s（%s）"
                  % (label, payload["state_version"], payload["current_task"]["task_id"],
                     payload["current_task"]["status"]))

    if idx_kind == "ok":
        params = idx_payload["parameters"]
        locked = [k for k, v in params.items() if v.get("approved", False)]
        print("🔐 参数       : 共 %d 项，已批准 %d 项%s"
              % (len(params), len(locked), ("（%s）" % locked) if locked else ""))
        print("📂 授权范围   : %s" % ", ".join(idx_payload["current_task"]["allowed_paths"]))
    print("=" * 68)
    return 0


def cmd_audit_config(args) -> int:
    """完整配置审核入口（只读）。

    输出逐字段审核表，供独立方核对；**不产生任何放行凭据**，
    严格模式的首次引入在可信审核凭据方案确认前始终拒绝。
    """
    repo = Path(args.repo).resolve() if args.repo else DEFAULT_REPO
    print("=" * 68)
    print("📋 完整配置审核（只读，不构成通过凭据）")
    print("📌 项目目录 : %s" % repo)
    if not repo.is_dir() or not repo_is_git(repo):
        print("🚨 Git 读取失败：不是可用的 Git 仓库")
        print("=" * 68)
        return 2

    head_kind, head_payload = read_head_state(repo)
    idx_kind, idx_payload, idx_raw = read_index_state(repo)
    print("🌿 分支/HEAD: %s / %s" % (current_branch(repo) or "?", head_sha(repo)[:12]))

    overall_ok = True
    for label, kind, payload, raw in (
        ("HEAD 版本", head_kind, head_payload, b""),
        ("暂存区版本", idx_kind, idx_payload, idx_raw),
    ):
        print("-" * 68)
        if kind == "absent":
            print("📄 %s：不存在" % label)
            continue
        if kind == "error":
            print("📄 %s：读取失败 — %s" % (label, payload))
            overall_ok = False
            continue

        print("📄 %s" % label)
        try:
            validate_structure(payload, label)
        except GateError as exc:
            print("   ❌ 结构校验失败：%s" % exc)
            overall_ok = False
            continue
        print("   ✅ 结构校验通过")

        task = payload["current_task"]
        print("   project            : %s" % payload["project"])
        print("   state_version      : v%s" % payload["state_version"])
        print("   git_baseline.branch: %s" % payload["git_baseline"]["branch"])
        print("   git_baseline.sha   : %s" % payload["git_baseline"]["baseline_sha"])
        print("   task_id            : %s" % task["task_id"])
        print("   task.status        : %s" % task["status"])
        print("   assigned_to        : %s" % task.get("assigned_to"))
        print("   auditor            : %s" % task.get("auditor"))
        print("   coordinator        : %s" % task.get("coordinator"))
        print("   allowed_paths      : %s" % ", ".join(task.get("allowed_paths") or []))
        print("   protected_files    : %s" % ", ".join(task.get("protected_state_files") or []))
        print("   参数逐项：")
        for name, entry in payload["parameters"].items():
            mark = "已批准" if entry.get("approved") else "未批准"
            print("     - %-28s %-8s 值=%r" % (name, mark, entry.get("value")))
            print("       批准出处 : %s" % (entry.get("approval_source") or "—"))
            print("       取值来源 : %s" % (entry.get("source") or "—"))
            print("       说明     : %s" % (entry.get("note") or "—"))

        if raw:
            print("   规范化哈希 : %s" % canonical_digest(raw))

    print("=" * 68)
    print("⚠️ 本输出只是审核材料，不是批准凭据。")
    print("   严格模式在可信审核凭据方案确认前，对首次引入一律拒绝，")
    print("   不会因为调用者传回某个哈希而放行。")
    print("=" * 68)
    return 0 if overall_ok else 2


def build_parser():
    parser = argparse.ArgumentParser(description="Wyckoff V3.1 多代理门禁（默认不启用）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--repo", default=None, help="仓库根目录（默认取脚本所在仓库）")
        p.add_argument("--strict", action="store_true", help="启用强制校验；不加以则只报告门禁未启用")
        p.add_argument("--agent", default=None, help="角色：Codex / Work / Antigravity")
        p.add_argument("--task", default=None, help="任务编号（严格模式必填）")
        p.add_argument("--state-version", type=int, default=None, help="本窗口记录的状态版本号（严格模式必填）")

    common(sub.add_parser("verify"))
    common(sub.add_parser("pre-exec"))
    for name in ("status", "audit-config"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=None)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "pre-exec":
        return cmd_pre_exec(args)
    if args.cmd == "audit-config":
        return cmd_audit_config(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
