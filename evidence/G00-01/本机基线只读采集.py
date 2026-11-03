#!/usr/bin/env python3
"""G00-01 一次性只读本机基线：只读取新仓库元数据和系统信息，打印 JSON 到 stdout。

注意：
- 不访问旧 GM/Wyckoff 的目录或数据库，不扫描全盘，不访问互联网或密钥内容。
- 不创建/修改/删除任何文件，不切换分支，不启动或停止服务。
- 用 python3 -B 运行，避免字节码写入；如需保存报告，由操作者保存到隔离位置。
- GitHub Actions 中的结果只属于托管运行器，不能冒充用户的 Mac。
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = "liuganming18-netizen/-AI-"
ROOT = Path(__file__).resolve().parents[2]
PORTS = (8787, 3000, 5173)
REMOTE_PATTERNS = (
    r"https://(?:[^/@]+@)?github\.com/liuganming18-netizen/-AI-(?:\.git)?",
    r"git@github\.com:liuganming18-netizen/-AI-(?:\.git)?",
    r"ssh://git@github\.com/liuganming18-netizen/-AI-(?:\.git)?",
)


def safe_remote(value: str) -> str:
    # 永不回显 HTTPS URL 中的用户或访问令牌。
    return re.sub(r"^(https?://)[^/@]+@", r"\1[已隐藏凭据]@", value)


def valid_remote(value: str) -> bool:
    return any(re.fullmatch(pattern, value.strip()) for pattern in REMOTE_PATTERNS)


def read_command(*argv: str, timeout: int = 15) -> dict:
    """不使用 shell；只调用明确的只读系统/Git 命令。"""
    try:
        env = os.environ.copy()
        env["GIT_OPTIONAL_LOCKS"] = "0"  # 防止 git status 的可选索引写入
        env["GIT_TERMINAL_PROMPT"] = "0"
        process = subprocess.run(
            list(argv), cwd=str(ROOT), text=True, env=env,
            capture_output=True, check=False, timeout=timeout,
        )
        return {
            "命令": list(argv),
            "退出码": process.returncode,
            "标准输出": process.stdout.rstrip("\n"),
            "标准错误": process.stderr.rstrip("\n"),
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"命令": list(argv), "退出码": None,
                "标准输出": "", "标准错误": type(exc).__name__}


def git_snapshot() -> dict:
    commands = {
        "工作区根目录": ("git", "rev-parse", "--show-toplevel"),
        "当前分支": ("git", "branch", "--show-current"),
        "HEAD": ("git", "rev-parse", "HEAD"),
        "本地变更": ("git", "status", "--porcelain=v1", "-uall"),
    }
    return {key: read_command(*cmd) for key, cmd in commands.items()}


def short_info(cmd: tuple[str, ...]) -> dict:
    row = read_command(*cmd)
    return {"命令": row["命令"], "退出码": row["退出码"],
            "原始输出": row["标准输出"] or row["标准错误"]}


def port_info(port: int) -> dict:
    if shutil.which("lsof") is None:
        return {"状态": "未核验：系统缺少 lsof", "端口": port}
    sample = read_command("lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcn")
    if sample["退出码"] == 1 and not sample["标准输出"]:
        return {"状态": "本次采样无监听", "端口": port}
    if sample["退出码"] != 0:
        return {"状态": "未核验：查询返回异常", "端口": port,
                "退出码": sample["退出码"]}
    # 仅保留进程名和监听地址，不抓取进程参数、路径或密钥。
    fields = [line for line in sample["标准输出"].splitlines()
              if line[:1] in {"c", "n"}]
    return {"状态": "本次采样有监听", "端口": port, "监听摘要": fields}


def self_test() -> int:
    assert valid_remote("https://github.com/liuganming18-netizen/-AI-.git")
    assert valid_remote("git@github.com:liuganming18-netizen/-AI-.git")
    assert valid_remote("ssh://git@github.com/liuganming18-netizen/-AI-")
    assert valid_remote("https://hidden-token@github.com/liuganming18-netizen/-AI-")
    for invalid in (
        "https://github.com/liuganming18-netizen/GM.git",
        "https://github.com/stranger/-AI-",
        "https://github.com/liuganming18-netizen/-AI-.git/../GM",
        "file:///Users/test/GM",
    ):
        assert not valid_remote(invalid), invalid
    assert "token-should-not-appear" not in safe_remote(
        "https://token-should-not-appear@github.com/liuganming18-netizen/-AI-")
    print("PASS：仅新仓库 origin 白名单、旧项目拒绝及凭据脱敏自测")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    if sys.argv[1:]:
        print("仅支持无参数或 --self-test", file=sys.stderr)
        return 2

    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    origin = read_command("git", "remote", "get-url", "origin")
    remote = origin["标准输出"].strip()
    if origin["退出码"] != 0 or not valid_remote(remote):
        # 对未授权工作目录立即停手：不采集其他信息、不读取旧项目。
        print(json.dumps({
            "工单": "G00-01", "裁决": "阻塞",
            "原因": "origin 未严格匹配正式新仓库，未运行任何后续检查",
            "origin_脱敏": "不在白名单内，已隐藏实际地址与参数",
            "开始时间UTC": started_at,
        }, ensure_ascii=False, indent=2))
        return 2

    before = git_snapshot()

    system = {
        "系统": short_info(("uname", "-s")),
        "架构": short_info(("uname", "-m")),
        "Node版本": short_info(("node", "--version")),
        "Python版本": short_info(("python3", "--version")),
        "磁盘空间_仅仓库挂载点": short_info(("df", "-h", ".")),
    }
    if platform.system() == "Darwin":
        system["macOS版本"] = short_info(("sw_vers",))
        system["物理内存字节数"] = short_info(("sysctl", "-n", "hw.memsize"))
    else:
        system["macOS版本"] = {"状态": "不适用；当前不是 macOS"}
        system["物理内存字节数"] = {"状态": "未核验；当前不是 macOS"}

    listeners = {str(port): port_info(port) for port in PORTS}
    after = git_snapshot()

    # 用完整的 Git 状态文本对比，不清理、不覆盖已有未知文件。
    drift = {field: {
        "前": before[field]["标准输出"],
        "后": after[field]["标准输出"],
    } for field in before if before[field] != after[field]}
    github_hosted = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    provenance = (
        "GitHub Actions 托管运行器，不是用户本机"
        if github_hosted else
        "非 GitHub Actions 的 macOS 候选现场，须人工确认机器身份"
        if platform.system() == "Darwin" else
        "非 GitHub Actions 的非 macOS 环境，不可替代用户 Mac"
    )

    report = {
        "工单": "G00-01",
        "schema版本": 1,
        "开始时间UTC": started_at,
        "完成时间UTC": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "现场来源": provenance,
        "当前阶段裁决": "只读取证已执行；尚待独立复核" if not drift else "阻塞：仓库状态漂移",
        "仓库基线": {
            "允许仓库": REPO,
            "origin_已验证": safe_remote(remote),
            "脚本所在仓库目录": str(ROOT),
            "运行前": before,
            "运行后": after,
            "前后差异": drift,
        },
        "环境基线": system,
        "端口监听_单次快照_不代表应用健康": listeners,
        "数据入口与权限": {
            "状态": "未核验",
            "原因": "当前未提供明确获授权的共享数据入口；本脚本禁止探查旧项目或原始行情路径",
            "生产数据库访问次数": 0,
        },
        "保护范围": [
            "旧 GM/Wyckoff 仓库及其数据：不访问、不修改",
            "生产行情/OI/资金费数据库：不访问、不写入",
            "新项目未知工作区差异：原样保留",
            "采集器、服务、模拟盘：不启动、不重启、不停止",
            "不调用网络接口、不写入文件、不泄漏 origin 凭据",
        ],
        "测试证据状态": {
            "T-G00-01-01": "脚本本次操作的访问边界可核；历史全局变更仍待独立复核",
            "T-G00-01-02": "GitHub 托管环境不能代替本机" if github_hosted else
                           "已有现场命令输出，需确认本机身份和缺项",
            "T-G00-01-03": "检测到仓库状态漂移" if drift else
                           "本次采集前后分支、HEAD、工作区状态未变化",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
