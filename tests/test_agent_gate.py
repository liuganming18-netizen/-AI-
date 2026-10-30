# -*- coding: utf-8 -*-
"""AGENT-GATE-ADAPT-01 隔离测试（整改版）。

原则
----
* 每个用例在独立临时目录里新建仓库，HEAD / 暂存区 / 工作区三层差异全部**真实建立**，
  不用 mock 顶替 git 返回值。
* 不调用 ``git commit``：历史由 ``write-tree`` + ``commit-tree`` + ``update-ref`` 构造。
* 不触碰项目仓库的暂存区、行情数据、快照 JSON 或运行锁；所有 git 命令的 cwd 都在临时目录内。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "框架" / "ops" / "agent_gate.py"

ALLOWED = ["框架/ops/agent_gate.py", "project_state.json", "tests/test_agent_gate.py"]
PROTECTED = ["project_state.json", "框架/ops/agent_gate.py"]
DECISION = "docs/用户约定/用户要求与决策.md"
NO_PARAMS = {"p1": {"value": 0.05, "approved": False, "source": "test"}}


def approved(value, line=66, source="策略/x.py:1"):
    return {"value": value, "approved": True,
            "approval_source": "%s:%s" % (DECISION, line), "source": source}


# ── 临时仓库夹具 ────────────────────────────────────────────────────────────


def _git(repo, *args, check=True, env=None):
    run_env = None
    if env:
        run_env = dict(os.environ)
        run_env.update(env)
    res = subprocess.run(
        ["git", "-c", "core.quotepath=false"] + list(args),
        cwd=str(repo), capture_output=True, text=True, env=run_env,
    )
    if check and res.returncode != 0:
        raise AssertionError("git %s 失败: %s" % (" ".join(args), res.stderr))
    return res


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(path), check=True)
    _git(path, "config", "user.email", "gate@test.local")
    _git(path, "config", "user.name", "Gate Test")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def make_commit(repo: Path, message="fixture") -> str:
    """用底层管道造提交对象，避免调用 git commit。"""
    _git(repo, "add", "-A")
    tree = _git(repo, "write-tree").stdout.strip()
    args = ["commit-tree", tree, "-m", message]
    head = _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    if head.returncode == 0 and head.stdout.strip():
        args += ["-p", head.stdout.strip()]
    sha = _git(repo, *args).stdout.strip()
    _git(repo, "update-ref", "refs/heads/main", sha)
    return sha


def tree_of(repo: Path, commit: str) -> str:
    return _git(repo, "rev-parse", commit + "^{tree}").stdout.strip()


def build_tree(repo: Path, base_tree: str, changes) -> str:
    """在**独立索引文件**里从 base_tree 出发做改动并写树，不碰工作区与主索引。

    changes 形如 {路径: 内容}，内容为 None 表示删除该路径。
    合并提交的两个父树必须各自独立构造，所以不能借用工作区 + ``git add``。
    """
    workdir = Path(tempfile.mkdtemp(prefix="agent-gate-idx-"))
    env = {"GIT_INDEX_FILE": str(workdir / "index")}
    try:
        _git(repo, "read-tree", base_tree, env=env)
        for rel, content in changes.items():
            if content is None:
                _git(repo, "update-index", "--force-remove", rel, env=env)
                continue
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=str(repo), input=content, capture_output=True, text=True, check=True,
            ).stdout.strip()
            _git(repo, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, rel), env=env)
        return _git(repo, "write-tree", env=env).stdout.strip()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def commit_tree(repo: Path, tree: str, parents, message="fixture") -> str:
    args = ["commit-tree", tree, "-m", message]
    for parent in parents:
        args += ["-p", parent]
    return _git(repo, *args).stdout.strip()


def build_commit(repo: Path, base_tree: str, changes, parents, message="fixture") -> str:
    return commit_tree(repo, build_tree(repo, base_tree, changes), parents, message)


def write(repo: Path, rel: str, content) -> Path:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    target.write_text(content, encoding="utf-8")
    return target


def read_state(repo: Path) -> dict:
    return json.loads((repo / "project_state.json").read_text(encoding="utf-8"))


def run_gate(repo: Path, *args, env=None, task="T-1", state_version=1):
    """严格模式下自动补齐任务编号与任务版本（用例需要故意缺失时传 None）。"""
    cmd = [sys.executable, str(GATE)] + list(args)
    if args and args[0] in ("verify", "pre-exec") and "--strict" in args:
        if task is not None and "--task" not in args:
            cmd += ["--task", str(task)]
        if state_version is not None and "--state-version" not in args:
            cmd += ["--state-version", str(state_version)]
    cmd += ["--repo", str(repo)]
    run_env = dict(os.environ)
    run_env.pop("USER_APPROVED", None)
    if env:
        run_env.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo), env=run_env)


def state_doc(baseline, *, branch="main", version=1, task="T-1", status="in_progress",
              assigned="Work", auditor="Codex", coordinator="Codex",
              allowed=None, protected=None, params=None, blockers=None):
    return {
        "project": "WyckoffMaster v3.1",
        "state_version": version,
        "git_baseline": {"branch": branch, "baseline_sha": baseline},
        "current_task": {
            "task_id": task,
            "title": "fixture",
            "status": status,
            "assigned_to": assigned,
            "auditor": auditor,
            "coordinator": coordinator,
            "allowed_paths": list(allowed if allowed is not None else ALLOWED),
            "protected_state_files": list(protected if protected is not None else PROTECTED),
            "blockers": list(blockers or []),
        },
        "parameters": json.loads(json.dumps(params if params is not None else NO_PARAMS)),
    }


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-gate-test-")
        self.repo = init_repo(Path(self._tmp.name) / "repo")
        write(self.repo, "README.md", "seed\n")
        self.base = make_commit(self.repo)
        self.head = self.base

    def tearDown(self):
        self._tmp.cleanup()

    def commit_state(self, extra_files=None, baseline=None, **over):
        """提交一版状态文件（HEAD 含状态文件），返回最终状态文档。"""
        seed_baseline = baseline if baseline is not None else self.base
        write(self.repo, "project_state.json", state_doc(seed_baseline, **over))
        for rel, content in (extra_files or {}).items():
            write(self.repo, rel, content)
        head1 = make_commit(self.repo)
        final_baseline = baseline if baseline is not None else head1
        doc = state_doc(final_baseline, **over)
        write(self.repo, "project_state.json", doc)
        self.head = make_commit(self.repo)
        return doc

    def stage_state(self, mutate):
        doc = read_state(self.repo)
        mutate(doc)
        write(self.repo, "project_state.json", doc)
        _git(self.repo, "add", "project_state.json")
        return doc

    def stage_file(self, rel, content="# staged\n"):
        write(self.repo, rel, content)
        _git(self.repo, "add", rel)

    def assertBlocked(self, res, keyword):
        self.assertEqual(res.returncode, 2, "应被拦截，实际通过。输出:\n%s" % res.stdout)
        self.assertIn(keyword, res.stdout, "拦截原因不含关键词 %r。输出:\n%s" % (keyword, res.stdout))

    def assertPassed(self, res):
        self.assertEqual(res.returncode, 0, "应通过，实际被拦截。输出:\n%s" % res.stdout)
        self.assertIn("严格校验通过", res.stdout)


# ── 1. 默认模式 ─────────────────────────────────────────────────────────────


class DefaultModeTests(GateTestCase):
    def test_default_reports_disabled_and_does_not_block(self):
        self.stage_file("框架/backend/paper_engine.py", "# 越权但默认不拦\n")
        res = run_gate(self.repo, "verify", "--agent", "Work")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("门禁未启用", res.stdout)
        self.assertNotIn("严格校验通过", res.stdout)

    def test_default_without_any_state_file(self):
        self.stage_file("a.txt", "x\n")
        res = run_gate(self.repo, "verify")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("门禁未启用", res.stdout)

    def test_default_ignores_unknown_role(self):
        res = run_gate(self.repo, "verify", "--agent", "Nobody")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("门禁未启用", res.stdout)

    def test_default_pre_exec_does_not_block(self):
        res = run_gate(self.repo, "pre-exec", "--agent", "Nobody", "--task", "WRONG")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("门禁未启用", res.stdout)


# ── 2. 首次引入：一律拒绝 ───────────────────────────────────────────────────


class FirstIntroTests(GateTestCase):
    def _stage(self, **over):
        write(self.repo, "project_state.json", state_doc(self.base, **over))
        _git(self.repo, "add", "project_state.json")

    def test_first_intro_always_rejected(self):
        self._stage()
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "首次引入被拒绝")
        self.assertIn("不提供首次引入放行通道", res.stdout)
        self.assertIn("规范化哈希", res.stdout)

    def test_trust_anchor_argument_no_longer_exists(self):
        """调用者传回哈希这条路必须已经不存在。"""
        self._stage()
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work",
                       "--trust-anchor", "a" * 64)
        self.assertNotEqual(res.returncode, 0, "trust-anchor 参数仍被接受，旁路未删除")
        self.assertIn("unrecognized arguments", res.stderr)

    def test_first_intro_rejected_even_with_approvals(self):
        self._stage(params={"p1": approved(0.05)})
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "首次引入被拒绝")
        self.assertIn("已批准参数", res.stdout)

    def test_unknown_baseline_rejected(self):
        write(self.repo, "project_state.json", state_doc("0" * 40))
        _git(self.repo, "add", "project_state.json")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "不存在")

    def test_wrong_branch_rejected(self):
        self._stage(branch="release/x")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "分支不匹配")

    def test_missing_parameter_value_rejected(self):
        write(self.repo, "project_state.json", state_doc(self.base, params={"p1": {"approved": False}}))
        _git(self.repo, "add", "project_state.json")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "缺少 value 字段")


# ── 3. 文件范围 ─────────────────────────────────────────────────────────────


class ScopeTests(GateTestCase):
    def setUp(self):
        super().setUp()
        self.commit_state()

    def test_allowed_file_passes(self):
        self.stage_file("tests/test_agent_gate.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_unauthorized_file_blocked(self):
        self.stage_file("框架/backend/paper_engine.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "越权施工")

    def test_directory_boundary_not_prefix_matched(self):
        self.commit_state(allowed=["evidence/"])
        self.stage_file("evidence_evil/a.json", "{}\n")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "越权施工")

    def test_directory_prefix_allows_nested(self):
        self.commit_state(allowed=["evidence/"])
        self.stage_file("evidence/sub/a.json", "{}\n")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_chinese_and_space_path_passes(self):
        self.commit_state(allowed=["docs/用户约定/"])
        self.stage_file("docs/用户约定/当前交接 副本.md")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_deletion_in_scope_passes(self):
        self.commit_state(extra_files={"tests/test_agent_gate.py": "# tracked\n"})
        _git(self.repo, "rm", "-q", "tests/test_agent_gate.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_deletion_out_of_scope_blocked(self):
        self.commit_state(extra_files={"框架/backend/paper_engine.py": "# tracked\n"})
        _git(self.repo, "rm", "-q", "框架/backend/paper_engine.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "越权施工")

    def test_rename_new_end_out_of_scope_blocked(self):
        self.commit_state(extra_files={"框架/ops/agent_gate.py": "# placeholder\n"})
        _git(self.repo, "mv", "框架/ops/agent_gate.py", "框架/ops/agent_gate_moved.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "越权施工")

    def test_rename_within_scope_passes(self):
        self.commit_state(allowed=["tests/"], extra_files={"tests/old_name.py": "# x\n"})
        _git(self.repo, "mv", "tests/old_name.py", "tests/new_name.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))


# ── 4. 基线与版本 ───────────────────────────────────────────────────────────


class BaselineTests(GateTestCase):
    def test_forked_baseline_blocked(self):
        _git(self.repo, "add", "-A")
        tree = _git(self.repo, "write-tree").stdout.strip()
        orphan = _git(self.repo, "commit-tree", tree, "-m", "orphan").stdout.strip()
        self.commit_state(baseline=orphan)
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "历史已分叉")

    def test_wrong_branch_blocked(self):
        self.commit_state(branch="release/x")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "分支不匹配")

    def test_state_version_mismatch_blocked(self):
        self.commit_state(version=3)
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work", "--state-version", "2")
        self.assertBlocked(res, "状态版本不匹配")

    def test_state_version_rollback_blocked(self):
        self.commit_state(version=3)
        self.stage_state(lambda d: d.__setitem__("state_version", 2))
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work", "--state-version", "2")
        self.assertBlocked(res, "状态版本回退")

    def test_head_ahead_with_non_state_commit_blocked(self):
        self.commit_state()
        write(self.repo, "框架/backend/other.py", "# 别的窗口已提交\n")
        make_commit(self.repo)
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "非授权提交")

    def test_intermediate_commit_reverted_still_blocked(self):
        """中间提交改了普通文件、随后又改回原样：端点 diff 为空，仍必须拒绝。"""
        self.commit_state()
        write(self.repo, "框架/backend/other.py", "# v1\n")
        make_commit(self.repo)
        (self.repo / "框架/backend/other.py").unlink()
        make_commit(self.repo)
        endpoint = _git(self.repo, "diff", "--name-only", self.head, "HEAD").stdout.strip()
        self.assertEqual(endpoint, "", "构造失败：端点差异应为空，实际 %r" % endpoint)
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "非授权提交")

    def test_placeholder_baseline_blocked(self):
        self.commit_state(baseline="REPLACE_WITH_CURRENT_HEAD_SHA")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "占位符")


# ── 5. 合并历史：父提交比较规则 ─────────────────────────────────────────────


class MergeHistoryTests(GateTestCase):
    """合并提交必须被逐提交核查，但正常合并不得被一律判违规。

    历史全部用底层命令构造（read-tree / update-index / write-tree / commit-tree /
    update-ref），不调用 git merge，也不调用 git commit。
    """

    def setUp(self):
        super().setUp()
        self.commit_state(extra_files={"框架/backend/paper_engine.py": "# v0\n"})
        self.baseline = self.head

    def _doc(self, blockers=()):
        return state_doc(self.baseline, blockers=blockers)

    def build_history(self, main_changes, side_changes, merge_changes, message="merge"):
        """造 main / side 两条分支和一个合并提交，返回合并提交 SHA。"""
        base_tree = tree_of(self.repo, self.baseline)
        main_sha = build_commit(self.repo, base_tree, main_changes, [self.baseline], "main-line")
        side_sha = build_commit(self.repo, base_tree, side_changes, [self.baseline], "side-line")
        merge_tree = build_tree(self.repo, tree_of(self.repo, side_sha), merge_changes)
        merge = commit_tree(self.repo, merge_tree, [main_sha, side_sha], message)
        parents = _git(self.repo, "rev-list", "--parents", "-n", "1", merge).stdout.split()
        self.assertEqual(len(parents), 3, "构造失败：不是双父合并提交 -> %s" % parents)
        _git(self.repo, "update-ref", "refs/heads/main", merge)
        _git(self.repo, "read-tree", merge)
        return merge

    def test_merge_only_unauthorized_file_added_blocked(self):
        """非授权文件只在合并结果里新增（两个父提交都没有），必须拒绝。"""
        merge = self.build_history(
            main_changes={"project_state.json": self._doc(["main"])},
            side_changes={"project_state.json": self._doc(["side"])},
            merge_changes={"框架/backend/evil.py": "# 只在合并结果里出现\n"},
        )
        self.stage_file("tests/test_agent_gate.py")
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "非授权提交")
        self.assertIn("evil.py", res.stdout)
        self.assertIn("合并 2 父", res.stdout, "拦截原因应指明是合并提交带进来的改动")
        self.assertIn(merge[:12], res.stdout)

    def test_merge_conflict_resolution_editing_unauthorized_file_blocked(self):
        """解决冲突时手改了非授权文件，而两个父提交都没碰过这个文件：只能靠合并核查发现。"""
        self.build_history(
            main_changes={"project_state.json": self._doc(["main"])},
            side_changes={"project_state.json": self._doc(["side"])},
            merge_changes={"project_state.json": self._doc(["main", "side"]),
                           "框架/backend/paper_engine.py": "# 解决冲突时顺手改的\n"},
        )
        self.stage_file("tests/test_agent_gate.py")
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "非授权提交")
        self.assertIn("paper_engine.py", res.stdout)
        self.assertIn("合并 2 父", res.stdout, "该改动只存在于合并结果里，应记到合并提交头上")

    def test_merge_conflict_on_unauthorized_file_resolved_to_third_version(self):
        """两个父提交各自改了同一个非授权文件，合并结果取第三种内容：必须拒绝。"""
        self.build_history(
            main_changes={"project_state.json": self._doc(["main"]),
                          "框架/backend/paper_engine.py": "# main 侧\n"},
            side_changes={"project_state.json": self._doc(["side"]),
                          "框架/backend/paper_engine.py": "# side 侧\n"},
            merge_changes={"框架/backend/paper_engine.py": "# 冲突手工解决\n"},
        )
        self.stage_file("tests/test_agent_gate.py")
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "非授权提交")
        self.assertIn("paper_engine.py", res.stdout)

    def test_merge_unauthorized_change_reverted_later_still_blocked(self):
        """合并夹带的改动随后被恢复原样：端点差异为空，旧基线仍必须被拒绝。"""
        merge = self.build_history(
            main_changes={"project_state.json": self._doc(["main"])},
            side_changes={"project_state.json": self._doc(["side"])},
            merge_changes={"框架/backend/evil.py": "# 合并夹带\n"},
        )
        revert = build_commit(self.repo, tree_of(self.repo, merge),
                              {"框架/backend/evil.py": None}, [merge], "revert evil")
        _git(self.repo, "update-ref", "refs/heads/main", revert)
        _git(self.repo, "read-tree", revert)

        endpoint = _git(self.repo, "diff", "--name-only", self.baseline, "HEAD").stdout.split()
        self.assertEqual(sorted(endpoint), ["project_state.json"],
                         "构造失败：端点差异只应剩受保护状态文件，实际 %r" % endpoint)
        self.assertNotIn("evil.py", " ".join(endpoint))

        self.stage_file("tests/test_agent_gate.py")
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "非授权提交")
        self.assertIn("evil.py", res.stdout)

    def test_merge_with_only_authorized_changes_passes(self):
        """两边都只改受保护状态文件、合并时手工解决冲突：合并本身不得被判违规。"""
        self.build_history(
            main_changes={"project_state.json": self._doc(["main"])},
            side_changes={"project_state.json": self._doc(["side"])},
            merge_changes={"project_state.json": self._doc(["main", "side"])},
        )
        self.stage_file("tests/test_agent_gate.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_clean_merge_of_protected_only_branches_passes(self):
        """干净合并（结果与侧分支一致，合并本身没带任何改动）同样通过。"""
        self.build_history(
            main_changes={"project_state.json": self._doc(["main"])},
            side_changes={"project_state.json": self._doc(["side"])},
            merge_changes={},
        )
        self.stage_file("tests/test_agent_gate.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_side_branch_unauthorized_change_blocked_at_branch_commit(self):
        """侧分支自己改非授权文件：算在该分支提交头上，不重复算给合并提交。"""
        self.build_history(
            main_changes={"project_state.json": self._doc(["main"])},
            side_changes={"project_state.json": self._doc(["side"]),
                          "框架/backend/evil_side.py": "# 侧分支带进来\n"},
            merge_changes={},
        )
        self.stage_file("tests/test_agent_gate.py")
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "非授权提交")
        self.assertIn("evil_side.py", res.stdout)
        self.assertNotIn("合并 2 父", res.stdout, "侧分支内容不应被记到合并提交头上")


# ── 6. 角色与任务 ───────────────────────────────────────────────────────────


class RoleAndTaskTests(GateTestCase):
    def setUp(self):
        super().setUp()
        self.commit_state()
        self.stage_file("tests/test_agent_gate.py")

    def test_wrong_role_blocked(self):
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Antigravity"), "未被授权")

    def test_auditor_role_allowed(self):
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Codex"))

    def test_wrong_task_id_blocked(self):
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work", "--task", "OTHER-01")
        self.assertBlocked(res, "任务编号不匹配")

    def test_task_status_not_in_progress_blocked(self):
        self.commit_state(status="pending_acceptance")
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "不允许施工")

    def test_missing_agent_blocked(self):
        self.assertBlocked(run_gate(self.repo, "verify", "--strict"), "缺少 --agent")

    def test_illegal_role_value_blocked(self):
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Nobody"), "非法角色")

    def test_missing_identity_field_blocked(self):
        self.stage_state(lambda d: d["current_task"].pop("assigned_to"))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "缺少必要字段")

    def test_missing_state_version_field_blocked(self):
        self.stage_state(lambda d: d.pop("state_version"))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "缺少必要字段")


# ── 7. 四个已复现绕过的回归测试 ─────────────────────────────────────────────


class BypassRegressionTests(GateTestCase):
    def setUp(self):
        super().setUp()
        self.commit_state()
        self.stage_file("tests/test_agent_gate.py")

    def test_missing_task_argument_rejected(self):
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work", task=None)
        self.assertBlocked(res, "缺少 --task")

    def test_missing_state_version_argument_rejected(self):
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work", state_version=None)
        self.assertBlocked(res, "缺少 --state-version")

    def test_approval_revocation_blocked(self):
        """已批准参数被改成 approved=false（撤销保护），必须拒绝。"""
        self.commit_state(params={"p1": approved(0.05)})
        self.stage_file("tests/test_agent_gate.py")
        self.stage_state(lambda d: d["parameters"]["p1"].__setitem__("approved", False))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "批准被撤销")

    def test_head_status_cannot_be_restored_by_index(self):
        """HEAD 已离开 in_progress 时，暂存区把它改回 in_progress 不能恢复施工权限。"""
        self.commit_state(status="pending_acceptance")
        self.stage_file("tests/test_agent_gate.py")
        self.stage_state(lambda d: d["current_task"].__setitem__("status", "in_progress"))
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "自行恢复施工权限")

    def test_head_status_not_in_progress_blocks_without_any_status_edit(self):
        """即使不动状态字段，HEAD 非施工态也不得施工。"""
        self.commit_state(status="pending_acceptance")
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "不允许施工")


# ── 8. 授权不得自我扩大 ─────────────────────────────────────────────────────


class EscalationTests(GateTestCase):
    def setUp(self):
        super().setUp()
        self.commit_state()

    def test_cannot_widen_allowed_paths(self):
        self.stage_state(lambda d: d["current_task"]["allowed_paths"].append("框架/backend/"))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "授权范围在同次提交中被扩大")

    def test_cannot_change_role(self):
        self.stage_state(lambda d: d["current_task"].__setitem__("assigned_to", "Antigravity"))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "角色字段 assigned_to")

    def test_cannot_change_task_id(self):
        self.stage_state(lambda d: d["current_task"].__setitem__("task_id", "NEW-01"))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "任务编号在同次提交中被更改")

    def test_cannot_refresh_baseline_with_other_files(self):
        self.stage_state(lambda d: d["git_baseline"].__setitem__("baseline_sha", self.base))
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "同次提交既刷新基线又改动其他文件")

    def test_cannot_drop_protected_file(self):
        self.stage_state(lambda d: d["current_task"].__setitem__("protected_state_files", ["project_state.json"]))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "受保护文件清单被削弱")

    def test_cannot_inflate_protected_list_to_mask_foreign_commit(self):
        """往 protected_state_files 里加一项，不能把「别的窗口提交了普通文件」洗成状态更新。"""
        self.commit_state()
        write(self.repo, "框架/backend/other.py", "# 别的窗口已提交\n")
        make_commit(self.repo)
        self.stage_state(
            lambda d: d["current_task"]["protected_state_files"].append("框架/backend/other.py")
        )
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "非授权提交")


# ── 9. 参数保护 ─────────────────────────────────────────────────────────────


class ParameterTests(GateTestCase):
    def test_approval_flag_flip_blocked(self):
        self.commit_state()
        self.stage_state(lambda d: d["parameters"]["p1"].update(approved(0.05)))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "未经用户批准被改为 approved=true")

    def test_env_var_is_not_approval(self):
        self.commit_state()
        self.stage_state(lambda d: d["parameters"]["p1"].update(approved(0.05)))
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work", env={"USER_APPROVED": "p1"})
        self.assertBlocked(res, "未经用户批准被改为 approved=true")

    def test_locked_value_change_blocked(self):
        self.commit_state(params={"p1": approved(0.05)})
        self.stage_state(lambda d: d["parameters"]["p1"].__setitem__("value", 0.5))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "已锁定参数")

    def test_parameter_deletion_blocked(self):
        self.commit_state()
        self.stage_state(lambda d: d.__setitem__("parameters", {}))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "被删除")

    def test_parameter_structure_invalid_blocked(self):
        self.commit_state()
        self.stage_state(lambda d: d["parameters"]["p1"].__setitem__("approved", "yes"))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "approved 必须是布尔值")

    def test_unapproved_value_change_allowed(self):
        self.commit_state()
        self.stage_state(lambda d: d["parameters"]["p1"].__setitem__("value", 0.99))
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_new_parameter_with_approval_blocked(self):
        self.commit_state()
        self.stage_state(lambda d: d["parameters"].__setitem__("p2", approved(1, line=99)))
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "新增参数")


# ── 10. 批准出处必须来自用户决策记录 ─────────────────────────────────────────


class ApprovalSourceTests(GateTestCase):
    def test_approved_without_source_rejected(self):
        self.commit_state(params={"p1": {"value": 0.05, "approved": True, "source": "x"}})
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "缺少 approval_source")

    def test_approved_with_code_source_rejected(self):
        self.commit_state(params={"p1": {"value": 0.05, "approved": True,
                                         "approval_source": "策略/WyckoffMaster/event_trend_v0_1/config.py:81",
                                         "source": "x"}})
        self.stage_file("tests/test_agent_gate.py")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "必须指向统一用户决策记录")

    def test_approved_with_decision_record_passes(self):
        self.commit_state(params={"p1": approved(0.05)})
        self.stage_file("tests/test_agent_gate.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))


# ── 11. 暂存区 vs 工作区 ────────────────────────────────────────────────────


class IndexVersusWorktreeTests(GateTestCase):
    def test_tampered_index_with_clean_worktree_blocked(self):
        """暂存区被篡改、工作区恢复正常，仍必须拒绝（证明不读工作区版本）。"""
        self.commit_state()
        clean = (self.repo / "project_state.json").read_text(encoding="utf-8")
        tampered = json.loads(clean)
        tampered["parameters"]["p1"].update(approved(0.05))
        write(self.repo, "project_state.json", tampered)
        _git(self.repo, "add", "project_state.json")
        write(self.repo, "project_state.json", clean)  # 工作区恢复成与 HEAD 完全一致
        dirty = _git(self.repo, "diff", "HEAD", "--name-only").stdout.strip()
        self.assertEqual(dirty, "", "工作区应当已恢复成 HEAD 版本，实际仍有改动: %s" % dirty)
        staged = _git(self.repo, "diff", "--cached", "--name-only", "HEAD").stdout.strip()
        self.assertEqual(staged, "project_state.json", "暂存区应仍是篡改版，实际: %r" % staged)
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "approved=true")

    def test_legal_index_with_dirty_worktree_passes(self):
        """暂存区合法、工作区另有未暂存修改，按暂存区判断。"""
        self.commit_state()
        self.stage_file("tests/test_agent_gate.py")
        write(self.repo, "框架/backend/paper_engine.py", "# 未暂存的越权改动\n")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_untracked_runtime_json_ignored(self):
        self.commit_state()
        write(self.repo, "框架/backend/live/pool_diagnostics.json", "{}\n")
        self.stage_file("tests/test_agent_gate.py")
        self.assertPassed(run_gate(self.repo, "verify", "--strict", "--agent", "Work"))

    def test_deleting_state_file_blocked(self):
        self.commit_state()
        _git(self.repo, "rm", "-q", "--cached", "project_state.json")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "状态文件不得通过删除")


# ── 12. 读取失败与非法内容 ──────────────────────────────────────────────────


class ReadFailureTests(GateTestCase):
    def test_not_a_git_repo_reports_git_failure(self):
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        res = run_gate(plain, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "Git 读取失败")
        self.assertNotIn("都没有", res.stdout)

    def test_invalid_json_in_index_blocked(self):
        self.commit_state()
        (self.repo / "project_state.json").write_text("{not json", encoding="utf-8")
        _git(self.repo, "add", "project_state.json")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "不是合法 JSON")

    def test_both_absent_blocked(self):
        self.stage_file("a.txt", "x\n")
        self.assertBlocked(run_gate(self.repo, "verify", "--strict", "--agent", "Work"), "都没有")


# ── 13. 配置审核入口 ────────────────────────────────────────────────────────


class AuditConfigTests(GateTestCase):
    def test_audit_config_lists_fields_and_is_not_a_credential(self):
        self.commit_state(params={"p1": approved(0.05)})
        res = run_gate(self.repo, "audit-config")
        self.assertEqual(res.returncode, 0, res.stdout)
        for token in ("完整配置审核", "state_version", "git_baseline", "allowed_paths",
                      "参数逐项", "批准出处", "不是批准凭据"):
            self.assertIn(token, res.stdout, "审核输出缺少 %r" % token)
        self.assertIn("docs/用户约定/用户要求与决策.md:66", res.stdout)

    def test_audit_config_does_not_unlock_first_intro(self):
        write(self.repo, "project_state.json", state_doc(self.base))
        _git(self.repo, "add", "project_state.json")
        audit = run_gate(self.repo, "audit-config")
        self.assertEqual(audit.returncode, 0, audit.stdout)
        res = run_gate(self.repo, "verify", "--strict", "--agent", "Work")
        self.assertBlocked(res, "首次引入被拒绝")


if __name__ == "__main__":
    unittest.main(verbosity=2)
