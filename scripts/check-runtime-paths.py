#!/usr/bin/env python3
"""部署前的路径隔离检查：仅访问路径元数据，不读取行情或创建运行目录。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


INPUT_KEYS = ("MARKET_DB", "MARKET_ARCHIVE")
OUTPUT_KEYS = (
    "EVENT_TREND_RUNS_DIR",
    "OI_RESONANCE_RUNS_DIR",
    "FRAME_FIT_REGISTRY",
    "WX_PROVENANCE_HOME",
    "BT_RUN_LOG",
)


def _overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def validate_paths(env: Mapping[str, str], code_root: str | Path) -> list[str]:
    """返回路径错误；空列表表示通过，尚不存在的外部输出目录不会被创建。

    不检查数据库内容、数据覆盖或实际写权限。运行目录的旧批次扫描规则
    来自 backtest_store：同父目录下其他以 runs 开头的目录也会被列入索引。
    """
    errors: list[str] = []
    try:
        code = Path(code_root).resolve(strict=True)
        if not code.is_dir():
            return ["代码根目录不是既有目录。"]
    except (OSError, RuntimeError, ValueError) as exc:
        return [f"代码根目录无法解析：{exc}"]

    raw_paths: dict[str, Path] = {}
    paths: dict[str, Path] = {}
    for key in INPUT_KEYS + OUTPUT_KEYS:
        value = env.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} 必须显式设置为绝对路径。")
            continue
        try:
            path = Path(value)
            if not path.is_absolute():
                errors.append(f"{key} 必须是绝对路径：{value}")
                continue
            # resolve 同时规范化 .. 并解析既有软链接；不创建任何目录。
            resolved = path.resolve(strict=False)
            raw_paths[key], paths[key] = path, resolved
            if _overlap(resolved, code):
                errors.append(f"{key} 解析后与代码树重叠：{resolved}")
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{key} 无法解析：{exc}")

    for key, required_kind in (("MARKET_DB", "文件"), ("MARKET_ARCHIVE", "目录")):
        path = paths.get(key)
        if path is None:
            continue
        try:
            valid = path.is_file() if key == "MARKET_DB" else path.is_dir()
            if not valid:
                errors.append(f"{key} 必须指向既有{required_kind}：{path}")
        except OSError as exc:
            errors.append(f"{key} 路径元数据不可读取：{exc}")

    runs = paths.get("EVENT_TREND_RUNS_DIR")
    oi_runs = paths.get("OI_RESONANCE_RUNS_DIR")
    if runs is not None and oi_runs is not None and runs != oi_runs:
        errors.append("EVENT_TREND_RUNS_DIR 与 OI_RESONANCE_RUNS_DIR 必须解析到同一目录。")

    # 日志文件的父目录才是写入目录；两个 runs 变量代表同一个逻辑目录。
    write_dirs: dict[str, Path] = {}
    for key in OUTPUT_KEYS:
        path = paths.get(key)
        if path is None:
            continue
        directory = path.parent if key == "BT_RUN_LOG" else path
        if key != "OI_RESONANCE_RUNS_DIR":
            write_dirs[key] = directory
        try:
            if key == "BT_RUN_LOG" and path.exists() and not path.is_file():
                errors.append(f"BT_RUN_LOG 不是普通文件路径：{path}")
            ancestor = directory
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                errors.append(f"{key} 的既有父路径不是目录：{ancestor}")
            if directory.exists() and not directory.is_dir():
                errors.append(f"{key} 的写入目录类型错误：{directory}")
        except OSError as exc:
            errors.append(f"{key} 路径元数据不可读取：{exc}")
        if _overlap(directory, code):
            errors.append(f"{key} 的写入目录与代码树重叠：{directory}")

    input_dirs: dict[str, Path] = {}
    if "MARKET_DB" in paths:
        input_dirs["MARKET_DB 父目录"] = paths["MARKET_DB"].parent
    if "MARKET_ARCHIVE" in paths:
        input_dirs["MARKET_ARCHIVE"] = paths["MARKET_ARCHIVE"]
    for key, directory in write_dirs.items():
        for input_key, input_directory in input_dirs.items():
            if _overlap(directory, input_directory):
                errors.append(f"{key} 与市场输入目录 {input_key} 重叠：{directory}")
    items = list(write_dirs.items())
    for index, (first_key, first) in enumerate(items):
        for second_key, second in items[index + 1 :]:
            if _overlap(first, second):
                errors.append(f"写入目录 {first_key} 与 {second_key} 重叠。")

    # 硬链接不会被 resolve 消除；已存在的日志不能与行情库共用同一个文件。
    if "BT_RUN_LOG" in paths and "MARKET_DB" in paths:
        try:
            log_path, db_path = paths["BT_RUN_LOG"], paths["MARKET_DB"]
            if log_path.exists() and db_path.exists() and log_path.samefile(db_path):
                errors.append("BT_RUN_LOG 与 MARKET_DB 是同一个文件（可能为硬链接）。")
        except OSError as exc:
            errors.append(f"无法检查日志与市场库文件身份：{exc}")

    # 同时检查实际环境路径和规范化路径的父目录，覆盖叶级软链接别名。
    contexts: set[tuple[Path, str]] = set()
    for key in ("EVENT_TREND_RUNS_DIR", "OI_RESONANCE_RUNS_DIR"):
        if key not in paths:
            continue
        for candidate in (raw_paths[key], paths[key]):
            try:
                contexts.add((candidate.parent.resolve(strict=False), candidate.name))
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{key} 父目录无法解析：{exc}")
    for parent, current_name in sorted(contexts, key=lambda item: (str(item[0]), item[1])):
        try:
            if not parent.exists():
                continue
            for sibling in parent.iterdir():
                if sibling.name != current_name and sibling.name.startswith("runs") and sibling.is_dir():
                    errors.append(f"运行目录同级存在其他 runs* 目录，会被旧批次扫描纳入索引：{sibling}")
                    break
        except OSError as exc:
            errors.append(f"无法检查运行目录同级扫描范围 {parent}：{exc}")
    return errors


def main() -> int:
    errors = validate_paths(os.environ, Path(__file__).resolve().parents[1])
    if errors:
        print("路径隔离检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("路径隔离检查通过：仅核验路径元数据；未启动服务、未读取行情、未创建运行目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
