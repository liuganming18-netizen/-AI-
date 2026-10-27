# Work 新窗口：使用已独立复审的 v4 执行成交量分批修复

用户本轮明确授权“派发任务”。Codex 已完成 v4 独立复审：16/16 SHA 一致、17/17 真实CLI隔离回归通过、v3生产条件负对照确失败、真实库身份JSON读回匹配且实际schema改变仍被拒。01_v4独立复审.json及v4独立回归日志.txt为当前证据。候选限定通过，不代表生产数据完成验收；无需重复询问本范围生产修复许可。

## 复用与路径
先完整读取本合同、同目录01_v4独立复审.json，再读取下列81原合同、80批准IDs与原始计划；本合同仅把批准工具改成v4，其余边界不变。
原合同目录：/Users/boos/.codex/worktrees/a112/加密货币趋势判断系统/策略工程落地_2026-10-10
原合同：81_Work新窗口_成交量分批生产修复与验收_2026-10-11.md
原批准：80_成交量第三版候选独立审核_2026-10-11_限定通过.json
生产库：/Users/boos/.codex/worktrees/90b1/加密货币趋势判断系统/data/market.sqlite3
原始全63冻结计划：/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/OKX成交量执行验收整改_2026-10-11/05_生产冻结计划_v3.json
原始计划SHA：f30640f5c1e67311550adee27a7fb689561f85c13d4457d0a266d1bfc4750a6c
本次工具：/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/OKX成交量生产修复与验收_2026-10-11/工具v4候选/
新输出：/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/OKX成交量v4分批执行_2026-10-11/
保留原3版、v4候选、原件、数据与断点。复用04/09现成工具；内部TOOL_VERSION保持okx-volfix-rev3兼容已冻结计划，本次版本由下列SHA锁定，不临场升版或修改工具。

## 执行与验收
1. 生产前核对工具SHA、原始计划SHA、库身份、固定scope、63批准IDs/122543预期行、原件sidecar、无第二历史writer。每批实测free>=12GiB、实际增长峰值、WAL<=2GiB和/api/health；健康collector/API/前端继续。守卫暂停保留同plan/journal续跑，不能关闭守卫强跑。
2. 先4单根旧source 1428835/1927619/2012423/1859283；独立仅4源subset plan-file与journal。dry-run冻结源集核对正确再apply，09 --phase accept +journal必须PASS/rc0后才进入下一阶段。
3. CRO旧source4125198：独立仅1源plan-file与journal，122481行，batch-size 2000。新版本逐根volume取原件volCcy；121696真实变化与785零量单列；首次repair_ms/available_at准确。完毕完整验收PASS/rc0。
4. 余58源：显式批准IDs减上述5源，仅58单根，独立plan-file/journal，逐批PASS。不得默认动态build_plan扩大范围。复用plan-file时only-sources不会重新过滤，禁止把全63计划传入4源阶段；每个subset的new_sha与批准全计划一致。
5. 总验收用原始全63计划及3阶段journal的首次repair_ms完整对账；每来源newsource.imported_at==新versions.available_at。全部旧sources/旧versions字段（包括available_at）前后冻结不变；新source metadata/摘要/血缘与原件一致；当前heads全字段逐根0DIFF；正确head总计122543。新增修订来源、新versions、按完整键条件切head是唯一许可生产变更。
6. 保持原15m549/549与固定cutoff验收口径、2887官方不可得单列；113未证数据OPEN，不修不删，不伪称整体语义全通过。并发第三方head不得覆盖。工具需改/身份漂移/原件冲突/FAIL均停生产writer报审。
7. 禁止新增历史下载（含1m/5m/指数/OI/funding/mark）、回测、前端施工、DDL/DELETE/DROP/VACUUM/TRUNCATE、改旧sourceSHA、生产回退演练、不读写MEMORY。不要启动无关任务。

交付各阶段完整CLI日志、冻结plan/journal/out/09、前后全字段快照、原件对账、健康容量WAL峰值与collector连续性、总122543正确heads、113OPEN清单、非递归SHA。成功状态READY_FOR_CODEX_FINAL_ACCEPTANCE，由Codex独立终验；失败状态含实际原因、已提交范围与可恢复断点。Work结束后的下一任务在新窗口安排。

## 当前批准工具SHA256
- volccy_common.py: ce5a1ef33a8b86d9f685b900495c57228203c130a13f3f06f40c34e6776a76fb
- 04_okx_volume_fix_v3.py: d0e8fc4a3069dbae538c05fe1eac1933a1193494d2216618f334c97024ddbb9c
- 06_okx_volume_rollback_v3.py: 3415899cbd9be0a98e91902fc1c517764bab4749bcab2bf08e16cef80da2530f
- 09_全字段不变式验收_v3.py: f73a96e7c71572406bad5afaf5207624bd656b79cc063d8c9be9d7c2cfc250e1

## 封闭批准旧source IDs
1428835,1429167,1429168,1429393,1429394,1429395,1429397,1429398,1429399,1429400,1429401,1429402,1429403,1429404,1429405,1429406,1429407,1429408,4125198,1927619,1927624,1927625,1927626,1927627,1927628,1927629,1927630,1927631,2012423,2032880,2034722,2035819,2042716,2070506,2070507,2070508,2084905,2084908,2084909,2084910,2084911,2084912,2084913,2084914,2084915,2084916,2084917,1859283,1861400,1861401,1861402,1861729,1861932,1861934,1861935,1861936,1861937,1861938,1861939,1861940,1861941,1861942,1861943
