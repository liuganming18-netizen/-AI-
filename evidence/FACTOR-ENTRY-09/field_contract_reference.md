# ENTRY-AUDIT-04 字段契约规范引用

本规范直接继承自 `evidence/ENTRY-AUDIT-04/handoff/` 冻结交接包（版本：`ENTRY-AUDIT-04.handoff.v1`）。

## 一、主键关联契约
1. **全局唯一候选标识**：`candidate_id`（全量 227 行唯一，无重复、无空缺）。
2. **信号快照标识**：`signal_key = {symbol}_{snapshot_time_ms}`（226 个唯一值，COTIUSDT 1640228400000 存在连续/窗口回放冲突样本，各自保留独立运行身份）。
3. **订单关联键**：已成交订单必须使用 `(run_id, order_id)` 复合键（205/205 唯一）；严禁单用 `order_id`，否则会引发跨代际串账。

## 二、真实账本与净盈亏公式契约
$$\text{net\_pnl} \equiv \text{gross\_pnl} - \text{fees} - \text{funding\_fee}$$
- **`gross_pnl`**：毛盈亏，多空统一为平仓现金流减开仓现金流，正=赚。
- **`fees`**：真实总手续费 $= \text{entry\_fee} + \text{exit\_fee}$，正=支出。
- **`funding_fee`**：真实资金费（成本口径），正=支出，负=收入。
- **滑点处理**：滑点损失 `slippage_loss` 与取整段 `rounding_loss` 已经体现在 `entry_price` 与 `exit_price` 中，严禁在 `net_pnl` 中二次重复扣减。

## 三、评价体系与分类契约
- **分母定义**：全量 227 行特征样本；入场质量分类分母为 **205 笔已成交**；未成交 22 笔（13 跳空越线 + 9 跌回箱体）统一标记为 `UNFILLED`。
- **必需输入**：缺少 `fwd_ret_4h`、`mfe_r_4h`、`mae_r_4h`、`first_move_4h`、`mfe_r`、`net_pnl` 等必需输入时严禁默认填 0，必须判定为 `UNKNOWN_MISSING_INPUT`。
- **主体系分类优先级（标准混合度量）**：
  1. Priority 1（早期走弱 `EARLY_WEAKNESS`）：$r_{4h} < 0 \land mfe_{atr, 4h} < 0.5 \land (mae_{atr, 4h} \ge 0.5 \lor first\_move = \text{FIRST\_ADVERSE})$
  2. Priority 2（浮盈回吐 `MFE_PULLBACK`）：$mfe_r \ge 1.0 \land net\_pnl \le 0$
  3. Priority 3（持续主升 `SUSTAINED_TREND`）：$mfe_r \ge 1.5 \land net\_pnl > 0$
  4. Priority 4（杂波/其他 `CHOPPY_OR_OTHER`）：其余所有已成交样本。

## 四、三态治理标记契约
- `spec_qualification`：合约规格判定（212 条 `unverified_historical`，13 条 `contradicted`，2 条 `not_listed`，**0 条 `verified`**）。
- `formal_pass_allowed`：全量 **`false`**（严禁用于正式回测与生产部署）。
- `exec_validity`：全量 `valid`（撮合引擎运行逻辑自检有效）。
- `research_usability`：`usable_with_caveat`（仅供探索性研究与归因排查使用）。
