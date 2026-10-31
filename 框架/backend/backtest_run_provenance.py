# -*- coding: utf-8 -*-
"""回测运行溯源 · 可信声明与执行记录的可验证绑定（GM-BACKTEST-TRUSTED-RUN-PROVENANCE-FIX-46）。

要解决的问题（43 号审计实测）
────────────────────────────
`run.json` 是**普通可写 JSON**：门禁结论 `data_gate` 可以整块从 A 运行复制到 B 运行，
21 个键逐字段相同、`input_sha256` 也不会失配（没有代码重算它）；`claim_level` 手写即生效；
`pit_status` 手写 `PIT_PROVEN` 即生效。⇒ 43 号结论：**`TRUSTED` 与它所附着的 run
之间没有绑定关系。不是「签名弱」，是「没有签名」。**

本模块建立三层绑定
──────────────────
1. **执行实例（seal）**：`create_run` 时生成 `exec_instance_id`，并把
   `run_id + exec_instance_id + 规格摘要 + 创建时刻` 写进**哈希链账本**（`SEAL` 条目）。
   ⇒ 「这个 run 在结果产生之前就存在」有了时间锚。
2. **门禁回执（gate_receipt）**：门禁**执行时刻**由执行方调用
   `issue_gate_receipt()` 产出，绑定 `run_id + exec_instance_id + 门禁四元组 +
   快照摘要 + 判定摘要 + claim_level + pit_status`，并 HMAC 签名。
   ⇒ 「这份门禁结论是给这个 run 算的」不再靠字段长得像。
3. **终态背书（attest）**：`set_status` 进入终态时，对 `run.json` 的**可信投影**
   （白名单字段）与**结果摘要**做 HMAC 背书，并写 `ATTEST` 链条目。
   ⇒ 「事后改收益 / 改配置 / 删门禁字段」都会让投影摘要失配。

信任边界（必须如实披露，不得宣称密码学级防伪）
──────────────────────────────────────────────
· 信任锚是一把 **HMAC 密钥**，默认放在 `~/.wx_provenance/key`（0600），可用
  `WX_PROVENANCE_HOME` 改目录。它**在结果目录之外** ⇒ 只有 runs 目录写权限的人
  无法伪造回执/背书（这是本模块相对「手写 JSON」的实质提升）。
· 但它是**本机文件**：能读该密钥的同一 OS 用户，仍然可以伪造一切。本模块**不能**
  防住这类对手，也**不声称**能防。它防的是：复制字段、改名重登记、手写 claim、
  追加伪造证明、事后改结果 —— 即 43 号实测的那 8 类操作。
· 密钥丢失/轮换 ⇒ 所有既有背书降级为 `UNVERIFIED`（fail-closed，不猜）。
· 链账本是**追加式**的，但仍是本机文件：删掉整个账本 + 删掉密钥 ⇒ 全部 UNVERIFIED，
  不会变成「可信」。**降级永远往 UNVERIFIED 走，不往 TRUSTED 走。**

三态（读取方唯一可用判据）
──────────────────────────
· `VERIFIED_TRUSTED`：背书有效 + 链完好 + 门禁回执有效且绑定本 run + 三条件同时成立
  （`claim_level==TRUSTED` ∧ `verdict==PASS` ∧ `pit_status==PIT_PROVEN`）。
· `UNVERIFIED`：没有可信主张，或证据不足以支持可信主张（历史记录默认落这里）。
· `REJECTED`：**声称可信但证明失败**（复制、篡改、伪造、无回执、链断、密钥不符）。
  `REJECTED` 不是「不可信」，是「有伪造/篡改迹象」——必须与 UNVERIFIED 分开呈现。

纪律
────
· **不修改** `backtest_runner.py` / `backtest_data_gate.py` / `backtest_gate_wiring.py`。
  本模块只被 `backtest_store.py`（登记与读取校验模块）调用。
· 背书失败**不得**影响回测本身：`seal` / `attest` 的所有异常都在调用方被吞成
  `provenance.seal_error` / `provenance.attest_error`，绝不抛给 runner。
· **默认 UNVERIFIED**：任何缺证据的路径都不许写 `TRUSTED`。
· 只读上游：本模块不改门禁、不改 runner、不写生产账本；写盘只发生在
  ①结果目录内的 `run.json`（由 store 写）②信任锚目录（key + chain）。
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:                                                         # pragma: no cover
    import fcntl
except ImportError:                                          # pragma: no cover
    fcntl = None                                             # type: ignore[assignment]

SCHEMA = "wx.run_provenance.v1"
RECEIPT_SCHEMA = "wx.gate_receipt.v1"

STATE_VERIFIED_TRUSTED = "VERIFIED_TRUSTED"
STATE_UNVERIFIED = "UNVERIFIED"
STATE_REJECTED = "REJECTED"
STATES = (STATE_VERIFIED_TRUSTED, STATE_UNVERIFIED, STATE_REJECTED)

CLAIM_TRUSTED = "TRUSTED"
CLAIM_ISOLATED = "ISOLATED_RESEARCH"
PIT_PROVEN = "PIT_PROVEN"

VERDICT_PASS = "PASS"

# ── 信任锚位置 ───────────────────────────────────────────────────────────────
HOME_ENV = "WX_PROVENANCE_HOME"
KEY_FILENAME = "key"
CHAIN_FILENAME = "chain.jsonl"
LOCK_FILENAME = "chain.lock"
LOCK_TIMEOUT_ENV = "WX_PROVENANCE_LOCK_TIMEOUT_SEC"
DEFAULT_LOCK_TIMEOUT_SEC = 30.0
_GENESIS = "0" * 64

# ── 可信投影：只有这些字段进入背书摘要 ───────────────────────────────────────
# 为什么是白名单而不是黑名单：黑名单会随 runner 加字段而悄悄失效（新字段不受保护），
# 白名单则会**主动**把新字段排除在外 —— 排除是可见的（摘要不变 ⇒ 改了也没人管），
# 所以白名单必须配合「字段清单哈希」一起校验：清单本身也进摘要。
ATTESTED_FIELDS: Tuple[str, ...] = (
    "run_id",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "spec",
    "config",
    "window",
    "hashes",
    "data_quality",
    "metrics",
    "engine",
    "data_gate",
    "pit_status",
    "execution_instance",
)
# 明确**不**进摘要的字段（写进文档，避免以后有人以为是漏了）：
#   updated_at        每次写都变，进摘要等于背书立刻失效
#   provenance        背书本体
#   experiment_record 在 set_status 之后由 runner 追加（`_record_experiment_outcome`）
#   traceback/failure_reason 失败路径的自由文本，长度与内容不稳定
#   summary/chart     大块展示载荷，与「结果是否可信」无关
VOLATILE_FIELDS: Tuple[str, ...] = (
    "updated_at", "provenance", "experiment_record", "traceback",
    "failure_reason", "summary", "chart", "note",
)

_CHAIN_LOCK = threading.Lock()
_CHAIN_CACHE_LOCK = threading.Lock()
_CHAIN_CACHE: Dict[str, Any] = {"key": None, "recs": None}
_IMPORT_MS = int(time.time() * 1000)


# ══════════════════════════ 规范化 / 摘要 ══════════════════════════

def canon(obj: Any) -> bytes:
    """与门禁 `backtest_data_gate._canon` **逐字节一致**的规范化。

    必须一致：回执要拿 `snapshot_digest` 去比 `data_gate.input_sha256`，
    而后者是门禁用它自己的 `_canon` 算的。两套规范化不同 ⇒ 恒失配。
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def digest(obj: Any) -> str:
    """对象 → 64 位十六进制 SHA256（无前缀，与门禁 `_sha` 同口径）。"""
    return hashlib.sha256(canon(obj)).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ms() -> int:
    return int(time.time() * 1000)


