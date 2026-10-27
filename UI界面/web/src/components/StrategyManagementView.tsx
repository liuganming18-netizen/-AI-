/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import {
  Sliders,
  Bell,
  Layers,
  ShieldAlert,
  GitBranch,
  FileCheck,
  CheckCircle2,
  AlertCircle,
  Database,
  GitCompare,
  Activity,
  Cpu
} from 'lucide-react';
import { useEndpoint } from '../api/useEndpoint';

/** 策略注册表低频只读刷新。 */
const REFRESH_MS = 15_000;

interface PaperContractEnvelope {
  ft_low4h_contract?: {
    strategy_version?: string | null;
    rule?: string | null;
    stoploss_s?: number | null;
    trail_d?: number | null;
    risk_budget?: number | null;
    fee_each_side?: number | null;
    slippage_each_side?: number | null;
    detail_timeframe?: string | null;
    tracking_timeframe?: string | null;
    config_valid?: boolean;
    config_error?: string | null;
  } | null;
  quality?: {
    status?: string | null;
    new_entry_gate?: { allow_new_entries?: boolean; reason?: string | null; gate_reason?: string | null } | null;
  } | null;
}

interface PaperDaemonEnvelope {
  item?: {
    signal_engine?: { status?: string | null } | null;
    market_data?: { status?: string | null } | null;
  } | null;
}

const paramPct = (v: unknown): string =>
  typeof v === 'number' && Number.isFinite(v) ? `${(Math.abs(v) * 100).toFixed(2)}%` : '未知';
const paramBps = (v: unknown): string =>
  typeof v === 'number' && Number.isFinite(v) ? `${(v * 10_000).toFixed(2)} bps` : '未知';

