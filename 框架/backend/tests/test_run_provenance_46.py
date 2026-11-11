#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-BACKTEST-TRUSTED-RUN-PROVENANCE-FIX-46 回归：可信声明必须与执行记录绑定。

每个用例对应 43 号审计实测过的一个「真的能骗过人」的操作。判据只有一条：
**这些操作不得被可信结果读取接口接受为 `VERIFIED_TRUSTED`。**

不依赖 pytest（项目测试走 stdlib unittest），不联网，不碰生产目录：
`EVENT_TREND_RUNS_DIR` 与 `WX_PROVENANCE_HOME` 全部指向临时目录。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

_TMP = None
_OLD_ENV = {}


def setUpModule():
    global _TMP, _OLD_ENV
    _TMP = tempfile.mkdtemp(prefix="gm46_test_")
    runs = os.path.join(_TMP, "runs")
    os.makedirs(runs, exist_ok=True)
    prov = os.path.join(_TMP, "prov")
    os.makedirs(prov, exist_ok=True)
    for k, v in (("EVENT_TREND_RUNS_DIR", runs), ("WX_PROVENANCE_HOME", prov)):
        _OLD_ENV[k] = os.environ.get(k)
        os.environ[k] = v


def tearDownModule():
    for k, v in _OLD_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


import backtest_store as S            # noqa: E402
import backtest_run_provenance as P   # noqa: E402

SEQ = {"n": 0}


def rid(tag: str = "") -> str:
    """合法 run_id（匹配 `RUN_ID_RE`）：`bt_<8>T<6>Z_<8hex>`。"""
    SEQ["n"] += 1
    h = hashlib.sha256(f"{tag}|{SEQ['n']}".encode()).hexdigest()[:8]
    return f"bt_20261031T{SEQ['n']:06d}Z_{h}"


def gate_block(*, claim="TRUSTED", verdict="PASS", input_sha="a" * 64,
               pit="PIT_PROVEN", enabled=True, qualifies=True):
    """照 25/37 号接线层 `stamp()` 的 21 键形状造门禁块。"""
    return {
        "gate": "GM-BACKTEST-DATA-GATE-17",
        "gate_version": "v2",
        "enabled": enabled,
        "mode": "trusted",
        "verdict": verdict,
        "data_qualifies_for_trusted": qualifies,
        "trusted_backtest_allowed": qualifies and verdict == "PASS",
        "run_allowed": True,
        "isolation_required": not qualifies,
        "isolation_label": None,
        "claim_level": claim,
        "counts": {"checks": 12, "fail": 0 if verdict == "PASS" else 1,
                   "unproven": 0, "pass": 12 if verdict == "PASS" else 11},
        "block_reasons": [],
        "unproven_reasons": [],
        "checks": [{"id": "KL_EMPTY", "status": "PASS", "detail": "ok", "evidence": {}}],
        "verdict_sha256": "b" * 64,
        "input_sha256": input_sha,
        "generated_at_utc": "2026-10-31T00:00:00Z",
        "subject": {"symbol": "BTCUSDT", "signal_timeframe": "1h", "exec_timeframe": "15m"},
        "no_fabrication": {"oi_filled_rows": 0, "funding_filled_rows": 0},
        "severity_policy": {"version": "s1"},
        "summary_zh": "判定 PASS｜正式可信回测放行",
    }


def spec_of(**kw):
    d = {"strategy_id": "WX_EVENT_TREND_V0_1", "symbols": ["BTCUSDT"],
         "exchange": "binance", "market": "um",
         "signal_timeframe": "1h", "exec_timeframe": "15m", "initial_equity": 2000.0}
    d.update(kw)
    return d


def config_of(**kw):
    d = {"actual_config": {"initial_equity": 2000.0, "risk_budget_per_trade": 0.005,
                           "fee_rate": 0.0005, "slippage_bps": 3.0}}
    d.update(kw)
    return d


def make_run(*, gate=None, pit=None, status="succeeded", metrics=None,
             spec=None, config=None, with_receipt=True):
    """走正规路径造一个 run：create_run（seal）→ 回执 → set_status（attest）。"""
    r = rid("run")
    S.create_run(r, {"spec": spec or spec_of(), "config": config or config_of(),
                     "window": {"win_start_ms": 1700000000000,
                                "win_end_ms": 1702592000000, "symbol": "BTCUSDT"}})
    if gate is not None and with_receipt:
        rec = P.issue_gate_receipt(run_id=r, gate_block=gate)
        S.record_gate_receipt(r, rec)
    extra = {"metrics": metrics or {"net_pnl": 400.0, "trades_count": 7,
                                    "final_equity": 2400.0, "net_return_pct": 20.0}}
    if gate is not None:
        extra["data_gate"] = gate
    if pit is not None:
        extra["pit_status"] = pit
    S.set_status(r, status, **extra)
    return r