# ══════════════════════════ 信任锚：密钥 ══════════════════════════

class KeyUnavailable(RuntimeError):
    """拿不到 HMAC 密钥 ⇒ 无法签名/验签（fail-closed，不降级为「信任」）。"""


class NoSeal(RuntimeError):
    """没有 SEAL 条目 ⇒ 该 run 不是在 `create_run` 路径上产生的，拒绝背书。"""


class ChainCorrupt(RuntimeError):
    """链已损坏或链尾不完整 ⇒ 拒绝追加，绝不静默重建或清空。"""


class ChainLockTimeout(RuntimeError):
    """等待跨进程链锁超时 ⇒ 拒绝追加，避免无界阻塞。"""


def home() -> Path:
    """信任锚目录（结果目录之外）。"""
    return Path(os.environ.get(HOME_ENV) or (Path.home() / ".wx_provenance"))


def key_path() -> Path:
    return home() / KEY_FILENAME


def chain_path() -> Path:
    return home() / CHAIN_FILENAME


def lock_path() -> Path:
    """跨进程锁文件。与链文件分离，避免锁操作改变链内容或哈希。"""
    return home() / LOCK_FILENAME


def _lock_timeout_seconds() -> float:
    raw = os.environ.get(LOCK_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_LOCK_TIMEOUT_SEC
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_LOCK_TIMEOUT_SEC


def load_key(*, create: bool = False) -> Optional[bytes]:
    """读密钥。`create=False` 时缺失返回 `None`（**不创建**，避免只读校验产生副作用）。"""
    p = key_path()
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if len(raw) < 32:
            return None
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return None
    if not create:
        return None
    d = home()
    d.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return load_key(create=False)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key.hex())
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


def key_fingerprint(key: Optional[bytes]) -> Optional[str]:
    """密钥指纹（不泄露密钥本体）。用于识别「换了一把密钥重新签名」。"""
    if not key:
        return None
    return hashlib.sha256(key).hexdigest()[:16]


def _mac(key: bytes, obj: Any) -> str:
    return hmac.new(key, canon(obj), hashlib.sha256).hexdigest()


# ══════════════════════════ 追加式哈希链 ══════════════════════════

def _tail_of_records(recs: List[Dict[str, Any]]) -> Tuple[int, str]:
    """(最后 seq, 最后记录摘要)。调用方必须已通过整链校验。"""
    if not recs:
        return 0, _GENESIS
    last = recs[-1]
    body = {k: v for k, v in last.items() if k != "mac"}
    return int(last.get("seq") or 0), digest(body)


def read_chain() -> List[Dict[str, Any]]:
    p = chain_path()
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:                                # noqa: BLE001
                out.append({"_corrupt": line[:200]})
    return out