export const StrategyManagementView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'list' | 'rules' | 'deps' | 'versions'>('list');
  const [selectedStep, setSelectedStep] = useState<number>(2);

  const paperEnv = useEndpoint<PaperContractEnvelope>('/paper/accounts', REFRESH_MS);
  const daemonEnv = useEndpoint<PaperDaemonEnvelope>('/paper/execution_daemon/status?arm=3a', REFRESH_MS);

  const contract = paperEnv.data?.ft_low4h_contract ?? null;
  const gate = paperEnv.data?.quality?.new_entry_gate ?? null;
  const gateValue = gate?.allow_new_entries;
  const gateLabel = gateValue === true ? '开启' : gateValue === false ? '关闭' : '未知';
  const gateDetail = gateValue === undefined
    ? (paperEnv.error ? '读取失败' : paperEnv.loading ? '读取中…' : '接口未返回')
    : (gate?.reason ?? gate?.gate_reason ?? '服务端未提供原因');
  const marketDataStatus = daemonEnv.data?.item?.market_data?.status ?? null;
  const signalEngineStatus = daemonEnv.data?.item?.signal_engine?.status ?? null;
  const dependencySummary = daemonEnv.data
    ? `行情 ${marketDataStatus ?? '未知'} · 信号引擎 ${signalEngineStatus ?? '未知'}`
    : daemonEnv.error ? '读取失败' : daemonEnv.loading ? '读取中…' : '未知';
  const contractSummary = contract
    ? `止损 ${paramPct(contract.stoploss_s)} · 跟踪 ${contract.tracking_timeframe ?? '未知'} · 风险 ${paramPct(contract.risk_budget)}`
    : paperEnv.error ? '配置读取失败' : paperEnv.loading ? '配置读取中…' : '配置未知';
  const feeSlipSummary = contract
    ? `滑点单边 ${paramBps(contract.slippage_each_side)} · 手续费单边 ${paramBps(contract.fee_each_side)}`
    : paperEnv.error ? '配置读取失败' : paperEnv.loading ? '配置读取中…' : '配置未知';

  const steps = [
    {
      step: '01',
      title: '区间形成与冻结',
      desc: '威科夫筑底/吸筹区间识别，计算结构下轨 P0 与高低点边界，观察周期为 4h/1h。',
    },
    {
      step: '02',
      title: '放量突破确认',
      desc: '1h K线收盘突破区间上轨，成交量放大超过基线均量 1.5 倍，持仓量 (OI) 增量共振。',
    },
    {
      step: '03',
      title: '后续独立回踩确认',
      desc: '突破后出现独立回踩测试支撑，回踩不破结构止损位，在回踩结束时生成候选信号。',
    },
    {
      step: '04',
      title: '因果撮合与入场风控',
      desc: '确认时刻后第一根已闭合 15m bar 开盘价因果撮合；单笔单名风险限制 0.5% 账户资金。',
    },
    {
      step: '05',
      title: '止损与跟踪退出',
      desc: '初始止损 P0 或固定 20%，入场后结合 4h 最低价动态跟踪上移，只上移不下移。',
    },
    {
      step: '06',
      title: '失效与缺数据熔断',
      desc: '行情缺失、网络延迟超过 1 小时或触及 20% 最大回撤锁止时，自动停止新开仓并熔断。',
    },
  ];

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-neutral-950 p-4 gap-3 select-none text-neutral-200 ai-grid">
      {/* 1. 顶栏导航与 Tab 切换 */}
      <div className="flex flex-wrap items-center justify-between border-b border-neutral-800 pb-3 gap-3">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 shrink-0">
            <Sliders className="w-5 h-5 text-emerald-400 shrink-0" />
            <h1 className="text-xl font-bold text-neutral-100 tracking-wide ai-gradient-text whitespace-nowrap">策略管理</h1>
          </div>
          {/* 四个真实子页签：策略列表 ｜ 规则说明 ｜ 数据依赖 ｜ 版本对比 */}
          <div className="flex items-center gap-2 text-xs font-medium bg-[var(--ds-surface-2)] p-1 rounded-lg border border-[var(--ds-border-subtle)]">
            <button
              data-testid="strat-tab-list"
              onClick={() => setActiveTab('list')}
              className={`px-3 py-1 rounded transition-colors ${
                activeTab === 'list'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-neutral-400 hover:text-neutral-200'
              }`}
            >
              策略列表{activeTab === 'list' ? '（当前页）' : ''}
            </button>
            <span className="text-neutral-700">|</span>
            <button
              data-testid="strat-tab-rules"
              onClick={() => setActiveTab('rules')}
              className={`px-3 py-1 rounded transition-colors ${
                activeTab === 'rules'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-neutral-400 hover:text-neutral-200'
              }`}
            >
              规则说明{activeTab === 'rules' ? '（当前页）' : ''}
            </button>
            <span className="text-neutral-700">|</span>
            <button
              data-testid="strat-tab-deps"
              onClick={() => setActiveTab('deps')}
              className={`px-3 py-1 rounded transition-colors ${
                activeTab === 'deps'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-neutral-400 hover:text-neutral-200'
              }`}
            >
              数据依赖{activeTab === 'deps' ? '（当前页）' : ''}
            </button>
            <span className="text-neutral-700">|</span>
            <button
              data-testid="strat-tab-versions"
              onClick={() => setActiveTab('versions')}
              className={`px-3 py-1 rounded transition-colors ${
                activeTab === 'versions'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-neutral-400 hover:text-neutral-200'
              }`}
            >
              版本对比{activeTab === 'versions' ? '（当前页）' : ''}
            </button>
          </div>
        </div>

        <div className="text-xs text-neutral-400 font-mono">
          当前处于：{activeTab === 'list' ? '策略概览与注册列表' : activeTab === 'rules' ? '规则定义与计算口径' : activeTab === 'deps' ? '多周期数据流与质量协议' : '生产候选 vs 历史归档对比'}
        </div>
      </div>

      {/* 2. 状态指标卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="ds-card p-3 space-y-1 mb-0!">
          <span className="text-xs font-medium text-[var(--ds-text-4)]">策略状态</span>
          <div className="text-[20px] font-semibold font-mono leading-tight text-emerald-400 truncate" data-testid="strategy-runtime-version">{contract?.strategy_version ?? (paperEnv.loading ? '读取中…' : '未知')}</div>
          <span className="text-[11px] text-neutral-500">paper/accounts 返回的运行参数身份 · {contract?.rule ?? '规则未知'}</span>
        </div>
        <div className="ds-card p-3 space-y-1 mb-0!">
          <span className="text-xs font-medium text-[var(--ds-text-4)]">规则版本</span>
          <div className="text-[20px] font-semibold font-mono leading-tight text-neutral-200">{contract?.rule ?? (paperEnv.loading ? '读取中…' : '未知')}</div>
          <span className="text-[11px] text-neutral-500">{contractSummary}</span>
        </div>
        <div className="ds-card p-3 space-y-1 mb-0!">
          <span className="text-xs font-medium text-[var(--ds-text-4)]">策略计算周期</span>
          <div className="text-[20px] font-semibold font-sans leading-tight text-neutral-200">1h 确认 / 15m 执行</div>
          <span className="text-[11px] text-neutral-500">4h 最低价跟踪出场</span>
        </div>
        <div className="ds-card p-3 space-y-1 mb-0!">
          <span className="text-xs font-medium text-[var(--ds-text-4)]">数据依赖状态</span>
          <div className="text-[13px] font-semibold font-mono leading-tight text-neutral-200" data-testid="strategy-runtime-dependencies">{dependencySummary}</div>
          <span className="text-[11px] text-neutral-500">执行守护与信号引擎状态 · 缺失时显示未知</span>
        </div>
      </div>

      {/* 3. 主体内容：根据 activeTab 渲染完全独立的内容分支 */}
      {activeTab === 'list' && (
        <div data-testid="strategy-list-view" className="flex-1 flex flex-col gap-3 overflow-y-auto min-h-0">
          <div className="ds-card p-4 space-y-3 mb-0!">
            <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
              <h2 className="text-sm font-bold text-neutral-100 flex items-center gap-2">
                <Layers className="w-4 h-4 text-emerald-400" />
                策略概览与注册列表 (Strategy Catalog)
              </h2>
              <span className="text-xs font-mono text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/40">
                1 生产候选 · 4 历史归档
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-neutral-800 text-neutral-400 font-mono">
                    <th className="py-2 px-3">策略标识</th>
                    <th className="py-2 px-3">内核标识</th>
                    <th className="py-2 px-3">当前状态</th>
                    <th className="py-2 px-3">确认周期</th>
                    <th className="py-2 px-3">撮合周期</th>
                    <th className="py-2 px-3">止损模型</th>
                    <th className="py-2 px-3">单笔风险</th>
                    <th className="py-2 px-3">版本更新</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-850">
                  <tr className="bg-neutral-900/60 hover:bg-neutral-850 transition-colors">
                    <td className="py-2.5 px-3 font-mono font-bold text-emerald-400">{contract?.strategy_version ?? '未知'}</td>
                    <td className="py-2.5 px-3 font-mono text-neutral-200">WX_EVENT_TREND_V0_1</td>
                    <td className="py-2.5 px-3"><span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-800 font-semibold">生产候选 (Frozen)</span></td>
                    <td className="py-2.5 px-3 text-neutral-300 font-mono">1h 收盘</td>
                    <td className="py-2.5 px-3 text-neutral-300 font-mono">15m 因果</td>
                    <td className="py-2.5 px-3 text-neutral-300">止损 {paramPct(contract?.stoploss_s)} / {contract?.tracking_timeframe ?? '未知'} 跟踪</td>
                    <td className="py-2.5 px-3 font-mono text-neutral-300">{paramPct(contract?.risk_budget)} 账户资金</td>
                    <td className="py-2.5 px-3 font-mono text-neutral-500">2026-10-18</td>
                  </tr>
                  <tr className="bg-neutral-950/40 text-neutral-500 hover:bg-neutral-900/40 transition-colors">
                    <td className="py-2 px-3 font-mono">FT-LOW4H-3a</td>
                    <td className="py-2 px-3 font-mono">STRUCTURE_3A</td>
                    <td className="py-2 px-3"><span className="px-1.5 py-0.5 rounded text-[10px] bg-neutral-900 text-neutral-500 border border-neutral-800">只读归档 (Retired)</span></td>
                    <td className="py-2 px-3 font-mono">4h</td>
                    <td className="py-2 px-3 font-mono">15m</td>
                    <td className="py-2 px-3">固定止损 / 4h 跟踪</td>
                    <td className="py-2 px-3 font-mono">0.5%</td>
                    <td className="py-2 px-3 font-mono">2026-10-14</td>
                  </tr>
                  <tr className="bg-neutral-950/40 text-neutral-500 hover:bg-neutral-900/40 transition-colors">
                    <td className="py-2 px-3 font-mono">FT-LOW4H-3b / 3c / 3d</td>
                    <td className="py-2 px-3 font-mono">STRUCTURE_3B_3D</td>
                    <td className="py-2 px-3"><span className="px-1.5 py-0.5 rounded text-[10px] bg-neutral-900 text-neutral-500 border border-neutral-800">只读归档 (Retired)</span></td>
                    <td className="py-2 px-3 font-mono">4h</td>
                    <td className="py-2 px-3 font-mono">15m</td>
                    <td className="py-2 px-3">固定止损 / 4h 跟踪</td>
                    <td className="py-2 px-3 font-mono">0.5%</td>
                    <td className="py-2 px-3 font-mono">2026-10-14</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="ds-card p-4 space-y-2 mb-0!">
              <h3 className="font-semibold text-neutral-200 text-xs">执行引擎与风控边界</h3>
              <div className="space-y-1 text-xs text-neutral-300 leading-relaxed">
                <div>• 单名名义上限：30% 权益 (冻结上限)</div>
                <div>• 总多头名义上限：90% 权益 (冻结上限)</div>
                <div>• 最低可用现金预留：10%</div>
                <div>• 最大同时持仓数：3 仓 (冻结规则)</div>
                <div>• 最大账户回撤锁止：20%</div>
                <div data-testid="strategy-entry-gate">• 新开仓总闸：{gateLabel}（{gateDetail}）</div>
              </div>
            </div>
            <div className="ds-card p-4 space-y-2 mb-0!">
              <h3 className="font-semibold text-neutral-200 text-xs">策略因果时钟契约</h3>
              <div className="space-y-1 text-xs text-neutral-300 leading-relaxed font-mono">
                <div>• 确认时刻：1h Bar 收盘时刻 (bar_close_time)</div>
                <div>• 最早撮合：确认时刻后第一根已闭合 15m Bar (earliest_fill_ms)</div>
                <div data-testid="strategy-fee-slip">• 撮合成本：{feeSlipSummary}</div>
                <div>• 数据隔离：实时库 live_signals.sqlite3 · 纸面账本 paper_*.sqlite3</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'rules' && (
        <div data-testid="strategy-rules-view" className="flex-1 flex flex-col lg:flex-row gap-3 overflow-y-auto lg:overflow-hidden min-h-0">
          {/* 左栏：策略列表 + 查看范围 + 手机提醒 */}
          <div className="w-full lg:w-56 shrink-0 flex flex-col gap-3 lg:overflow-y-auto">
            <div className="ds-card p-3.5 space-y-2 text-xs mb-0!">
              <h2 className="font-semibold text-neutral-200 pb-1.5 border-b border-neutral-800">策略列表</h2>
              <div className="p-2.5 rounded bg-neutral-900 border border-emerald-900/40 space-y-1">
                <div className="font-bold text-emerald-400">{contract?.strategy_version ?? '未知'}</div>
                <div className="text-[11px] text-neutral-300">威科夫因子共振突破趋势 (WX_EVENT_TREND_V0_1)</div>
                <div className="text-[10px] font-mono text-neutral-500">单笔风险: {paramPct(contract?.risk_budget)} · {contract?.detail_timeframe ?? '未知'} 因果撮合</div>
              </div>
              <div className="p-2 rounded bg-neutral-900/50 border border-neutral-800 text-[11px] text-neutral-400">
                <div className="font-medium text-neutral-400">已退役旧策略族：</div>
                <div className="text-[10px] text-neutral-500">3a, 3b, 3c, 3d (只读归档)</div>
              </div>
            </div>

            <div className="ds-card p-3.5 space-y-2 text-xs flex-1 mb-0!">
              <h2 className="font-semibold text-neutral-200 pb-1.5 border-b border-neutral-800">风控边界与闸门</h2>
              <div className="space-y-1.5 text-neutral-300 text-[11px]">
                <div>• 单名名义上限：30% 权益 (冻结上限)</div>
                <div>• 总多头名义上限：90% 权益 (冻结上限)</div>
                <div>• 最低可用现金预留：10%</div>
                <div>• 最大同时持仓数：3 仓 (冻结规则)</div>
                <div>• 最大账户回撤锁止：20%</div>
                <div>• 新开仓总闸：{gateLabel}（{gateDetail}）</div>
              </div>
            </div>

            <div className="ds-card p-3.5 space-y-1.5 text-xs shrink-0 mb-0!">
              <div className="flex items-center gap-1.5 font-semibold text-neutral-200">
                <Bell className="w-3.5 h-3.5 text-neutral-400" />
                <span>告警通知</span>
              </div>
              <div className="text-[11px] text-neutral-400 font-mono">
                <div>渠道：Telegram / 飞书 Hook</div>
                <div className="text-emerald-500/80 font-sans">本地审计已启用 · 日志留存</div>
              </div>
            </div>
          </div>

          {/* 中栏：6 阶段逻辑序列 */}
          <div className="w-full lg:flex-1 flex flex-col gap-3 overflow-hidden min-w-0 min-h-0">
            <div className="ds-card flex-1 flex flex-col overflow-hidden p-0! mb-0!">
              <div className="px-4 py-2.5 flex items-center justify-between border-b border-neutral-800 bg-neutral-900/60">
                <h2 className="text-sm font-semibold text-neutral-100 flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-emerald-400" />
                  策略条件序列 · 6 阶段完整执行链条
                </h2>
                <span className="text-[10px] text-neutral-500 font-mono">严格因果 · 防前视</span>
              </div>

              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {steps.map((item, idx) => {
                  const isSelected = selectedStep === idx + 1;
                  return (
                    <div
                      key={item.step}
                      data-testid={`stage-step-${item.step}`}
                      onClick={() => setSelectedStep(idx + 1)}
                      className={`p-3 rounded border transition-colors cursor-pointer ${
                        isSelected
                          ? 'bg-emerald-950/30 border-emerald-500/60'
                          : 'bg-neutral-900/60 border-neutral-800 hover:bg-neutral-900'
                      }`}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-mono font-bold text-xs text-emerald-400">{item.step}</span>
                        <h3 className="font-semibold text-xs text-neutral-100">{item.title}</h3>
                      </div>
                      <p className="text-xs text-neutral-400 pl-6 leading-relaxed">
                        {item.desc}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="ds-card p-3.5 space-y-1.5 text-xs shrink-0 mb-0!">
              <h3 className="font-semibold text-neutral-200 flex items-center gap-2">
                <span>当前阶段详情与计算口径</span>
                <span className="text-[10px] text-emerald-400 font-mono font-normal">
                  ({steps[selectedStep - 1]?.title})
                </span>
              </h3>
              <div className="text-[11px] text-neutral-400 space-y-1 leading-relaxed">
                <div className="font-mono text-neutral-300">
                  字段: open, high, low, close, volume, taker_buy_volume, oi, funding_rate
                </div>
                <div className="text-neutral-500">
                  • 撮合纪律：首根 open_time &gt;= 确认时刻 + 15m 的已闭合 15m bar 开盘价；绝不使用旧价回填。
                </div>
              </div>
            </div>
          </div>

          {/* 右栏：参数白名单与执行环境 */}
          <div className="w-full lg:w-72 shrink-0 flex flex-col gap-3 lg:overflow-y-auto">
            <div className="ds-card p-3.5 space-y-2 text-xs mb-0!">
              <h3 className="font-semibold text-neutral-200 pb-1 border-b border-neutral-800">参数白名单（FT-LOW4H-GRID）</h3>
              <div className="space-y-1.5 text-[11px] text-neutral-300 font-mono">
                <div>• 初始止损 (s)：5%, 10%, 15%, 20%</div>
                <div>• 跟踪距离 (d)：5%, 10%, 15%, 20%</div>
                <div>• 突破量比 (vs)：0.4, 0.6, 0.8</div>
                <div>• 振幅门槛 (z)：0.2, 0.4, 0.6</div>
                <div>• 参数组总数：4 × 4 × 9 = 144 组</div>
              </div>
            </div>

            <div className="ds-card p-3.5 space-y-2 text-xs flex-1 mb-0!">
              <h3 className="font-semibold text-neutral-200 pb-1 border-b border-neutral-800">止损与出场机制</h3>
              <div className="space-y-2 text-[11px] text-neutral-400">
                <div>
                  <span className="text-neutral-200 font-medium">初始止损价：</span>
                  <span className="font-mono text-emerald-400 block">成交价 × (1 − s)</span>
                </div>
                <div>
                  <span className="text-neutral-200 font-medium">跟踪候选价：</span>
                  <span className="font-mono text-emerald-400 block">最近已收盘 4h 最低价 × (1 − d)</span>
                </div>
                <div>
                  <span className="text-neutral-200 font-medium">生效止损价：</span>
                  <span className="font-mono text-emerald-400 block">max(初始止损, 前次止损, 候选价)</span>
                </div>
                <div className="text-neutral-500 border-t border-neutral-800 pt-1">
                  • 单调不减原则：止损只上移不下移，触及出场按因果成交，不保证滑点。
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 2. 数据依赖 Tab：真实呈现数据流体系 */}
      {activeTab === 'deps' && (
        <div data-testid="strategy-deps-view" className="flex-1 flex flex-col gap-3 overflow-y-auto min-h-0">
          <div className="ds-card p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
              <h2 className="text-sm font-bold text-neutral-100 flex items-center gap-2">
                <Database className="w-4 h-4 text-emerald-400" />
                统一只读行情库与实时流数据源依赖清单
              </h2>
              <span className="text-xs font-mono text-neutral-400">存储路径: 数据/市场数据/db/market_v2.sqlite3</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1">
                <div className="font-bold text-neutral-200 flex items-center justify-between">
                  <span>多周期真实 K 线 (candles)</span>
                  <span className="text-[10px] text-emerald-400 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800/60">核心依赖</span>
                </div>
                <div className="text-xs text-neutral-400">包含 15m, 1h, 4h, 1d 完整闭合 K 线；入库标的 4,385 个组合，总数 114,950,812 根。</div>
                <div className="text-[10px] font-mono text-neutral-500 pt-1">更新延迟: &lt; 15s · 来源: 币安合约/现货</div>
              </div>

              <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1">
                <div className="font-bold text-neutral-200 flex items-center justify-between">
                  <span>持仓量历史 (oi_history)</span>
                  <span className="text-[10px] text-emerald-400 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800/60">因子共振</span>
                </div>
                <div className="text-xs text-neutral-400">币安 USD-M 合约全标的未平仓合约量 (OI)；入库 723,156 行，用于识别资金建仓与突破有效性。</div>
                <div className="text-[10px] font-mono text-neutral-500 pt-1">更新频率: 5m 桶 · 缺失时严禁补 0</div>
              </div>

              <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1">
                <div className="font-bold text-neutral-200 flex items-center justify-between">
                  <span>资金费率结算 (funding_history)</span>
                  <span className="text-[10px] text-emerald-400 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800/60">精确结算</span>
                </div>
                <div className="text-xs text-neutral-400">真实历史结算费率与 Mark Price；入库 2,106,815 行；持仓期间按真实事件扣费，绝不固定 8h 计算。</div>
                <div className="text-[10px] font-mono text-neutral-500 pt-1">方向保留: 负费率如实保留符号</div>
              </div>

              <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1">
                <div className="font-bold text-neutral-200 flex items-center justify-between">
                  <span>实时行情报价 (realtime_quotes)</span>
                  <span className="text-[10px] text-emerald-400 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800/60">估值专用</span>
                </div>
                <div className="text-xs text-neutral-400">覆盖 814 个标的最新买卖价与 Mark 价格；新鲜度要求价格时点与心跳同时成立。仅供估值，不作为成交价。</div>
                <div className="text-[10px] font-mono text-neutral-500 pt-1">时效阈值: 300s · 超时回退闭合收盘价</div>
              </div>

              <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1">
                <div className="font-bold text-neutral-200 flex items-center justify-between">
                  <span>合约交易规范 (symbol_pool)</span>
                  <span className="text-[10px] text-emerald-400 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800/60">步进取整</span>
                </div>
                <div className="text-xs text-neutral-400">包含 price_step, qty_step, min_notional, max_qty 合约规格，开仓与止损严格向下/向上保守取整。</div>
                <div className="text-[10px] font-mono text-neutral-500 pt-1">规格来源: 币安 exchangeInfo 权威目录</div>
              </div>

              <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1">
                <div className="font-bold text-neutral-200 flex items-center justify-between">
                  <span>冷层归档数据 (parquet)</span>
                  <span className="text-[10px] text-neutral-400 bg-neutral-800 px-1.5 py-0.5 rounded border border-neutral-700">长周期分析</span>
                </div>
                <div className="text-xs text-neutral-400">通过 DuckDB + PyArrow 读取的归档 Parquet 冷层数据，跨度自 2024 年至今，支持长周期历史回溯。</div>
                <div className="text-[10px] font-mono text-neutral-500 pt-1">存储路径: 数据/市场数据/parquet</div>
              </div>
            </div>

            <div className="p-3 rounded bg-neutral-900/40 border border-neutral-800 text-xs text-neutral-400 space-y-1">
              <div className="font-bold text-neutral-300">数据质量协议与防前视纪律：</div>
              <div>1. 接口只读行情库，前端轮询绝不触发外部网络爬取；</div>
              <div>2. 任何缺失数据以真实 NULL/缺失展示，严禁伪造连续性或补 0；</div>
              <div>3. 成交价格严格锚定已收盘的 15m K 线，坚决阻断任何未收盘未来数据。</div>
            </div>
          </div>
        </div>
      )}

      {/* 3. 版本对比 Tab：对比生产候选策略与历史归档版本 */}
      {activeTab === 'versions' && (
        <div data-testid="strategy-versions-view" className="flex-1 flex flex-col gap-3 overflow-y-auto min-h-0">
          <div className="ds-card p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-neutral-800 pb-2">
              <h2 className="text-sm font-bold text-neutral-100 flex items-center gap-2">
                <GitCompare className="w-4 h-4 text-emerald-400" />
                策略版本架构演进与差异对比矩阵
              </h2>
              <span className="text-xs font-mono text-neutral-400">当前唯一生产候选: WX_EVENT_TREND_V0_1</span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left border-collapse">
                <thead>
                  <tr className="border-b border-neutral-800 bg-neutral-900/60 text-neutral-400 font-mono">
                    <th className="p-2.5">维度 / 特性</th>
                    <th className="p-2.5 text-emerald-400">当前策略：WX_EVENT_TREND_V0_1</th>
                    <th className="p-2.5 text-amber-400">历史策略族：F_WX_v2.2（3a–3d）</th>
                    <th className="p-2.5">演进原因与改进效果</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-800/60 font-sans">
                  <tr>
                    <td className="p-2.5 font-bold text-neutral-300">入场信号逻辑</td>
                    <td className="p-2.5 text-neutral-200">1h K 线收盘确认突破 + 回踩不破结构</td>
                    <td className="p-2.5 text-neutral-400">唐奇安通道突破触发（四臂不同周期）</td>
                    <td className="p-2.5 text-neutral-400">增加回踩确认，假突破过滤率大幅提升</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-bold text-neutral-300">止损与退出机制</td>
                    <td className="p-2.5 text-neutral-200">
                      固定 5%~20% 初始止损 + 4h 最低价动态跟踪上移
                    </td>
                    <td className="p-2.5 text-neutral-400">固定 3×ATR 止损 + 720h 超时强平</td>
                    <td className="p-2.5 text-neutral-400">取消 240/720h 机械退出，让利润充分奔跑</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-bold text-neutral-300">资金与风控模型</td>
                    <td className="p-2.5 text-neutral-200">单笔 0.5% 账户风险预算，仓位随止损距离反推</td>
                    <td className="p-2.5 text-neutral-400">四臂共享 5,000U 本金，单臂独立固定分配</td>
                    <td className="p-2.5 text-neutral-400">实现真正的等风险仓位加权，避免尾部标的过重</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-bold text-neutral-300">撮合时钟纪律</td>
                    <td className="p-2.5 text-neutral-200">首根 open &gt;= 确认时刻 + 15m 的已收盘 15m 开盘价</td>
                    <td className="p-2.5 text-neutral-400">依赖最近已闭合 bar（早期存在前视漂移）</td>
                    <td className="p-2.5 text-neutral-400">彻底阻断未来数据与时间穿越，因果可复现</td>
                  </tr>
                  <tr>
                    <td className="p-2.5 font-bold text-neutral-300">生产实盘状态</td>
                    <td className="p-2.5 text-emerald-400 font-medium">研究期 144 组网格完备验证中</td>
                    <td className="p-2.5 text-neutral-400">已退役并作为只读沙盒环境保留</td>
                    <td className="p-2.5 text-neutral-400">未经过严格前向验证前绝不下发主网订单</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="p-3 rounded bg-neutral-900 border border-neutral-800 space-y-1.5 text-xs text-neutral-400">
              <div className="font-bold text-neutral-200">版本指纹与代码溯源：</div>
              <div className="font-mono text-[11px] text-neutral-300 space-y-0.5">
                <div>• 当前核心实现文件：strategies/wx_event_trend.py / strategies/wx_execution.py</div>
                <div>• 网格实验配置基准：evidence/FT-LOW4H-GRID-01/ (144 组全覆盖)</div>
                <div>• 历史归档清单记录：evidence/Work_闭环整改_2026-10-15/archive_manifest.json</div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
