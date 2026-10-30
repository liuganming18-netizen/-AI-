# 修改文件

- `UI界面/web/src/api/labBacktest.ts`
- `UI界面/web/src/api/liveTrading.ts`
- `UI界面/web/src/components/InlineExpandedChart.tsx`
- `UI界面/web/src/components/LabChartPanel.tsx`
- `UI界面/web/src/components/LightweightChart.tsx`
- `框架/backend/adapter.py`
- `框架/backend/backtest_api.py`
- `框架/backend/backtest_chart.py`
- `框架/backend/live_api.py`
- `框架/backend/tests/test_kline_event_fullchain.py`

## 行为范围

- 后端增加回测事件只读映射/市场已平仓纸面成交只读查询。
- 前端回测区间与事件详情/时间线联动、图上命中区；市场页面限制四类交易事件并按真实字段取数。
- 新增 4 个定向 Python 用例及本目录实际数据、浏览器与测试证据。
- 未修改策略参数、PIT 门槛、交易规则、生产 SQLite/正式 Parquet/ZIP/账本。