def _chain_key() -> Tuple[str, int, int]:
    """缓存键：路径 + 字节数 + mtime(ns)。任何一个变了就重读。"""
    p = chain_path()
    try:
        st = p.stat()
        return (str(p), int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return (str(p), -1, -1)


def chain_snapshot() -> List[Dict[str, Any]]:
    """链记录（带缓存）。

    为什么必须有缓存：`verify_run()` 每次都要看链，而读取层一旦接上
    （`list_runs` 里有上千个 run），无缓存就是「每个 run 重读整条链」= O(n²)。
    缓存键用「大小 + mtime」而不是时间 TTL：追加必然改变大小，能保证不漏读。
    """
    k = _chain_key()
    with _CHAIN_CACHE_LOCK:
        if _CHAIN_CACHE.get("key") == k and _CHAIN_CACHE.get("recs") is not None:
            return _CHAIN_CACHE["recs"]                      # type: ignore[return-value]
    recs = read_chain()
    with _CHAIN_CACHE_LOCK:
        _CHAIN_CACHE.update({"key": k, "recs": recs})
    return recs


def append_chain(kind: str, *, run_id: str, exec_instance_id: Optional[str],
                 payload_digest: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """追加一条链记录（HMAC 签名 + prev 链接）。**没有密钥就抛** `KeyUnavailable`。

    完整事务在跨进程文件锁内执行：读链 → 全链验签 → 分配 seq → 追加 → fsync。
    链已损坏或链尾不是完整换行结尾时抛 `ChainCorrupt`，不静默重建、不截断、不续写。
    重复调用不会复用旧序号：每次成功调用都得到新的唯一 seq。
    """
    with _CHAIN_LOCK:
        with _chain_transaction():
            key = load_key(create=True)
            if key is None:
                raise KeyUnavailable(f"无法取得溯源密钥：{key_path()}")
            p = chain_path()
            raw = b""
            if p.exists():
                raw = p.read_bytes()
                if raw and not raw.endswith(b"\n"):
                    raise ChainCorrupt(
                        "链尾不是完整换行结尾（疑似进程异常终止造成部分写入）；"
                        "拒绝追加，保留原链等待人工核验")
            recs = read_chain()
            checked = _verify_records(recs, key)
            if not checked["ok"]:
                raise ChainCorrupt(
                    f"证明链在第 {checked['broken_seq']} 条损坏（{checked['reason']}）；"
                    "拒绝追加，绝不静默重建或清空")
            seq, prev = _tail_of_records(recs)
            rec: Dict[str, Any] = {
                "seq": seq + 1,
                "prev": prev,
                "kind": kind,
                "subject": {"run_id": run_id, "exec_instance_id": exec_instance_id},
                "payload_digest": payload_digest,
                "ts_ms": now_ms(),
                "key_fp": key_fingerprint(key),
            }
            if extra:
                rec["extra"] = extra
            rec["mac"] = _mac(key, rec)
            data = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("证明链追加写入返回 0 字节")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return rec


def _verify_records(recs: List[Dict[str, Any]], key: Optional[bytes]) -> Dict[str, Any]:
    """对已读取的记录做逐条验签、prev 链接与 seq 连续性校验。"""
    if not recs:
        return {"ok": True, "entries": 0, "broken_seq": None, "reason": None}
    if key is None:
        return {"ok": False, "entries": len(recs), "broken_seq": None,
                "reason": "KEY_UNAVAILABLE"}
    prev = _GENESIS
    for i, rec in enumerate(recs, start=1):
        if rec.get("_corrupt") is not None:
            return {"ok": False, "entries": len(recs), "broken_seq": i, "reason": "CORRUPT_LINE"}
        if int(rec.get("seq") or 0) != i:
            return {"ok": False, "entries": len(recs), "broken_seq": i, "reason": "SEQ_GAP"}
        if str(rec.get("prev")) != prev:
            return {"ok": False, "entries": len(recs), "broken_seq": i, "reason": "PREV_MISMATCH"}
        body = {k: v for k, v in rec.items() if k != "mac"}
        if not hmac.compare_digest(str(rec.get("mac") or ""), _mac(key, body)):
            return {"ok": False, "entries": len(recs), "broken_seq": i, "reason": "BAD_MAC"}
        prev = digest(body)
    return {"ok": True, "entries": len(recs), "broken_seq": None, "reason": None}


def verify_chain(*, key: Optional[bytes] = None) -> Dict[str, Any]:
    """整链校验：逐条验签 + prev 链接 + seq 连续。返回首个断点。"""
    key = key if key is not None else load_key()
    return _verify_records(chain_snapshot(), key)


@contextmanager
def _chain_transaction():
    """跨进程独占事务：锁生命周期覆盖读链、验链、分配序号、追加与落盘。

    线程锁负责同进程线程；文件锁负责不同进程。两者都保留。
    非 POSIX 环境或取不到 `fcntl` 时 fail-closed，不退回「只用线程锁」。
    """
    if fcntl is None:                                        # pragma: no cover
        raise ChainLockTimeout("当前平台不支持 fcntl.flock，拒绝无跨进程保护的追加")
    d = home()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path()), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    deadline = time.monotonic() + _lock_timeout_seconds()
    try:
        while not acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise ChainLockTimeout(
                        f"等待证明链锁超时：{lock_path()}（{_lock_timeout_seconds():g} 秒）")
                time.sleep(0.01)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:                                  # pragma: no cover
                pass
        os.close(fd)


def find_entry(kind: str, run_id: str, exec_instance_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """按 `kind + subject` 找链条目，**返回最后一条**（同一 run 可多次背书，以最新为准）。

    注意：调用方必须先跑 `verify_chain()`；这里只做检索，不做信任判定。
    """
    hit = None
    for rec in chain_snapshot():
        if rec.get("_corrupt") is not None:
            continue
        if rec.get("kind") != kind:
            continue
        subj = rec.get("subject") or {}
        if subj.get("run_id") == run_id and subj.get("exec_instance_id") == exec_instance_id:
            hit = rec
    return hit


# ══════════════════════════ 执行实例（seal） ══════════════════════════

def new_exec_instance_id() -> str:
    """执行实例标识：`xi_` + 32 位十六进制（128 bit 随机）。"""
    return "xi_" + secrets.token_hex(16)


def spec_digest_of(doc: Dict[str, Any]) -> str:
    """规格摘要：`spec` + `config` 的规范化摘要（执行配置绑定的锚）。"""
    return digest({"spec": doc.get("spec"), "config": doc.get("config")})


def seal_run(run_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """在 `create_run` 里调用：生成执行实例并把 SEAL 写进链。

    失败**不抛**（回测不能因为溯源不可用就跑不起来），而是把失败写进返回值。
    """
    xi = new_exec_instance_id()
    ms = now_ms()
    block: Dict[str, Any] = {
        "exec_instance_id": xi,
        "sealed_at_ms": ms,
        "sealed_at_utc": utc_now_iso(),
        "spec_digest": spec_digest_of(doc),
        "created_at": doc.get("created_at"),
        "seal_ok": False,
        "seal_error": None,
    }
    payload = {
        "run_id": run_id,
        "exec_instance_id": xi,
        "spec_digest": block["spec_digest"],
        "created_at": block["created_at"],
        "sealed_at_ms": ms,
    }
    try:
        rec = append_chain("SEAL", run_id=run_id, exec_instance_id=xi,
                           payload_digest=digest(payload))
        block["seal_ok"] = True
        block["seal_seq"] = rec["seq"]
        block["key_fp"] = rec.get("key_fp")
    except Exception as exc:                                 # noqa: BLE001
        block["seal_error"] = f"{type(exc).__name__}: {exc}"
    return block


# ══════════════════════════ 门禁回执（45 号接口） ══════════════════════════

def gate_file_sha256() -> Optional[str]:
    """门禁本体文件 SHA256（四元组第 4 项）。缺失返回 None（不猜）。"""
    p = Path(__file__).resolve().parent / "backtest_data_gate.py"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def gate_verdict_digest(gate_block: Dict[str, Any]) -> str:
    """对 `run.json` 里的 `data_gate` 块整体取摘要。

    为什么是整块而不是只取 `verdict`：43 号 E5b 证明「照抄键全集」可以造出
    21 键相同的假件 —— 只摘要 `verdict` 更挡不住。整块摘要 + HMAC 才能挡。
    """
    return digest(gate_block or {})


def issue_gate_receipt(*, run_id: str, gate_block: Dict[str, Any],
                       exec_instance_id: Optional[str] = None,
                       pit_status: Optional[str] = None,
                       gate_version: Optional[str] = None,
                       severity_policy_version: Optional[str] = None,
                       issuer_note: Optional[str] = None) -> Dict[str, Any]:
    """**门禁执行时刻**调用：为「这份门禁结论是给这个 run 算的」出回执。

    这是给 45 号（门禁接线方）的**参考实现**，接口契约见
    `evidence/GM-BACKTEST-TRUSTED-RUN-PROVENANCE-FIX-46/CONTRACT.md`。
    本函数不改门禁、不改 runner；45 号只需在拿到 `decision` 之后调一次。

    绑定项（少一项就不成立）：
      run_id · exec_instance_id · gate 四元组 · snapshot_digest · verdict_digest ·
      claim_level · pit_status
    """
    key = load_key(create=True)
    if key is None:
        raise KeyUnavailable(f"无法取得溯源密钥：{key_path()}")
    if exec_instance_id is None:
        exec_instance_id = _exec_instance_of_run(run_id)
    if not exec_instance_id:
        raise NoSeal(f"run 无 execution_instance，拒绝出回执：{run_id}")

    gb = gate_block or {}
    aux_pv = ((gb.get("aux") or {}).get("policy_version"))
    severe_blk = gb.get("severe_blocked")
    pit_st = pit_status if pit_status is not None else gb.get("pit_status")

    body: Dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": "gr_" + secrets.token_hex(12),
        "run_id": run_id,
        "exec_instance_id": exec_instance_id,
        "gate": {
            "gate_id": gb.get("gate"),
            "gate_version": gate_version if gate_version is not None else gb.get("gate_version"),
            "severity_policy_version": (severity_policy_version
                                        if severity_policy_version is not None
                                        else ((gb.get("severity_policy") or {}).get("version"))),
            "aux_policy_version": aux_pv,
            "gate_file_sha256": gate_file_sha256(),
        },
        "snapshot_digest": gb.get("input_sha256"),
        "verdict_digest": gate_verdict_digest(gb),
        "verdict": gb.get("verdict"),
        "claim_level": gb.get("claim_level"),
        "pit_status": pit_st,
        "severe_blocked": severe_blk,
        "run_allowed": gb.get("run_allowed"),
        "trusted_backtest_allowed": gb.get("trusted_backtest_allowed"),
        "data_qualifies_for_trusted": gb.get("data_qualifies_for_trusted"),
        "issued_at_ms": now_ms(),
        "issuer": {
            "pid": os.getpid(),
            "imported_at_ms": _IMPORT_MS,
            "key_fp": key_fingerprint(key),
            "note": issuer_note,
        },
    }
    body["mac"] = _mac(key, body)
    try:
        append_chain("GATE_RECEIPT", run_id=run_id, exec_instance_id=exec_instance_id,
                     payload_digest=body["mac"],
                     extra={"receipt_id": body["receipt_id"]})
    except Exception:                                        # noqa: BLE001
        # 链写不进去不影响回执本身的签名有效性；但会让「回执已登记」这一环缺失，
        # 校验侧不依赖该条目（回执自带 HMAC），所以这里只吞不抛。
        pass
    return body


def _exec_instance_of_run(run_id: str) -> Optional[str]:
    """读 run.json 取 `execution_instance.exec_instance_id`（延迟导入避免循环）。"""
    try:
        import backtest_store as S
        doc = S.read_run(run_id)
    except Exception:                                        # noqa: BLE001
        return None
    return ((doc.get("execution_instance") or {}).get("exec_instance_id"))


def verify_receipt(receipt: Optional[Dict[str, Any]], *, key: Optional[bytes] = None) -> Dict[str, Any]:
    """回执自校验（不涉及 run.json）。返回 `{ok, reason}`。"""
    if not receipt:
        return {"ok": False, "reason": "RECEIPT_MISSING"}
    key = key if key is not None else load_key()
    if key is None:
        return {"ok": False, "reason": "KEY_UNAVAILABLE"}
    body = {k: v for k, v in receipt.items() if k != "mac"}
    if not hmac.compare_digest(str(receipt.get("mac") or ""), _mac(key, body)):
        return {"ok": False, "reason": "BAD_RECEIPT_SIGNATURE"}
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return {"ok": False, "reason": "RECEIPT_SCHEMA_UNKNOWN"}
    return {"ok": True, "reason": None}


# ══════════════════════════ 终态背书（attest） ══════════════════════════

def attested_projection(doc: Dict[str, Any]) -> Dict[str, Any]:
    """可信投影：白名单字段 + **字段清单本身**（防止以后悄悄改清单）。"""
    proj = {k: doc.get(k) for k in ATTESTED_FIELDS}
    proj["_fields"] = list(ATTESTED_FIELDS)
    return proj


def result_digest(doc: Dict[str, Any]) -> str:
    """结果摘要：指标 + 数据指纹（`input_sha256` 在 `data_gate` 里，另由投影覆盖）。"""
    return digest({"metrics": doc.get("metrics"), "hashes": doc.get("hashes")})


def attest_run(run_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """终态背书。**没有 SEAL 就拒绝背书**（防「对历史运行补签」）。

    失败不抛，把原因写进返回块（`attest_ok=False`）。
    """
    block: Dict[str, Any] = {
        "schema": SCHEMA,
        "attested": False,
        "attested_at_ms": now_ms(),
        "attested_at_utc": utc_now_iso(),
        "attest_ok": False,
        "attest_error": None,
    }
    # 结构规范化：手写 run.json 可能把 execution_instance 写成字符串
    _ei = doc.get("execution_instance")
    _ei = _ei if isinstance(_ei, dict) else {}
    xi = _ei.get("exec_instance_id")
    if not xi:
        block["attest_error"] = "EXEC_INSTANCE_MISSING"
        return block
    key = load_key()
    if key is None:
        block["attest_error"] = "KEY_UNAVAILABLE"
        return block
    try:
        seal = find_entry("SEAL", run_id, xi)
        if seal is None:
            block["attest_error"] = "NO_SEAL"
            return block
        if seal.get("key_fp") and seal.get("key_fp") != key_fingerprint(key):
            block["attest_error"] = "SEAL_KEY_MISMATCH"
            return block
        gb = doc.get("data_gate") or {}
        receipt = ((doc.get("provenance") or {}).get("gate_receipt"))
        payload = {
            "schema": SCHEMA,
            "run_id": run_id,
            "exec_instance_id": xi,
            "seal_seq": seal.get("seq"),
            "sealed_at_ms": _ei.get("sealed_at_ms"),
            "spec_digest": _ei.get("spec_digest"),
            "attested_projection_digest": digest(attested_projection(doc)),
            "result_digest": result_digest(doc),
            "gate_block_present": bool(gb),
            "gate_verdict_digest": gate_verdict_digest(gb) if gb else None,
            "gate_receipt_digest": (digest({k: v for k, v in receipt.items() if k != "mac"})
                                    if receipt else None),
            "status": doc.get("status"),
            "claim_level": gb.get("claim_level"),
            "pit_status": doc.get("pit_status"),
            "key_fp": key_fingerprint(key),
        }
        rec = append_chain("ATTEST", run_id=run_id, exec_instance_id=xi,
                           payload_digest=digest(payload))
        block.update({
            "attested": True,
            "attest_ok": True,
            "exec_instance_id": xi,
            "attest_seq": rec["seq"],
            "key_fp": key_fingerprint(key),
            "payload": payload,
            "mac": _mac(key, payload),
        })
    except Exception as exc:                                 # noqa: BLE001
        block["attest_error"] = f"{type(exc).__name__}: {exc}"
    return block


# ══════════════════════════ 读取校验（唯一信任判据） ══════════════════════════

def _r(code: str, detail: str) -> Dict[str, str]:
    return {"code": code, "detail": detail}


def verify_run(run_id: str, *, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """校验一次运行的可信身份。**绝不抛异常**，任何内部错误都降级为 UNVERIFIED。

    为什么要有这层包装：run.json 是**外部可写文件**，畸形结构（`provenance` 写成字符串、
    `data_gate` 写成列表）会让朴素实现抛 `AttributeError`。校验器一崩，调用方要么 500、
    要么在 `except` 里当成「没问题」放过去 —— 两种都比 fail-closed 更糟。
    """
    try:
        return _verify_run_impl(run_id, doc=doc)
    except Exception as exc:                                 # noqa: BLE001
        return {
            "schema": SCHEMA, "run_id": run_id, "state": STATE_UNVERIFIED,
            "reasons": [_r("VERIFIER_ERROR",
                           f"校验器内部异常，按 fail-closed 降级：{type(exc).__name__}: {exc}")],
            "checks": {}, "claim_level": None, "pit_status": None, "attested": False,
        }


def _verify_run_impl(run_id: str, *, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """校验一次运行的可信身份。**fail-closed**：任何缺失/不一致都不给 `VERIFIED_TRUSTED`。

    返回：`{schema, run_id, state, reasons[], checks{}, claim_level, pit_status}`
    `state` 三值：`VERIFIED_TRUSTED` / `UNVERIFIED` / `REJECTED`。
    """
    rep: Dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "state": STATE_UNVERIFIED,
        "reasons": [],
        "checks": {},
        "claim_level": None,
        "pit_status": None,
        "attested": False,
    }

    if doc is None:
        try:
            import backtest_store as S
            d = S.read_run_dir(run_id)
        except Exception as exc:                             # noqa: BLE001
            rep["reasons"].append(_r("RUN_NOT_FOUND", f"{type(exc).__name__}: {exc}"))
            return rep
        rj = d / "run.json"
        rep["checks"]["run_json_exists"] = rj.exists()
        if not rj.exists():
            # 历史目录（无 run.json）⇒ 只读合成视图，不可能可信
            rep["reasons"].append(_r("NO_RUN_JSON",
                                     "该目录无 run.json（历史批量产物），不存在可验证的执行记录"))
            return rep
        try:
            doc = json.loads(rj.read_text(encoding="utf-8"))
        except Exception as exc:                             # noqa: BLE001
            rep["reasons"].append(_r("RUN_JSON_CORRUPT", f"{type(exc).__name__}: {exc}"))
            return rep

    # 结构规范化：run.json 是外部可写文件，`data_gate` / `provenance` 可能被写成
    # 字符串、列表、数字。**先判类型再用**，否则后面每个 .get() 都是崩溃点。
    _raw_gb = doc.get("data_gate")
    gb = _raw_gb if isinstance(_raw_gb, dict) else {}
    claim = gb.get("claim_level")
    pit = doc.get("pit_status")
    rep["claim_level"] = claim
    rep["pit_status"] = pit
    rep["checks"]["data_gate_present"] = bool(gb)
    rep["checks"]["gate_enabled"] = gb.get("enabled")

    # run.json 里的 run_id 必须与目录名一致（改 run_id 冒充另一个运行的第一步）。
    # 老记录可能根本没写 run_id ⇒ 只登记为 None，**不据此判 REJECTED**（缺字段不是篡改）。
    _doc_rid = doc.get("run_id")
    rep["checks"]["run_id_matches_dir"] = (None if _doc_rid is None else (_doc_rid == run_id))
    if _doc_rid is not None and _doc_rid != run_id:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "RUN_ID_DIR_MISMATCH",
            f"run.json 的 run_id={_doc_rid!r} 与目录名 {run_id!r} 不一致"))
        return rep

    # 背书结构（损坏证明 ⇒ 直接 REJECTED：损坏的证据不是「没有主张」）
    _raw_prov = doc.get("provenance")
    prov = _raw_prov if isinstance(_raw_prov, dict) else {}
    _payload = prov.get("payload")
    _prov_corrupt = (_raw_prov is not None and not isinstance(_raw_prov, dict))
    if _payload is not None and not isinstance(_payload, dict):
        _prov_corrupt = True
    if _prov_corrupt:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "CORRUPT_ATTESTATION",
            "provenance 块结构损坏（不是对象，或 payload 不是对象）⇒ 按 fail-closed 判拒绝"))
        return rep
    has_attestation = bool(prov.get("mac")) and isinstance(_payload, dict)
    rep["attested"] = has_attestation
    rep["checks"]["attestation_present"] = has_attestation

    # ── A. 没有背书：先看它**有没有声称可信** ──────────────────────────────
    if not has_attestation:
        if claim == CLAIM_TRUSTED:
            rep["state"] = STATE_REJECTED
            rep["reasons"].append(_r(
                "UNBACKED_TRUSTED_CLAIM",
                "run.json 声称 claim_level=TRUSTED，但没有任何执行背书；"
                "手写 claim 不构成可信身份"))
        elif pit == PIT_PROVEN:
            rep["state"] = STATE_REJECTED
            rep["reasons"].append(_r(
                "UNBACKED_PIT_PROVEN",
                "run.json 声称 pit_status=PIT_PROVEN，但没有任何执行背书"))
        elif prov.get("registration") == "EXTERNAL_REGISTRATION":
            rep["reasons"].append(_r(
                "EXTERNAL_REGISTRATION",
                "该 run 由 register_external_run 事后补登记，没有执行期证明"))
        else:
            rep["reasons"].append(_r(
                "NO_ATTESTATION",
                "run.json 无 provenance 背书（历史记录或非 create_run 路径产生）"))
        return rep

    # ── B. 有背书：逐环验证（顺序即失败优先级） ────────────────────────────
    key = load_key()
    if key is None:
        rep["reasons"].append(_r("KEY_UNAVAILABLE",
                                 f"信任锚不可用（{key_path()}），无法验签 ⇒ 只能 UNVERIFIED"))
        return rep

    payload = prov.get("payload") or {}
    body = {k: v for k, v in payload.items()}
    if not hmac.compare_digest(str(prov.get("mac") or ""), _mac(key, body)):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("BAD_SIGNATURE",
                                 "背书签名与信任锚不符 ⇒ 背书被伪造或被换密钥重签"))
        return rep
    rep["checks"]["signature_ok"] = True

    # 背书是不是给**这个 run** 的
    if payload.get("run_id") != run_id:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "ATTESTATION_RUN_MISMATCH",
            f"背书绑定 run_id={payload.get('run_id')!r}，与本 run {run_id!r} 不符"))
        return rep

    xi = ((doc.get("execution_instance") or {}).get("exec_instance_id"))
    rep["checks"]["exec_instance_id"] = xi
    if not xi:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("EXEC_INSTANCE_MISSING",
                                 "run.json 缺 execution_instance（执行实例标识被删除）"))
        return rep
    if payload.get("exec_instance_id") != xi:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "ATTESTATION_INSTANCE_MISMATCH",
            f"背书绑定执行实例 {payload.get('exec_instance_id')!r}，与当前 {xi!r} 不符"))
        return rep

    # 门禁块是否被整块摘掉
    if payload.get("gate_block_present") and not gb:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("GATE_BLOCK_REMOVED",
                                 "背书时存在 data_gate 块，当前已被删除"))
        return rep

    # 可信投影摘要（挡：改收益 / 改配置 / 删门禁字段 / 改 run_id / 改状态）
    got = digest(attested_projection(doc))
    if got != payload.get("attested_projection_digest"):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "DIGEST_MISMATCH",
            "run.json 可信投影摘要与背书不符 ⇒ 背书之后字段被改过"
            f"（当前 {got[:16]}…，背书 {str(payload.get('attested_projection_digest'))[:16]}…）"))
        return rep
    rep["checks"]["projection_digest_ok"] = True

    # 结果摘要（挡：只改收益不动其它字段的定向篡改）
    rd = result_digest(doc)
    if rd != payload.get("result_digest"):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("RESULT_DIGEST_MISMATCH", "结果摘要与背书不符"))
        return rep
    rep["checks"]["result_digest_ok"] = True

    # 链完整性 + SEAL 存在
    ch = verify_chain(key=key)
    rep["checks"]["chain"] = ch
    if not ch["ok"]:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("CHAIN_BROKEN",
                                 f"溯源链在第 {ch['broken_seq']} 条断裂（{ch['reason']}）"))
        return rep
    seal = find_entry("SEAL", run_id, xi)
    rep["checks"]["seal_found"] = seal is not None
    if seal is None:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "NO_SEAL",
            "溯源链里没有该 (run_id, 执行实例) 的 SEAL 条目 ⇒ 该 run 不是 create_run 产生的，"
            "或 SEAL 被删除（历史运行补签一律拒绝）"))
        return rep

    # 背书必须**同时**存在于追加式账本里：否则「删掉链尾那一行」就等于既删了证据
    # 又留着一个自洽的 HMAC（HMAC 只证明「有人签过」，不证明「签过这件事还在账上」）。
    att = find_entry("ATTEST", run_id, xi)
    rep["checks"]["attest_entry_found"] = att is not None
    if att is None:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "ATTEST_ENTRY_MISSING",
            "溯源链里没有该 run 的 ATTEST 条目 ⇒ 背书未登记或被从账本中删除"))
        return rep
    if str(att.get("payload_digest")) != digest(payload):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "ATTEST_ENTRY_MISMATCH",
            "溯源链里登记的背书摘要与 run.json 里的背书不符"))
        return rep

    # 门禁回执（决定能否升到 TRUSTED）
    receipt = prov.get("gate_receipt")
    rv = verify_receipt(receipt, key=key)
    rep["checks"]["receipt"] = rv
    if not rv["ok"]:
        code = "NO_GATE_RECEIPT" if rv["reason"] == "RECEIPT_MISSING" else rv["reason"]
        if claim == CLAIM_TRUSTED:
            rep["state"] = STATE_REJECTED
            rep["reasons"].append(_r(
                code,
                "声称 TRUSTED 但缺少/损坏门禁执行回执 ⇒ 无法证明该门禁结论是给本 run 算的"))
            return rep
        rep["reasons"].append(_r(code, "无门禁执行回执（研究运行，不构成可信主张）"))
        rep["state"] = STATE_UNVERIFIED
        return rep

    if receipt.get("run_id") != run_id:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("RECEIPT_RUN_MISMATCH",
                                 f"回执绑定 run_id={receipt.get('run_id')!r}，不是本 run"))
        return rep
    if receipt.get("exec_instance_id") != xi:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("RECEIPT_INSTANCE_MISMATCH",
                                 "回执绑定的执行实例与本 run 不符（回执是从别的 run 搬来的）"))
        return rep
    if receipt.get("snapshot_digest") != gb.get("input_sha256"):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r(
            "RECEIPT_INPUT_MISMATCH",
            "回执的快照摘要 ≠ 当前 data_gate.input_sha256 ⇒ 门禁结论与本次输入不匹配"))
        return rep
    if receipt.get("verdict_digest") != gate_verdict_digest(gb):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("RECEIPT_VERDICT_MISMATCH",
                                 "回执的判定摘要 ≠ 当前 data_gate 块 ⇒ 门禁字段被改过"))
        return rep
    if payload.get("gate_receipt_digest") and \
            payload["gate_receipt_digest"] != digest({k: v for k, v in receipt.items() if k != "mac"}):
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("RECEIPT_SWAPPED_AFTER_ATTEST",
                                 "背书之后门禁回执被替换"))
        return rep
    rep["checks"]["receipt_bound"] = True

    # 阻断伪造：如果回执明确记录了 pit_status，run.json 顶层必须与之严格一致
    rec_pit = receipt.get("pit_status")
    if rec_pit is not None and rec_pit != pit:
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("PIT_STATUS_MISMATCH",
                                 f"run.json pit_status={pit!r} 与门禁回执记录 {rec_pit!r} 不符"))
        return rep

    # 声称与状态是否自相矛盾
    if claim == CLAIM_TRUSTED and doc.get("status") == "failed":
        rep["state"] = STATE_REJECTED
        rep["reasons"].append(_r("TRUSTED_CLAIM_ON_FAILED_RUN",
                                 "status=failed 却声称 TRUSTED（拒绝的运行不得变成成功运行）"))
        return rep

    # 三条件同时成立才给 VERIFIED_TRUSTED
    cond = {
        "claim_level_is_trusted": claim == CLAIM_TRUSTED,
        "verdict_is_pass": gb.get("verdict") == VERDICT_PASS,
        "pit_is_proven": pit == PIT_PROVEN,
        "data_qualifies": gb.get("data_qualifies_for_trusted") is True,
        "trusted_allowed": gb.get("trusted_backtest_allowed") is True,
        "gate_enabled": gb.get("enabled") is True,
    }
    rep["checks"]["trust_conditions"] = cond
    if all(cond.values()):
        rep["state"] = STATE_VERIFIED_TRUSTED
        rep["reasons"].append(_r("ALL_CHECKS_PASSED",
                                 "背书、链、回执、三条件全部成立"))
    else:
        rep["state"] = STATE_UNVERIFIED
        rep["reasons"].append(_r(
            "ATTESTED_NOT_TRUSTED",
            "执行记录已背书，但主张等级不是 TRUSTED 或三条件未同时成立 ⇒ 只能当隔离研究引用"))
    return rep


