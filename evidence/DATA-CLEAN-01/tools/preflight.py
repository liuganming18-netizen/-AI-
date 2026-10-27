#!/usr/bin/env python3
"""DATA-CLEAN-01 分批删除执行器（移入废纸篓，可恢复）。

用法:
  preflight.py <批次名>          # 只核对，不动
  preflight.py <批次名> --go     # 核对通过后移入废纸篓

安全设计：
- 核对任一项不通过 ⇒ 立即停止，不执行
- 只接受脚本内硬编码的绝对路径，不接受外部传参路径
- 移动到 ~/.Trash/DATA-CLEAN-01_<时间戳>/ 下，保留原名
- 不做递归删除，只用 shutil.move
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BASE = os.path.join(ROOT, "数据/研究输入/WyckoffMaster")
TRASH_ROOT = os.path.expanduser("~/.Trash")

BATCHES = {
    "第一批": {
        "src": os.path.join(BASE, "_backup/20261014T192054Z"),
        "expect_files": 3155,
        "expect_bytes": 9463433746,
        "keep": [os.path.join(BASE, "funding/BTCUSDT.parquet"),
                 os.path.join(BASE, "ohlcv/1h/BTCUSDT.parquet"),
                 os.path.join(BASE, "oi_metrics/BTCUSDT.parquet"),
                 os.path.join(BASE, "mark1h/BTCUSDT.parquet")],
        "note": "与 live 逐字节相同的第二份快照",
    },
    "第二批": {
        "src": os.path.join(BASE, "ohlcv"),
        "expect_files": 1578,
        "expect_bytes": 3364587820,
        "keep": [os.path.join(BASE, "funding/BTCUSDT.parquet"),
                 os.path.join(BASE, "oi_metrics/BTCUSDT.parquet"),
                 os.path.join(BASE, "mark1h/BTCUSDT.parquet"),
                 os.path.join(BASE, "_backup/20261014T190050Z/manifest.json")],
        "note": "OHLCV 研究输入，可从 parquet + SQLite 重建",
    },
    "第三批": {
        "src": os.path.join(BASE, "oi_metrics"),
        "expect_files": 526,
        "expect_bytes": 5904213533,
        "keep": [os.path.join(BASE, "funding/BTCUSDT.parquet"),
                 os.path.join(BASE, "mark1h/BTCUSDT.parquet"),
                 os.path.join(BASE, "_backup/20261014T190050Z/manifest.json"),
                 os.path.join(ROOT, "数据/市场数据/parquet/metrics/binance/um/BTCUSDT/2026.parquet"),
                 os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um/BTCUSDT/1h/2026.parquet")],
        "note": "持仓量+多空比指标，仓储层有同字段且更准",
    },
}


def du_bytes(path):
    total = 0
    n = 0
    for dp, _, fns in os.walk(path):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.lstat(p)
                total += st.st_size
                n += 1
            except OSError:
                pass
    return n, total


def recent_write(path, minutes=10):
    cutoff = time.time() - minutes * 60
    for dp, _, fns in os.walk(path):
        for fn in fns:
            try:
                if os.lstat(os.path.join(dp, fn)).st_mtime > cutoff:
                    return True
            except OSError:
                pass
    return False


def lsof_hits(path):
    try:
        out = subprocess.run(["lsof", "-nP", "+D", path], capture_output=True, text=True, timeout=120)
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        return max(0, len(lines) - 1)
    except Exception:
        return -1


def df_avail_gb():
    out = subprocess.check_output(["df", "-k", "/Users/boos"], text=True).splitlines()
    kb = int(out[-1].split()[3])
    return round(kb / 1024 / 1024, 2)


def readable(p):
    try:
        if not os.path.exists(p):
            return False
        with open(p, "rb") as f:
            f.read(4096)
        return True
    except Exception:
        return False


def main():
    name = sys.argv[1]
    go = "--go" in sys.argv
    cfg = BATCHES[name]
    src = cfg["src"]

    print(f"=== {name} 前置核对 ===")
    print(f"目标: {src}")
    checks = []

    exists = os.path.isdir(src)
    checks.append(("路径存在且为目录", exists, src))
    if not exists:
        return report(checks, name, go)

    n, b = du_bytes(src)
    checks.append((f"文件数 = {cfg['expect_files']}", n == cfg["expect_files"], f"实测 {n}"))
    checks.append((f"总字节 = {cfg['expect_bytes']:,}", b == cfg["expect_bytes"], f"实测 {b:,}"))

    for k in cfg["keep"]:
        ok = readable(k)
        checks.append((f"保留副本可读 {os.path.relpath(k, ROOT)}", ok, ""))

    hits = lsof_hits(src)
    checks.append(("无进程占用", hits == 0, f"lsof 命中 {hits} 条"))

    rw = recent_write(src, 10)
    checks.append(("近 10 分钟无写入", not rw, ""))

    avail = df_avail_gb()
    checks.append(("可用空间 ≥ 12 GiB", avail >= 12, f"{avail} GiB"))

    ok_all = all(c[1] for c in checks)
    for desc, ok, detail in checks:
        print(f"  [{'通过' if ok else '不通过'}] {desc}  {detail}")

    if not ok_all:
        print("\n❌ 存在不通过项，本批停止，未做任何改动。")
        return 1

    if not go:
        print("\n✅ 核对全部通过（未执行）。加 --go 才会移动。")
        return 0

    # 执行
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest_dir = os.path.join(TRASH_ROOT, f"DATA-CLEAN-01_{ts}")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.exists(dest):
        print(f"❌ 废纸篓中已存在同名目标: {dest}")
        return 1

    before = df_avail_gb()
    t0 = time.time()
    shutil.move(src, dest)
    dt = time.time() - t0
    after = df_avail_gb()

    src_gone = not os.path.exists(src)
    dest_ok = os.path.isdir(dest)
    n2, b2 = du_bytes(dest) if dest_ok else (0, 0)

    print(f"\n=== 执行结果 ===")
    print(f"  原路径已不存在: {src_gone}")
    print(f"  废纸篓路径: {dest}")
    print(f"  废纸篓内文件数: {n2}（移动前 {n}）")
    print(f"  废纸篓内字节: {b2:,}（移动前 {b:,}）")
    print(f"  耗时: {dt:.2f} 秒")
    print(f"  磁盘可用 前 {before} GiB → 后 {after} GiB")

    rec = {
        "batch": name, "src": src, "dest": dest,
        "files_before": n, "bytes_before": b,
        "files_after": n2, "bytes_after": b2,
        "src_gone": src_gone, "dest_exists": dest_ok,
        "seconds": round(dt, 2),
        "avail_before_gb": before, "avail_after_gb": after,
        "at": datetime.now().isoformat(),
    }
    logp = os.path.join(ROOT, "evidence/DATA-CLEAN-01/execution_log.jsonl")
    with open(logp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n已记录到 {logp}")
    return 0 if (src_gone and dest_ok and n2 == n) else 2


def report(checks, name, go):
    for desc, ok, detail in checks:
        print(f"  [{'通过' if ok else '不通过'}] {desc}  {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
