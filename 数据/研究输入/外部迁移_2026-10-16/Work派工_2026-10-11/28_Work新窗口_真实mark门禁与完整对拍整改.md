# 新Work策略窗口：三个已复现阻塞整改

沿用04/06/07/12/23合同授权和全部冻结规则。本次实际工程施工，不只写计划；下一任务须新窗口，旧产物保留。只最新3a/3b/3c/3d、18配置、每臂跨Binance/OKX共享10000USDT，风险/费用/延迟/holdout不改。Codex审核，Work实施。

先读27独立结论及`第二轮独立复审/26_第二轮独立核验结果.json`、`27_对拍资金费篡改反例.json`；可复跑26审查入口。61个SHA和四个入口实跑rc0已核对，不重做已通过的首页或扩大策略搜索。

1. **实际mark必须真实。** wx_execution.resolve_funding_mark普通15m交易bar_open_at_T不能替代event mark。缺event mark及真实marks序列时必须拒绝/阻断；rate、mark、event_time、available_at、source/单位/历史代际均可追溯。合成夹具需显式提供真实mark测试金额，另保留“trade open存在、actual mark缺失”负例；不要删trade bar来使缺mark负例看似通过。保留普通trade open与mark不同、负rate、T^-旧仓/同刻新仓、未来close扰动及非对齐事件的严格验证。不补0、不回退entry/trade价格、不用之后bar代表T。
2. **门禁验证接受证据及实际运行身份。** 检查证据实际文件/内容SHA与Codex独立接受记录，对具体run/data/code/engine/18有效配置/holdout/执行成本/10000资本实核，不只字段非空。任何未知路径、伪SHA、accepted_by=work或任意自称codex、身份hash不匹配/旧run套用、synthetic缺失或伪装都不能放行。接受来源应采用明确冻结的Codex审查记录及其范围，不把任意可编辑label当认证，也不要求当前尚不存在的研究PASS。现阶段G01–G05真实pending；只在内存/临时目录用合成验收记录测试正例，严格与研究放行隔离。
3. **逐笔对拍漏检必须关闭。** B侧funding+1且其他字段不改，应matched=false且退出非0；hash_match=false必须阻断。同时逐笔比较fee/funding与mark价源/合约单位、意图/成交/退出完整ID、UTC、exchange+market+contract+generation、拒绝原因、整数步数、逐时现金/预占/持仓/净权益、终止权益、replay_hash及容差。新增同名跨所和代际不同负例，以及只改费用/资金费/中间权益/末权益/ID/hash的篡改负例。五场景只有3a不足以证明18配置，复用已登记配置覆盖，不新增参数。

真实Freqtrade Trade/Order创建已确认，保留局部隔离复用。当前只是把自制wx_execution成交/PnL复制到官方模型，诚实标记为持久化适配；核查既有Freqtrade执行/订单更新/盈亏骨架可实际复用的调用路径与差异，再做最小局部适配。不得只复制结果后宣称真实主引擎/独立对拍。优先约定成熟独立实现或已被接受实现；若没有可用现成入口，列本地源码/依赖查证和限制、最小适配选择，再施工，避免继续另造完整撮合系统。

交付新目录`接线路整改3`：实际before字节+SHA/diff、上述严格反例脚本/完整命令/退出码/全字段差异、复用真实调用证据、原26+8回归、完整18身份、非递归文件清单及READY_RETRY_3.md。不能覆盖旧Work或Codex证据，不改长期记忆。原始审查04输出可保留，不改其断言使已有问题被隐藏。

正式最新API及完整回滚仍待验，保留既有隔离预览；在本轮严格反例和API预览/回滚经Codex接受前不重启健康8787。前端不改，数据路不碰，无新下载/生产写入/正式回测/paper/实盘/FINAL绩效读取/外部通知。12GiB+增长及30GiB历史保护不改，113/88修订不重跑。
