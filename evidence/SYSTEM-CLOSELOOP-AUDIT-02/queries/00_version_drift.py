# -*- coding: utf-8 -*-
"""00 版本漂移探针 —— 判断「能不能开始正式验收」的唯一机械依据。

工单要求：
  「收到反重力版本冻结回执后，再对该固定版本执行正式验收；**不对持续变动的版本拼接通过项**。」
  「**版本一致**：检查验收开始、结束及实际运行进程的版本；**发生变动时作废受影响结果并重新核验**。」

四条判据，全部机械可复算：

  D0  存在「版本冻结回执」实体文件（对方书面声明冻结的那份东西）
  D1  同一份文件集，在 T0 与 T1 两次采样之间 **零变化**
  D2  采样时点之后没有新写入（所有被审文件 mtime <= T1 采样时刻）
  D3  运行中的后端进程，其启动时刻 **晚于** 最后一次代码变更（否则跑的是旧代码）

**只有 D0 ∧ D1 ∧ D2 ∧ D3 全为真，才算版本已冻结。**

第一版把「D3 解析不出」当成「不算失败」，等于让一个不可验证的项偷偷变成通过 ——
这正是工单禁止的「把未验证计入通过」。已改为：D3 只能是 True 才算过，
None（不可判定）一律按未通过处理，并在报告里写「未验证」。

用法：
  python 00_version_drift.py                    # 采 T0，等 120s，采 T1，判
  python 00_version_drift.py --interval 600     # 拉长观察窗
  python 00_version_drift.py --snapshot         # 只采一次，落基线
  python 00_version_drift.py --from-snapshots   # 复用已落盘的 T0/T1 反复复算

产物：out/00_版本快照_T0.json、out/00_版本快照_T1.json、out/00_漂移判定.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
DELIVERY = BASE / "evidence/SYSTEM-CLOSELOOP-02"   # 反重力交付目录

PORT = 8787


def audited_files() -> list[pathlib.Path]:
    """被审文件集：不是只看上一轮那 29 个，而是后端全部 py + 前端全部 src + 配置 + 闸门。
    避免「对方改了清单外的文件」这种情况漏掉。"""
    fs: list[pathlib.Path] = []
    fs += [p for p in (BASE / "框架/backend").rglob("*.py")
           if "__pycache__" not in p.parts and "_legacy_archive" not in p.parts]
    fs += [p for p in (BASE / "UI界面/web/src").rglob("*")
           if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".json")]
    for extra in ("UI界面/web/vite.config.ts", "UI界面/web/package.json",
                  "框架/backend/live/paper_new_entry_gate.json"):
        p = BASE / extra
        if p.exists():
            fs.append(p)
    return sorted(set(fs))


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sample() -> dict:
    now = dt.datetime.now().astimezone()
    rec = {}
    for p in audited_files():
        st = p.stat()
        rec[p.relative_to(BASE).as_posix()] = {
            "sha256": sha(p),
            "字节": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "mtime_ms": int(st.st_mtime * 1000),
        }
    return {"采样时刻": now.isoformat(timespec="seconds"),
            "采样时刻_ms": int(now.timestamp() * 1000),
            "文件数": len(rec), "文件": rec}


# ---------------------------------------------------------------- D0 冻结回执
def freeze_receipt() -> dict:
    """D0：反重力是否交付了「版本冻结回执」这个实体。
    只认文件名/内容里明确声明冻结的，不认「我改完了」这种口头。"""
    hits = []
    if DELIVERY.exists():
        for p in DELIVERY.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            if any(k in name for k in ("冻结", "freeze", "FROZEN")):
                hits.append({"文件": str(p.relative_to(BASE)), "字节": p.stat().st_size,
                             "依据": "文件名含冻结关键词"})
                continue
            if p.suffix in (".md", ".txt", ".json"):
                try:
                    t = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:                                # noqa: BLE001
                    continue
                if ("版本冻结" in t or "FROZEN" in t or "freeze" in t.lower()) and \
                        ("回执" in t or "声明" in t or "sha" in t.lower()):
                    hits.append({"文件": str(p.relative_to(BASE)), "字节": p.stat().st_size,
                                 "依据": "内容含冻结声明"})
    return {"交付目录": str(DELIVERY.relative_to(BASE)),
            "目录存在": DELIVERY.exists(),
            "找到的冻结回执": hits,
            "D0_存在冻结回执": bool(hits)}


# ---------------------------------------------------------------- D3 运行进程
def running_backend() -> dict:
    """找正在监听 8787 的进程，并读它的启动时刻与命令行。
    ps 在本沙箱被拒（operation not permitted），改用 lsof 拿 PID + psutil 拿启动时刻。"""
    pid = None
    via = None
    try:
        import subprocess
        r = subprocess.run(["lsof", "-nP", f"-i:{PORT}", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                pid = int(parts[1])
                via = "lsof"
                break
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "原因": f"lsof 失败: {exc}"}
    if pid is None:
        return {"可判定": False, "原因": f"端口 {PORT} 上没有监听进程（服务没起）"}
    try:
        import psutil
        pr = psutil.Process(pid)
        started = dt.datetime.fromtimestamp(pr.create_time()).astimezone()
        return {"可判定": True, "pid": pid, "来源": via,
                "启动时刻": started.isoformat(timespec="seconds"),
                "启动时刻_ms": int(started.timestamp() * 1000),
                "命令行": " ".join(pr.cmdline())[:300],
                "工作目录": pr.cwd()}
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "pid": pid, "来源": via,
                "原因": f"拿到 pid 但读不出启动时刻: {type(exc).__name__}: {exc}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=120, help="两次采样间隔秒数")
    ap.add_argument("--snapshot", action="store_true", help="只采一次，落基线快照")
    ap.add_argument("--from-snapshots", action="store_true",
                    help="不重新采样，直接用已落盘的 T0/T1 快照做判定（可反复复算）")
    a = ap.parse_args()

    if a.from_snapshots:
        t0 = json.loads((OUT / "00_版本快照_T0.json").read_text(encoding="utf-8"))
        t1 = json.loads((OUT / "00_版本快照_T1.json").read_text(encoding="utf-8"))
        print(f"[复用] T0={t0['采样时刻']}  T1={t1['采样时刻']}  {t1['文件数']} 个文件")
    else:
        print(f"[T0] 采样中（{len(audited_files())} 个文件）...")
        t0 = sample()
        (OUT / "00_版本快照_T0.json").write_text(
            json.dumps(t0, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[T0] {t0['采样时刻']}  {t0['文件数']} 个文件")
        if a.snapshot:
            print("已落基线快照（--snapshot 模式，不判漂移）")
            return
        print(f"[等待] {a.interval} 秒后采 T1 ...")
        time.sleep(a.interval)
        print("[T1] 采样中 ...")
        t1 = sample()
        (OUT / "00_版本快照_T1.json").write_text(
            json.dumps(t1, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[T1] {t1['采样时刻']}  {t1['文件数']} 个文件")

    a0, a1 = t0["文件"], t1["文件"]
    moved = []
    for k in sorted(set(a0) | set(a1)):
        x, y = a0.get(k), a1.get(k)
        if x is None:
            moved.append({"文件": k, "变化": "T1 新增"})
        elif y is None:
            moved.append({"文件": k, "变化": "T1 消失"})
        elif x["sha256"] != y["sha256"]:
            moved.append({"文件": k, "变化": "间隔内被改",
                          "T0_mtime": x["mtime"], "T1_mtime": y["mtime"],
                          "T0_sha": x["sha256"][:12], "T1_sha": y["sha256"][:12],
                          "字节": f'{x["字节"]} → {y["字节"]}'})

    d1 = len(moved) == 0

    latest_rel, latest = max(a1.items(), key=lambda kv: kv[1]["mtime_ms"])
    d2 = not any(v["mtime_ms"] > t1["采样时刻_ms"] for v in a1.values())

    # D0
    rec = freeze_receipt()
    d0 = rec["D0_存在冻结回执"]

    # D3
    proc = running_backend()
    d3 = None
    if proc.get("可判定"):
        latest_dt = dt.datetime.fromtimestamp(latest["mtime_ms"] / 1000).astimezone()
        started = dt.datetime.fromtimestamp(proc["启动时刻_ms"] / 1000).astimezone()
        d3 = proc["启动时刻_ms"] >= latest["mtime_ms"]
        d3_note = (f"进程启动 {started.isoformat(timespec='seconds')} vs "
                   f"最后代码变更 {latest_dt.isoformat(timespec='seconds')}（{latest_rel}）"
                   + ("⇒ 跑的是最新代码" if d3 else "⇒ **进程比代码旧，跑的是旧代码**"))
    else:
        d3_note = f"不可判定：{proc.get('原因')} ⇒ 按未验证处理，**不计入通过**"

    # 观察窗长度与「最后一次改动距今多久」——这两个数决定 D1 的说服力
    t1_dt = dt.datetime.fromtimestamp(t1["采样时刻_ms"] / 1000).astimezone()
    quiet_since = (t1_dt - dt.datetime.fromtimestamp(latest["mtime_ms"] / 1000).astimezone()).total_seconds()
    window = (t1["采样时刻_ms"] - t0["采样时刻_ms"]) / 1000

    frozen = bool(d0 and d1 and d2 and (d3 is True))
    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "采样间隔_s": window,
        "T0": t0["采样时刻"], "T1": t1["采样时刻"],
        "文件数": len(a1),
        "D0_存在冻结回执": d0,
        "D0_回执明细": rec,
        "D1_间隔内零变化": d1,
        "D1_变动文件": moved,
        "D2_采样后无新写入": d2,
        "D2_最晚变更": {"文件": latest_rel, "mtime": latest["mtime"]},
        "D3_进程跑最新代码": d3,
        "D3_说明": d3_note,
        "D3_进程": proc,
        "观察窗_s": window,
        "最后一次改动距T1_s": quiet_since,
        "结论_版本已冻结": frozen,
        "结论_含义": ("四条判据全过 ⇒ 可开始正式验收" if frozen else
                     "至少一条不过 ⇒ **不得开始正式验收**，"
                     "也不得把本轮任何结果拼进最终结论"),
        "缺口": [n for n, ok in (("D0 无冻结回执", d0), ("D1 间隔内有改动", d1),
                                 ("D2 采样后有新写入", d2), ("D3 进程版本不可判定或偏旧", d3 is True))
                 if not ok],
    }
    (OUT / "00_漂移判定.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 漂移判定 ===")
    print(f"D0 存在冻结回执      : {d0}   （{len(rec['找到的冻结回执'])} 个命中）")
    print(f"D1 间隔内零变化      : {d1}   （窗口 {window:.0f}s，变动 {len(moved)} 个）")
    for m in moved[:20]:
        print(f"     - {m['文件']}  {m['变化']}  {m.get('T0_mtime','')} → {m.get('T1_mtime','')}")
    print(f"D2 采样后无新写入    : {d2}   最晚变更 {latest['mtime']}  {latest_rel}"
          f"（距 T1 {quiet_since:.0f}s）")
    print(f"D3 进程跑最新代码    : {d3}   {d3_note}")
    print(f"\n>>> 版本已冻结 = {frozen}")
    if res["缺口"]:
        print(">>> 缺口：" + "；".join(res["缺口"]))
    print(f">>> {res['结论_含义']}")


if __name__ == "__main__":
    main()
