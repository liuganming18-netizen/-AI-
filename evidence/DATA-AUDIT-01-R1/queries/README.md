# queries · 可直接复制执行的只读查询

工单整改要求 3：「为持仓量覆盖、新鲜度、冷档索引异常、大户指标停更、资金费冷读失败等结论
提供**可复算的只读查询**、完整结果和统计时点。」

`04_可复算查询与结果.md` 里的查询是给人看的摘要；这里放的是**能原样跑**的文件。

## 文件清单

| 文件 | 对应结论 | 本单那次运行的完整结果 | 事实 ID |
|---|---|---|---|
| `_pool_526.sql` | 池内 526 币视图（自动生成，勿手改） | 526 行，来自 `scope_crypto.json` | F01 |
| `Q1_oi_coverage.sql` | 持仓量 5m 覆盖 | 池内 **526/526**；全库 528 币；池内 6,188,452 行 / 全库 6,197,631 行 | F10、F13 |
| `Q2_oi_freshness.sql` | 持仓量 5m 新鲜度 | **520/526（98.86%）≤1h**；`1d~3d` 2、`>3d` 4；最久 10.67 天 | F11、F12 |
| `Q3_catalog_diff.py` | 冷档索引异常 | 磁盘 42,482 / 索引 32,920；未登记 **11,471**（live 9,298）；悬空 **1,909** | F50、F51、F52 |
| `Q4_metrics_staleness.py` | 大户指标停更 | 526 币 / **127,973,455 行**；最新 2026-10-14T23:55Z；停更 >7 天 **526/526** | F40、F41、F42 |
| `Q5_funding_cold_read.py` | 资金费冷读失败 | 文件在盘 **526/526**；冷读 **0 行 526/526**；对照 oi/candles 冷读各 500 行 | F22、F23、F24 |
| `Q9_pit_fill.py` | 可得时间（PIT）填充率 | 冷档 `available_at` **0%**；冷档 funding **无 fetched_at 列**；热库 funding 100% | F25、F43、F6-01… |

统计时点见各文件头注释；机器可读索引在 `out/92_query_manifest.json`（含每个文件的 sha256）。

## 怎么跑

### SQL 两条（必须先建 pool 视图）

```bash
cd "/Users/boos/Desktop/威科夫因子共振趋势"
DB="数据/市场数据/db/market_v2.sqlite3"

# 方式一：sqlite3 CLI（-readonly 保证不写；实测可用）
sqlite3 -readonly "$DB" ".read evidence/DATA-AUDIT-01-R1/queries/_pool_526.sql" \
                         ".read evidence/DATA-AUDIT-01-R1/queries/Q1_oi_coverage.sql"
```

```python
# 方式二：用托管 python（不依赖 sqlite3 CLI，同样只读）
import os, sqlite3, pathlib
q = pathlib.Path("evidence/DATA-AUDIT-01-R1/queries")
db = os.path.abspath("数据/市场数据/db/market_v2.sqlite3")

c = sqlite3.connect(":memory:", uri=True)          # 主库在内存，业务库只读附着
c.execute("ATTACH DATABASE ? AS m", (f"file:{db}?mode=ro",))
c.executescript((q / "_pool_526.sql").read_text(encoding="utf-8"))
c.execute("PRAGMA query_only=ON")                  # 建完视图再锁（见下方坑）

for f in ("Q1_oi_coverage.sql", "Q2_oi_freshness.sql"):
    body = "\n".join(l for l in (q / f).read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("--"))
    for stmt in [s for s in body.split(";") if s.strip()]:
        rows = c.execute(stmt).fetchall()
        print(len(rows), "行", rows[:3])
```

> **两个坑（都实测踩过）**：
> ① `PRAGMA query_only` 是**连接级**的，不是库级的——写成 `PRAGMA m.query_only=ON` 会连带
> 禁止建 TEMP VIEW，报 `attempt to write a readonly database`。要在建完视图之后再锁。
> ② 直接 `connect("file:...?mode=ro")` 再 `CREATE TEMP VIEW` 也会报同一个错；
> 必须「主库内存 + 业务库只读附着」。

### Python 四个

```bash
cd "/Users/boos/Desktop/威科夫因子共振趋势"
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
$PY evidence/DATA-AUDIT-01-R1/queries/Q3_catalog_diff.py
$PY evidence/DATA-AUDIT-01-R1/queries/Q4_metrics_staleness.py
$PY evidence/DATA-AUDIT-01-R1/queries/Q5_funding_cold_read.py
$PY evidence/DATA-AUDIT-01-R1/queries/Q9_pit_fill.py
```

## 读数时的四条纪律

1. **行数会变，结构性事实不会变**。采集器一直在写热库，`oi_history` 5m 行数每次跑都不一样
   （本单 6,197,631，之后继续增长）。判定看**桶计数**（520/526、526/526、0 行）与
   **结构性事实**（列不存在、路径深度不匹配），不看绝对行数。
2. **`ts` 是整数毫秒**，不是 ISO 字符串。用 `strftime` 前必须 `/1000`，否则得 NULL。
3. **新鲜度按 `MAX(ts)` 算，不是 `MAX(fetched_at)`**。池内 `fetched_at` 只有 42.98% 有值，
   拿它算新鲜度会把「没记采集时刻」误读成「数据陈旧」。
4. **池内 = 526**。`scope_crypto.json` 的 `binance_items` 里含 5 个中文符号合约
   （`币安人生USDT`、`我踏马来了USDT`、`龙虾USDT`、`牛来USDT`、`哈基米USDT`），
   是币安真实合约、不是脏数据；池外还有 2 个加密指数（`BTCDOMUSDT`、`ALLUSDT`）被明确排除。

## 与 scripts/ 的关系

`queries/` 是**给人复算的最小查询**；`scripts/` 是产出 `out/` 全部证据的完整脚本。
两者口径一致，但 `queries/` 只打印、不落盘，`scripts/` 会写 `out/*.json|csv`。
需要重新生成全部证据时用 `reproduce.sh`，不要逐个跑 `queries/`。