def run_json_path(r: str) -> Path:
    return S.read_run_dir(r) / "run.json"


def read_json(r: str) -> dict:
    return json.loads(run_json_path(r).read_text(encoding="utf-8"))


def write_json(r: str, doc: dict) -> None:
    """**模拟攻击者的直接文件写入**（绕过 store 的一切逻辑）。"""
    run_json_path(r).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                encoding="utf-8")


def make_bare_dir(r: str, doc: dict) -> None:
    """造一个「目录在、run.json 手写」的运行（无 SEAL、无背书）。"""
    d = S.get_runs_root() / r
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                encoding="utf-8")


def codes(rep: dict):
    return [x["code"] for x in rep.get("reasons") or []]


class T00_基线(unittest.TestCase):
    def test_可信记录被识别为_VERIFIED_TRUSTED(self):
        g = gate_block()
        r = make_run(gate=g, pit=P.PIT_PROVEN)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "VERIFIED_TRUSTED", codes(rep))
        self.assertTrue(S._PROV.is_trusted(r))
        # 三条件确实同时成立
        cond = rep["checks"]["trust_conditions"]
        self.assertTrue(all(cond.values()), cond)

    def test_研究运行不得冒充_TRUSTED(self):
        g = gate_block(claim="ISOLATED_RESEARCH", verdict="UNPROVEN",
                       qualifies=False)
        r = make_run(gate=g, pit="PIT_UNPROVEN")
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "UNVERIFIED", codes(rep))
        self.assertIn("ATTESTED_NOT_TRUSTED", codes(rep))

    def test_历史无_run_json仍可安全读取(self):
        r = rid("hist")
        d = S.get_runs_root() / r
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps({
            "symbol": "BTCUSDT",
            "summary_variant_a": {"initial_equity": 2000.0, "final_equity": 2100.0,
                                  "net_pnl": 100.0, "trade_count": 3},
        }), encoding="utf-8")
        doc = S.read_run(r)                       # 合成只读视图仍可读
        self.assertEqual(doc["registered"], False)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "UNVERIFIED")
        self.assertIn("NO_RUN_JSON", codes(rep))

    def test_拒绝记录不会变成成功运行(self):
        g = gate_block(verdict="FAIL", claim="TRUSTED", qualifies=False)
        r = make_run(gate=g, pit="PIT_UNPROVEN", status="failed")
        rep = S.verify_run_trust(r)
        self.assertNotEqual(rep["state"], "VERIFIED_TRUSTED")
        self.assertIn("TRUSTED_CLAIM_ON_FAILED_RUN", codes(rep))

    def test_校验不写盘(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        p = run_json_path(r)
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        for _ in range(3):
            S.verify_run_trust(r)
        after = hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(before, after, "校验路径不得修改 run.json")


class T01_攻击一_复制门禁块到另一个运行(unittest.TestCase):
    def test_A1a_直接手写run_json带别人的data_gate(self):
        a = make_run(gate=gate_block(input_sha="c" * 64), pit=P.PIT_PROVEN)
        stolen = read_json(a)["data_gate"]
        b = rid("b")
        make_bare_dir(b, {
            "run_id": b, "status": "succeeded", "created_at": "2026-10-31T00:00:00Z",
            "spec": spec_of(), "config": config_of(),
            "metrics": {"net_pnl": 7999.0, "trades_count": 0},
            "data_gate": stolen, "pit_status": P.PIT_PROVEN,
        })
        rep = S.verify_run_trust(b)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("UNBACKED_TRUSTED_CLAIM", codes(rep))

    def test_A1b_在已背书运行上注入别人的data_gate(self):
        a = make_run(gate=gate_block(input_sha="d" * 64), pit=P.PIT_PROVEN)
        stolen = read_json(a)["data_gate"]
        b = make_run(gate=None, pit=None)          # 背书时无门禁块
        doc = read_json(b)
        doc["data_gate"] = stolen
        doc["pit_status"] = P.PIT_PROVEN
        write_json(b, doc)
        rep = S.verify_run_trust(b)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("DIGEST_MISMATCH", codes(rep))


class T02_攻击二_保留输入摘要改收益(unittest.TestCase):
    def test_改_metrics_被拒(self):
        r = make_run(gate=gate_block(input_sha="e" * 64), pit=P.PIT_PROVEN)
        self.assertEqual(S.verify_run_trust(r)["state"], "VERIFIED_TRUSTED")
        doc = read_json(r)
        self.assertEqual(doc["data_gate"]["input_sha256"], "e" * 64)   # 输入摘要没动
        doc["metrics"]["net_pnl"] = 99999.0
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("DIGEST_MISMATCH", codes(rep))


class T03_攻击三_改_run_id_后重新登记(unittest.TestCase):
    def test_外部补登记恒为_UNVERIFIED(self):
        a = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        src = read_json(a)
        b = rid("reg")
        d = S.get_runs_root() / b
        d.mkdir(parents=True, exist_ok=True)
        doc = dict(src)
        doc["run_id"] = b
        S.register_external_run(b, doc)
        rep = S.verify_run_trust(b)
        # 补登记会**丢掉**继承来的背书；但这条记录仍带着 TRUSTED 主张 ⇒
        # 比 UNVERIFIED 更严重：直接判 REJECTED（声称可信却没有任何证明）。
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("UNBACKED_TRUSTED_CLAIM", codes(rep))
        self.assertNotIn("ATTESTATION_RUN_MISMATCH", codes(rep))   # 背书已被丢弃

    def test_干净补登记为_UNVERIFIED(self):
        b = rid("reg2")
        d = S.get_runs_root() / b
        d.mkdir(parents=True, exist_ok=True)
        S.register_external_run(b, {"spec": spec_of(), "config": config_of(),
                                    "status": "succeeded",
                                    "metrics": {"net_pnl": 10.0}})
        rep = S.verify_run_trust(b)
        self.assertEqual(rep["state"], "UNVERIFIED", codes(rep))
        self.assertIn("EXTERNAL_REGISTRATION", codes(rep))
        self.assertTrue(read_json(b)["provenance"]["attested"] is False)

    def test_直接改run_json里的run_id被拒(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        doc = read_json(r)
        doc["run_id"] = "bt_20261031T999999Z_deadbeef"
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("RUN_ID_DIR_MISMATCH", codes(rep))


class T04_攻击四_直接写claim_level(unittest.TestCase):
    def test_手写TRUSTED被拒(self):
        r = rid("claim")
        make_bare_dir(r, {
            "run_id": r, "status": "succeeded", "created_at": "2026-10-31T00:00:00Z",
            "spec": spec_of(), "config": config_of(),
            "metrics": {"net_pnl": 5000.0},
            "data_gate": gate_block(), "pit_status": P.PIT_PROVEN,
        })
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED")
        self.assertIn("UNBACKED_TRUSTED_CLAIM", codes(rep))

    def test_在已背书运行上改claim_level被拒(self):
        r = make_run(gate=gate_block(claim="ISOLATED_RESEARCH", verdict="UNPROVEN",
                                     qualifies=False), pit="PIT_UNPROVEN")
        doc = read_json(r)
        doc["data_gate"]["claim_level"] = "TRUSTED"
        doc["data_gate"]["verdict"] = "PASS"
        doc["data_gate"]["data_qualifies_for_trusted"] = True
        doc["data_gate"]["trusted_backtest_allowed"] = True
        doc["pit_status"] = P.PIT_PROVEN
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("DIGEST_MISMATCH", codes(rep))


class T05_攻击五_删除门禁字段(unittest.TestCase):
    def test_删掉data_gate被拒(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        doc = read_json(r)
        doc.pop("data_gate", None)
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("GATE_BLOCK_REMOVED", codes(rep))


class T06_攻击六_伪造PIT_PROVEN(unittest.TestCase):
    def test_手写pit_status被拒(self):
        r = rid("pit")
        make_bare_dir(r, {
            "run_id": r, "status": "succeeded",
            "spec": spec_of(), "config": config_of(),
            "metrics": {"net_pnl": 1.0},
            "pit_status": P.PIT_PROVEN,
        })
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("UNBACKED_PIT_PROVEN", codes(rep))

    def test_已背书运行上改pit_status被拒(self):
        r = make_run(gate=gate_block(claim="ISOLATED_RESEARCH", verdict="UNPROVEN",
                                     qualifies=False), pit="PIT_UNPROVEN")
        doc = read_json(r)
        doc["pit_status"] = P.PIT_PROVEN
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("DIGEST_MISMATCH", codes(rep))


class T07_攻击七_改配置留旧门禁证明(unittest.TestCase):
    def test_改config被拒(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        doc = read_json(r)
        doc["config"]["actual_config"]["fee_rate"] = 0.0      # 把手续费改成 0
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("DIGEST_MISMATCH", codes(rep))

    def test_改spec被拒(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        doc = read_json(r)
        doc["spec"]["initial_equity"] = 1_000_000.0
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))


class T08_攻击八_给历史运行补签(unittest.TestCase):
    def test_历史运行调set_status也拿不到背书(self):
        r = rid("old")
        make_bare_dir(r, {
            "run_id": r, "status": "queued", "created_at": "2026-09-01T00:00:00Z",
            "spec": spec_of(), "config": config_of(),
            "data_gate": gate_block(), "pit_status": P.PIT_PROVEN,
        })
        S.set_status(r, "succeeded", metrics={"net_pnl": 8888.0})
        doc = read_json(r)
        self.assertFalse((doc.get("provenance") or {}).get("attested"))
        # 没有执行实例 ⇒ 连「为哪个实例背书」都说不出来；有实例但无 SEAL ⇒ NO_SEAL。
        # 两条都是拒绝补签，具体落在哪一条取决于该历史目录有没有 execution_instance。
        self.assertIn((doc.get("provenance") or {}).get("attest_error"),
                      ("NO_SEAL", "EXEC_INSTANCE_MISSING"))
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))

    def test_搬运别人的provenance块被拒(self):
        a = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        good = read_json(a)["provenance"]
        b = rid("copy")
        make_bare_dir(b, {
            "run_id": b, "status": "succeeded",
            "spec": spec_of(), "config": config_of(),
            "metrics": {"net_pnl": 7777.0},
            "data_gate": gate_block(), "pit_status": P.PIT_PROVEN,
            "provenance": good,
        })
        rep = S.verify_run_trust(b)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("ATTESTATION_RUN_MISMATCH", codes(rep))


class T09_回执与链(unittest.TestCase):
    def test_回执不能跨run搬运(self):
        a = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        rec_a = read_json(a)["provenance"]["gate_receipt"]
        b = rid("rcpt")
        S.create_run(b, {"spec": spec_of(), "config": config_of(),
                         "window": {"win_start_ms": 1, "win_end_ms": 2}})
        with self.assertRaises(PermissionError):
            S.record_gate_receipt(b, rec_a)

    def test_删门禁回执后TRUSTED降级为REJECTED(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        doc = read_json(r)
        doc["provenance"].pop("gate_receipt", None)
        write_json(r, doc)
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("NO_GATE_RECEIPT", codes(rep))

    def test_链尾被删即拒(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        self.assertEqual(S.verify_run_trust(r)["state"], "VERIFIED_TRUSTED")
        cp = P.chain_path()
        backup = cp.read_bytes()
        try:
            lines = [ln for ln in backup.decode().splitlines() if ln.strip()]
            self.assertGreaterEqual(len(lines), 2)
            cp.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            rep = S.verify_run_trust(r)
            self.assertEqual(rep["state"], "REJECTED", codes(rep))
            self.assertIn("ATTEST_ENTRY_MISSING", codes(rep))
        finally:
            cp.write_bytes(backup)

    def test_链中间被改即断(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        self.assertEqual(S.verify_run_trust(r)["state"], "VERIFIED_TRUSTED")
        cp = P.chain_path()
        backup = cp.read_bytes()
        try:
            lines = [ln for ln in backup.decode().splitlines() if ln.strip()]
            rec = json.loads(lines[0])
            rec["ts_ms"] = int(rec.get("ts_ms") or 0) + 1     # 改一个字节
            lines[0] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
            cp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            rep = S.verify_run_trust(r)
            self.assertEqual(rep["state"], "REJECTED", codes(rep))
            self.assertIn("CHAIN_BROKEN", codes(rep))
        finally:
            cp.write_bytes(backup)

    def test_换密钥后旧背书被拒(self):
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN)
        self.assertEqual(S.verify_run_trust(r)["state"], "VERIFIED_TRUSTED")
        kp = P.key_path()
        backup = kp.read_bytes()
        try:
            kp.write_text("ab" * 32, encoding="utf-8")
            rep = S.verify_run_trust(r)
            self.assertEqual(rep["state"], "REJECTED", codes(rep))
            self.assertIn("BAD_SIGNATURE", codes(rep))
        finally:
            kp.write_bytes(backup)


class T10_口径无回归(unittest.TestCase):
    def test_会计字段不被溯源改动(self):
        m = {"net_pnl": -12.345, "trades_count": 3, "fees": 1.5, "funding": -0.2,
             "final_equity": 1987.655}
        r = make_run(gate=gate_block(), pit=P.PIT_PROVEN, metrics=dict(m))
        doc = read_json(r)
        for k, v in m.items():
            self.assertEqual(doc["metrics"][k], v)
        S.verify_run_trust(r)
        doc2 = read_json(r)
        self.assertEqual(doc["metrics"], doc2["metrics"])

    def test_非终态不产生背书(self):
        r = rid("run")
        S.create_run(r, {"spec": spec_of(), "config": config_of()})
        S.set_status(r, "running")
        doc = read_json(r)
        self.assertNotIn("provenance", doc)
        self.assertTrue((doc.get("execution_instance") or {}).get("seal_ok"))
        self.assertEqual(S.verify_run_trust(r)["state"], "UNVERIFIED")

    def test_seal失败不影响创建(self):
        old = os.environ.get("WX_PROVENANCE_HOME")
        os.environ["WX_PROVENANCE_HOME"] = "/dev/null/nope"
        try:
            r = rid("noseal")
            S.create_run(r, {"spec": spec_of(), "config": config_of()})
            doc = read_json(r)
            self.assertIsNotNone(doc.get("execution_instance"))
            self.assertFalse(doc["execution_instance"]["seal_ok"])
            self.assertEqual(S.verify_run_trust(r)["state"], "UNVERIFIED")
        finally:
            if old is None:
                os.environ.pop("WX_PROVENANCE_HOME", None)
            else:
                os.environ["WX_PROVENANCE_HOME"] = old


class T11_损坏证明(unittest.TestCase):
    """工单 §四.C：对「损坏证明」必须 fail-closed，且校验器**不得抛异常**。

    为什么单列一类：`run.json` 是外部可写文件，畸形结构会让朴素实现抛
    `AttributeError`；校验器一崩，上层要么 500、要么在 `except` 里当没事放过。
    """

    def test_provenance写成字符串被拒(self):
        r = rid("bad")
        make_bare_dir(r, {"run_id": r, "status": "succeeded",
                          "spec": spec_of(), "config": config_of(),
                          "metrics": {"net_pnl": 1.0},
                          "provenance": "not-a-dict"})
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("CORRUPT_ATTESTATION", codes(rep))

    def test_payload写成字符串被拒(self):
        r = rid("bad2")
        make_bare_dir(r, {"run_id": r, "status": "succeeded",
                          "spec": spec_of(), "config": config_of(),
                          "metrics": {"net_pnl": 1.0},
                          "provenance": {"mac": "x" * 64, "payload": "not-a-dict"}})
        rep = S.verify_run_trust(r)
        self.assertEqual(rep["state"], "REJECTED", codes(rep))
        self.assertIn("CORRUPT_ATTESTATION", codes(rep))

    def test_data_gate写成列表不崩(self):
        r = rid("bad3")
        make_bare_dir(r, {"run_id": r, "status": "succeeded",
                          "spec": spec_of(), "config": config_of(),
                          "metrics": {"net_pnl": 1.0},
                          "data_gate": ["not", "a", "dict"],
                          "pit_status": P.PIT_PROVEN})
        rep = S.verify_run_trust(r)          # 不抛
        self.assertNotEqual(rep["state"], "VERIFIED_TRUSTED")
        self.assertIn("UNBACKED_PIT_PROVEN", codes(rep))

    def test_校验器对任意畸形doc都不抛(self):
        import random
        rnd = random.Random(46)
        junk = [None, 0, 1.5, "", "x", [], [1], {}, {"a": 1}, True]
        for i in range(30):
            r = rid(f"fuzz{i}")
            make_bare_dir(r, {
                "run_id": r, "status": rnd.choice(junk),
                "data_gate": rnd.choice(junk), "provenance": rnd.choice(junk),
                "pit_status": rnd.choice(junk), "metrics": rnd.choice(junk),
                "spec": rnd.choice(junk), "config": rnd.choice(junk),
            })
            try:
                rep = S.verify_run_trust(r)
            except Exception as exc:                     # noqa: BLE001
                self.fail(f"畸形 run.json 让校验器抛异常：{type(exc).__name__}: {exc}")
            self.assertIn(rep["state"], ("VERIFIED_TRUSTED", "UNVERIFIED", "REJECTED"))
            if rep["state"] == "VERIFIED_TRUSTED":
                self.fail(f"畸形 run.json 被判为可信：{rep}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