def is_trusted(run_id: str, *, doc: Optional[Dict[str, Any]] = None) -> bool:
    """便捷判定：只有 `VERIFIED_TRUSTED` 返回 True。"""
    return verify_run(run_id, doc=doc)["state"] == STATE_VERIFIED_TRUSTED


def require_trusted(run_id: str, *, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """准入用：非 `VERIFIED_TRUSTED` 一律抛 `PermissionError`（fail-closed）。"""
    rep = verify_run(run_id, doc=doc)
    if rep["state"] != STATE_VERIFIED_TRUSTED:
        codes = ",".join(r["code"] for r in rep["reasons"])
        raise PermissionError(f"运行 {run_id} 不是 VERIFIED_TRUSTED（{rep['state']}：{codes}）")
    return rep


__all__ = [
    "SCHEMA", "RECEIPT_SCHEMA", "STATES",
    "STATE_VERIFIED_TRUSTED", "STATE_UNVERIFIED", "STATE_REJECTED",
    "CLAIM_TRUSTED", "CLAIM_ISOLATED", "PIT_PROVEN",
    "ATTESTED_FIELDS", "VOLATILE_FIELDS",
    "HOME_ENV", "home", "key_path", "chain_path", "lock_path",
    "KeyUnavailable", "NoSeal", "ChainCorrupt", "ChainLockTimeout",
    "canon", "digest", "utc_now_iso", "now_ms",
    "load_key", "key_fingerprint",
    "read_chain", "chain_snapshot", "append_chain", "verify_chain", "find_entry",
    "new_exec_instance_id", "spec_digest_of", "seal_run",
    "gate_file_sha256", "gate_verdict_digest", "issue_gate_receipt", "verify_receipt",
    "attested_projection", "result_digest", "attest_run",
    "verify_run", "is_trusted", "require_trusted",
]
