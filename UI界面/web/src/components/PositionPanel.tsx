/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from 'react';
import { TokenIcon } from './TokenIcon';
import type {
  LiveAccountView,
  PaperAccountView,
  PaperPendingView,
  PaperPositionView,
  ExecutionDaemonView,
} from '../api/liveTrading';
import {
  Wallet,
  Shield,
  ArrowRight,
  TrendingUp,
  TrendingDown,
  Lock,
  History,
  AlertCircle,
  Clock,
  Layers,
  Play,
  Pause,
  X,
  Cpu,
} from 'lucide-react';

interface PositionPanelProps {
  /** 四臂纸面账户（服务端账本） */
  paperAccounts: PaperAccountView[];
  /** 所有臂的未平仓持仓（服务端账本） */
  paperPositions: PaperPositionView[];
  /**
   * 所有臂的**未来待成交挂单**（服务端登记，真实进程时钟推进）。
   * 因果纪律：应成交时刻未到 ⇒ 等；错过成交时刻 ⇒ 服务端拒单，不用旧价回填。
   */
  paperPending?: PaperPendingView[];
  /** 纸面账本不可用时的真实原因 */
  paperMissingReason: string | null;
  /**
   * 服务端纸面账本的口径说明（含**实时起点水位线**：为什么自动持仓可能是空的）。
   * 空持仓时必须展示，否则会被误读成「没接通」。
   */
  paperNote?: string | null;
  /** 服务端**新开仓总闸**（fail-closed）；null = 后端未返回。 */
  newEntryGate?: { allowNewEntries: boolean; reason: string | null; exitsFeesFundingHealthUnaffected: boolean } | null;
  /** 旧版/未验收账本标记（后端只读追加）。 */
  legacyLedger?: { preserved: boolean; verificationStatus: string | null; label: string | null; excludedFromOfficial: boolean } | null;
  /** 实盘只读账户快照 */
  liveAccounts: LiveAccountView[];
  /** 实盘账户为空时的真实原因（未配置凭据 / worker 未运行） */
  liveMissingReason: string | null;
  /** 是否有下单在途 */
  orderBusy: boolean;
  onSelectTokenSymbol: (symbol: string) => void;
  onClosePosition: (arm: string, positionId: number) => void;
  onOpenQuickPosition: (direction: 'long' | 'short') => void;
  /** 下单/平仓的真实回执或服务端拒绝原因 */
  orderNotice?: string | null;
  // ── WORK-04 退役：Paper 未接新共享核心 ⇒ NOT_CONNECTED（fail-closed）──────────
  /** 为真时：本面板不展示旧 3a–3d 账户，且**开/平仓按钮一律禁用**。 */
  paperNotConnected?: boolean;
  /** 新共享核心策略 id（用于文案：为什么当前没有可运行账户）。 */
  paperNewCore?: string | null;
  /** 已退役旧策略注册表键（`3a/3b/3c/3d/A/B`）。 */
  paperRetiredKeys?: string[];
  /** 切换模拟实盘开始 / 暂停 */
  onToggleGate?: (action?: 'toggle' | 'start' | 'pause') => Promise<boolean>;
  /** 是否处于运行状态 */
  isGateRunning?: boolean;
  /** 执行守护与信号引擎状态 */
  executionDaemon?: ExecutionDaemonView | null;
}

/** 数字格式化：null / 非有限值一律 '—'，不显示 0 冒充真实值。 */
const fmt = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtUsd = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `$${fmt(v, digits)}`;

const fmtSignedUsd = (v: number | null | undefined, digits = 2): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}$${fmt(Math.abs(v), digits)}`;
};

const fmtPriceSmart = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 5 : 6;
  return `$${fmt(v, digits)}`;
};

const fmtCompactUtcMs = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms <= 0) return '—';
  const d = new Date(ms);
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const da = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${mo}-${da} ${hh}:${mm}Z`;
};

/** 开仓时间精确到秒并明确标注时区：YYYY-MM-DD HH:mm:ss (UTC+8) */
const fmtDateTimeSeconds = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms <= 0) return '—';
  const d = new Date(ms);
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const da = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${y}-${mo}-${da} ${hh}:${mm}:${ss} (UTC+8)`;
};

/**
 * 挂单状态码 → 中文文案（UI-PENDING-01）。
 *
 * **只做翻译，不改语义**：状态码原样保留在行内 `data-status` 与 `font-mono` 处，
 * 便于与账本 `paper_pending.status` 逐条核对；未知状态码原样显示，不猜测。
 * 契约里 `pending_orders()` 只取 `status='pending'`，其余三态属防御性映射。
 */
const PENDING_STATUS: Record<string, string> = {
  pending: '等待成交',
  filled: '已成交',
  rejected: '已拒单',
  expired: '已过期',
};

/** 挂单来源 → 文案（`origin` 为服务端字段，缺失即「未标注」，不推断）。 */
const pendingOriginText = (origin: string | null): string => {
  if (origin === 'signal') return '实时信号触发';
  if (origin === 'manual') return '页面手动请求';
  return origin ? origin : '未标注';
};

/** 实盘状态 → 展示文案与配色。**区分**「已连接但无持仓」与「未配置 / 鉴权失败 / 数据陈旧」。 */
const LIVE_STATUS: Record<string, { text: string; cls: string }> = {
  connected: { text: '已连接', cls: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' },
  connected_no_positions: {
    text: '已连接 · 无持仓',
    cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25',
  },
  not_configured: { text: '未配置凭据', cls: 'bg-neutral-800 text-neutral-400 border-neutral-700' },
  auth_failed: { text: '鉴权失败', cls: 'bg-rose-500/20 text-rose-400 border-rose-500/30' },
  error: { text: '读取失败', cls: 'bg-rose-500/20 text-rose-400 border-rose-500/30' },
  reader_unavailable: { text: '读取器不可用', cls: 'bg-amber-500/20 text-amber-400 border-amber-500/30' },
  stale: { text: '数据陈旧', cls: 'bg-amber-500/20 text-amber-400 border-amber-500/30' },
};

export const PositionPanel: React.FC<PositionPanelProps> = ({
  paperAccounts,
  paperPositions,
  paperPending,
  paperMissingReason,
  paperNote,
  newEntryGate,
  legacyLedger,
  liveAccounts,
  liveMissingReason,
  orderBusy,
  onSelectTokenSymbol,
  onClosePosition,
  onOpenQuickPosition,
  orderNotice,
  paperNotConnected = false,
  paperNewCore = null,
  paperRetiredKeys = [],
  onToggleGate,
  isGateRunning = false,
  executionDaemon = null,
}) => {
  const [selectedArm, setSelectedArm] = useState<'ALL' | string>('ALL');
  const [dismissedNotice, setDismissedNotice] = useState<string | null>(null);
  const [expandedStops, setExpandedStops] = useState<Record<number, boolean>>({});
  const [stopsHistory, setStopsHistory] = useState<Record<number, any[]>>({});
  const [loadingStops, setLoadingStops] = useState<Record<number, boolean>>({});
  const [stopsError, setStopsError] = useState<Record<number, string | null>>({});

  const toggleStopHistory = async (posId: number, arm: string) => {
    const nextState = !expandedStops[posId];
    setExpandedStops(prev => ({ ...prev, [posId]: nextState }));
    if (nextState) {
      setLoadingStops(prev => ({ ...prev, [posId]: true }));
      setStopsError(prev => ({ ...prev, [posId]: null }));
      try {
        const res = await fetch(`/api/v1/paper/stops/${posId}?arm=${arm}`);
        if (!res.ok) {
          const errData = await res.json().catch(() => null);
          const errMsg = errData?.missing_reason || `请求失败 (${res.status} ${res.statusText})`;
          setStopsError(prev => ({ ...prev, [posId]: errMsg }));
          setStopsHistory(prev => ({ ...prev, [posId]: [] }));
          return;
        }
        const data = await res.json();
        setStopsHistory(prev => ({ ...prev, [posId]: data?.items || [] }));
      } catch (err: any) {
        console.error('Fetch stops history failed', err);
        setStopsError(prev => ({ ...prev, [posId]: err?.message || '网络连接失败' }));
      } finally {
        setLoadingStops(prev => ({ ...prev, [posId]: false }));
      }
    }
  };

  const paperConnected = paperAccounts.length > 0 && !paperMissingReason;
  const showNotice = orderNotice && orderNotice !== dismissedNotice;

  /** WORK-04：Paper 未接新核心 ⇒ 禁止新开仓 / 真实下单（按钮禁用 + 明确文案）。 */
  const notConnected = paperNotConnected || !paperConnected;
  /**
   * 服务端新开仓总闸关闭 ⇒ 手工开仓同样会被拒。
   *
   * 后端 `paper_engine` 的闸门**同时覆盖自动 / 手工 / 挂单三条路径**（见
   * `new_entry_gate()` 处注释），暂停时 `POST /paper/open` 一律返回 `gate_blocked`。
   * 因此按钮必须跟着变灰 —— 否则界面写着「已暂停」，旁边却亮着一个能点的「开多」，
   * 点下去才被服务端拒绝，等于把一次注定失败的请求伪装成可用操作。
   */
  const gateBlocked = !isGateRunning;
  const executionDisabled = notConnected || gateBlocked;
  /** 禁用原因（决定按钮悬停提示，避免三种原因都写同一句话）。 */
  const disabledReason = notConnected
    ? '模拟实盘 NOT_CONNECTED：已禁止新开仓与真实下单'
    : '实盘模拟已暂停（服务端阻断新开仓）：先点上方「已暂停」启动，才允许开仓';

  /** 共用本金口径（生产规则：`docs/单策略施工授权与待核对问题_2026-10-14.md` 第 7 行
   *  「模拟实盘统一为一个账户，总资金 5,000 USDT」）。
   *  3a–3d 是同一主策略的四种入场类型（Spring / 温和突破 / 过热突破后回踩），
   *  **共用这一份本金**，不是四个各 5,000 的账户 ⇒ 本金只能算一次，
   *  每个臂只贡献「相对本金的变动」。之前直接 reduce 求和，等于把 5,000 数了四遍。 */
  const seedAll = paperAccounts.length > 0 ? paperAccounts[0].initialEquity : 0;
  const deltaEquity = (x: (typeof paperAccounts)[number]) =>
    (x.equity ?? x.initialEquity) - x.initialEquity;
  const deltaCash = (x: (typeof paperAccounts)[number]) => x.cash - x.initialEquity;

  /** 全局四臂合计（始终挂在 data-cash / data-equity 等属性上，确保验收脚本兼容）。 */
  const totalEquityAll = paperConnected
    ? seedAll + paperAccounts.reduce((a, x) => a + deltaEquity(x), 0)
    : null;
  const totalCashAll = paperConnected
    ? seedAll + paperAccounts.reduce((a, x) => a + deltaCash(x), 0)
    : null;
  const allPendingList = paperPending ?? paperAccounts.flatMap((a) => a.pending);

  /** 当前选中视图下的账户集合（ALL = 全部 4 臂汇总，否则为指定单臂）。 */
  const scopedAccounts = useMemo(
    () =>
      selectedArm === 'ALL'
        ? paperAccounts
        : paperAccounts.filter((a) => a.arm === selectedArm),
    [paperAccounts, selectedArm],
  );

  const scopedPositions = useMemo(
    () =>
      selectedArm === 'ALL'
        ? paperPositions
        : paperPositions.filter((p) => p.arm === selectedArm),
    [paperPositions, selectedArm],
  );

  const scopedPending = useMemo(() => {
    if (selectedArm === 'ALL') return allPendingList;
    const acc = paperAccounts.find((a) => a.arm === selectedArm);
    return acc ? acc.pending : [];
  }, [allPendingList, paperAccounts, selectedArm]);

  const viewStats = useMemo(() => {
    if (!paperConnected || scopedAccounts.length === 0) {
      return {
        initialEquity: null,
        equity: null,
        cash: null,
        usedMargin: null,
        grossNotional: null,
        unrealizedPnl: null,
        realizedPnl: null,
        feesPaid: null,
        fundingPaid: null,
        maxDrawdownPct: null,
        anyDrawdownLocked: false,
        nOrders: null,
        nRejected: null,
        nClosed: null,
        paramsSource: null as string | null,
      };
    }
    // 本金共用：只算一次（各臂是同一份 5,000 的不同入场类型，不是并列账户）。
    const seed = scopedAccounts.length > 0 ? scopedAccounts[0].initialEquity : 0;
    const initialEquity = seed;
    const equity = seed + scopedAccounts.reduce((s, a) => s + deltaEquity(a), 0);
    const cash = seed + scopedAccounts.reduce((s, a) => s + deltaCash(a), 0);
    const usedMargin = scopedAccounts.reduce((s, a) => s + a.usedMargin, 0);
    const grossNotional = scopedAccounts.reduce((s, a) => s + a.grossNotional, 0);
    const unrealizedPnl = scopedAccounts.reduce((s, a) => s + a.unrealizedPnl, 0);
    const realizedPnl = scopedAccounts.reduce((s, a) => s + a.realizedPnlTotal, 0);
    const feesPaid = scopedAccounts.reduce((s, a) => s + a.feesPaidTotal, 0);
    const fundingPaid = scopedAccounts.reduce((s, a) => s + a.fundingPaidTotal, 0);
    const maxDrawdownPct = Math.max(...scopedAccounts.map((a) => (a.drawdown ?? 0) * 100));
    const anyDrawdownLocked = scopedAccounts.some((a) => a.drawdownLocked);
    const nOrders = scopedAccounts.reduce((s, a) => s + a.nOrders, 0);
    const nRejected = scopedAccounts.reduce((s, a) => s + a.nRejected, 0);
    const nClosed = scopedAccounts.reduce((s, a) => s + (a.nClosed || 0), 0);
    // 参数来源：直接取自服务端（逐臂可能不同）；用于暴露「是否走了冻结视图外的兜底」。
    const sources = Array.from(new Set(scopedAccounts.map((a) => a.paramsSource).filter((v): v is string => !!v)));
    const paramsSource = sources.length > 0 ? sources.join(' / ') : null;
    return {
      initialEquity,
      equity,
      cash,
      usedMargin,
      grossNotional,
      unrealizedPnl,
      realizedPnl,
      feesPaid,
      fundingPaid,
      maxDrawdownPct,
      anyDrawdownLocked,
      nOrders,
      nRejected,
      nClosed,
      paramsSource,
    };
  }, [paperConnected, scopedAccounts]);

  const modelVersion = paperAccounts[0]?.modelVersion ?? null;
  const exposurePct =
    viewStats.equity && viewStats.equity > 0 && viewStats.grossNotional !== null
      ? (viewStats.grossNotional / viewStats.equity) * 100
      : null;

  return (
    <aside
      className="workbench-side-panel workbench-position-panel bg-neutral-900 border-l border-neutral-800 flex flex-col justify-between shrink-0 select-none overflow-hidden"
      data-testid="paper-panel"
      data-connected={paperConnected ? '1' : '0'}
      data-arms={paperAccounts.map((a) => a.arm).join(',')}
    >
      <div className="workbench-position-content flex-1 overflow-y-auto divide-y divide-neutral-800">
        {/* Section 1: 模拟实盘持仓（服务端账本） */}
        <div className="p-3.5 space-y-2.5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-neutral-100 flex items-center gap-1.5">
              <Wallet className="w-4 h-4 text-emerald-400" />
              <span>模拟实盘持仓</span>
            </h3>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-neutral-400">
                共 {paperPositions.length} 仓
              </span>
              {onToggleGate && (
                <button
                  type="button"
                  onClick={() => onToggleGate()}
                  aria-pressed={isGateRunning}
                  title={isGateRunning ? "当前运行中：点击暂停（阻断新开仓，已有持仓继续盯市）" : "当前已暂停：点击启动（放开新开仓）"}
                  /* 中性底 + 状态点。暂停时只把**描边**换成琥珀色 —— 这是设计规范
                     第 3 条「层级靠结构与字重，不靠颜色」允许的强调手段：
                     强调用描边，不用整块铺色（规范 01「不用色块堆叠」）。 */
                  className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-medium bg-[var(--ds-surface-2)] text-[var(--ds-text-2)] border transition-colors hover:bg-[var(--ds-surface-3)] hover:text-[var(--ds-text)] ${
                    isGateRunning
                      ? 'border-[var(--ds-border)]'
                      : 'border-[var(--ds-warn)]'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 shrink-0 rounded-full ${
                      isGateRunning ? 'bg-[var(--ds-pos)]' : 'bg-[var(--ds-warn)]'
                    }`}
                    aria-hidden="true"
                  />
                  <span>{isGateRunning ? '运行中' : '已暂停'}</span>
                </button>
              )}
            </div>
          </div>

          {/* WORK-04 退役：Paper 未接新共享核心 ⇒ NOT_CONNECTED（不回退旧 3a–3d 账本模型） */}
          {paperNotConnected && (
            <div
              data-testid="paper-not-connected"
              className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-[10px] text-amber-300 leading-relaxed space-y-1"
            >
              <div className="flex items-center gap-1.5 font-bold text-amber-200">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                <span>模拟实盘 NOT_CONNECTED</span>
              </div>
              <div className="break-all">
                Paper 尚未接通新共享核心
                {paperNewCore ? `（${paperNewCore} 只完成 Backtest 一侧）` : ''}
                ⇒ 不展示旧 3a–3d 纸面账户，<b>禁止新开仓与真实下单</b>，不回退旧执行通道
                （exec_bridge）。旧账本原件保留（可恢复只读归档），但不作为当前账户。
              </div>
              {paperRetiredKeys.length > 0 && (
                <div className="font-mono text-amber-400/80">
                  已退役旧策略键：{paperRetiredKeys.join(' / ')}
                </div>
              )}
            </div>
          )}

          {paperMissingReason && (
            <div
              data-testid="paper-missing-error"
              className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-[10px] text-rose-300 flex items-center gap-2"
            >
              <AlertCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
              <span>持仓接口读取异常: {paperMissingReason}</span>
            </div>
          )}

          {/* 执行守护与信号引擎状态（移入低干扰折叠详情，默认折叠，主界面不铺陈技术指标） */}
          <details
            className="p-2.5 rounded-lg bg-neutral-950/60 border border-neutral-850 text-xs font-mono text-neutral-400 group"
            data-testid="execution-daemon-card"
          >
            <summary className="cursor-pointer select-none flex items-center justify-between text-[11px] text-neutral-400 hover:text-neutral-200">
              <span className="flex items-center gap-1.5 font-sans">
                <Cpu className="w-3.5 h-3.5 text-neutral-500 group-open:text-emerald-400" />
                <span>执行守护详情</span>
              </span>
              <span className="text-[10px] text-neutral-500 group-open:hidden">点击展开</span>
            </summary>
            <div className="pt-2 mt-2 border-t border-neutral-800 space-y-2">
              <div className="grid grid-cols-2 gap-1 text-[11px] text-neutral-400">
                <div>守护 PID: <span className="text-neutral-200 font-semibold">{executionDaemon?.pid ?? '—'}</span></div>
                <div>推进周期: <span className="text-neutral-200 font-semibold">{executionDaemon?.cycleCount ?? '—'} 次</span></div>
                <div className="col-span-2 flex justify-between" data-testid="daemon-universe">
                  <span>监控标的:</span>
                  <span className="text-neutral-200 font-semibold">
                    {executionDaemon?.universe != null ? `${executionDaemon.universe} 个` : '—'}
                    {(() => {
                      const sc = executionDaemon?.stateCounter;
                      if (!sc) return '';
                      const parts = [
                        ['BOX', '箱体中'],
                        ['SWEEP', '扫低'],
                        ['RETEST', '回踩'],
                        ['BREAKOUT', '突破'],
                      ]
                        .filter(([k]) => (sc[k] ?? 0) > 0)
                        .map(([k, label]) => `${label} ${sc[k]}`);
                      return parts.length ? ` · ${parts.join(' / ')}` : ' · 全部空闲';
                    })()}
                  </span>
                </div>
                {executionDaemon?.replayCandidates != null && (
                  <div className="col-span-2 flex justify-between" data-testid="daemon-replay">
                    <span>历史回放候选:</span>
                    <span className="text-neutral-200 font-semibold">
                      {executionDaemon.replayCandidates} 个
                      <span className="text-neutral-500 font-normal">（历史不补开）</span>
                    </span>
                  </div>
                )}
                {executionDaemon?.atrCaliber != null && (
                  <div className="col-span-2 flex justify-between" data-testid="daemon-atr-caliber">
                    <span>ATR 口径:</span>
                    <span className="text-neutral-200 font-semibold">
                      {(() => {
                        const a = executionDaemon.atrCaliber as Record<string, any>;
                        const period = a.period ?? '—';
                        const warm = a.warmup_bars ?? '—';
                        const warmed = a.n_warmed ?? null;
                        const total = a.n_engines ?? null;
                        const notWarmed = a.n_not_warmed ?? 0;
                        const skipped = a.last_box_skipped_warmup_utc;
                        return (
                          <>
                            4h Wilder ATR({period}) 连续递推
                            <span className="text-neutral-500 font-normal">
                              {' '}· 预热 {warm} 根
                              {warmed != null && total != null
                                ? ` · 已达标 ${warmed}/${total}`
                                : ''}
                              {notWarmed > 0 ? ` · 未达标 ${notWarmed}` : ''}
                              {skipped ? ` · 最近跳过建档 ${skipped}` : ''}
                            </span>
                          </>
                        );
                      })()}
                    </span>
                  </div>
                )}
                {executionDaemon?.atrCaliber != null && (
                  <div className="col-span-2 flex justify-between" data-testid="daemon-box-gate">
                    <span>建档口径:</span>
                    <span className="text-neutral-200 font-semibold">
                      {(() => {
                        const a = executionDaemon.atrCaliber as Record<string, any>;
                        const mode = a.box_gate_mode;
                        const metric = a.gate_metric;
                        const opened = a.n_box_gate_open;
                        const total = a.n_engines;
                        const label =
                          mode === 'atr_warmup'
                            ? '规范口径（ATR 预热满即建档）'
                            : mode === 'window_bars'
                              ? '旧口径（窗口内再等 200 根）'
                              : '—';
                        return (
                          <>
                            {label}
                            <span className="text-neutral-500 font-normal">
                              {' '}· 判据 {metric ?? '—'}
                              {opened != null && total != null
                                ? ` · 闸门已开 ${opened}/${total}`
                                : ''}
                            </span>
                          </>
                        );
                      })()}
                    </span>
                  </div>
                )}
                {executionDaemon?.watermarkCheck != null && (
                  <div className="col-span-2 flex justify-between" data-testid="daemon-watermark">
                    <span>1h 水位:</span>
                    <span className="text-neutral-200 font-semibold">
                      {(() => {
                        const w = executionDaemon.watermarkCheck as Record<string, any>;
                        const lag = w.lag_1h_bars;
                        const per = (w.per_symbol as Record<string, any>) || {};
                        const nGt1 = per.n_lag_1h_gt1;
                        const aligned = w.aligned_1h;
                        const bad = typeof lag === 'number' && lag > 1;
                        return (
                          <>
                            <span className={bad ? 'text-rose-400' : undefined}>
                              {typeof lag === 'number' ? `落后 ${lag} 根` : '—'}
                            </span>
                            <span className="text-neutral-500 font-normal">
                              {' '}
                              · {aligned === true ? '已对齐' : aligned === false ? '未对齐' : '—'}
                              {typeof nGt1 === 'number' ? ` · 逐币落后>1 根 ${nGt1} 个` : ''}
                            </span>
                          </>
                        );
                      })()}
                    </span>
                  </div>
                )}
                {executionDaemon?.dataQuality != null && (
                  <div className="col-span-2 flex justify-between" data-testid="daemon-data-quality">
                    <span>数据质量闸门:</span>
                    <span className="text-neutral-200 font-semibold">
                      {(() => {
                        const dq = executionDaemon.dataQuality as Record<string, any>;
                        const n = dq.blocked_n;
                        const blocked = (dq.blocked as Record<string, any>) || {};
                        const syms = Object.keys(blocked);
                        const head = syms.slice(0, 2).join(', ');
                        return (
                          <>
                            <span
                              className={
                                typeof n === 'number' && n > 0 ? 'text-amber-400' : undefined
                              }
                            >
                              {typeof n === 'number' ? `禁止决策 ${n} 个` : '—'}
                            </span>
                            <span className="text-neutral-500 font-normal">
                              {' '}· 交易所占位行（零成交+平价）
                              {syms.length > 0 ? ` · ${head}${syms.length > 2 ? ' 等' : ''}` : ''}
                            </span>
                          </>
                        );
                      })()}
                    </span>
                  </div>
                )}
              </div>
              <div className="flex justify-between items-center text-[10px] text-neutral-400">
                <span>最近推进:</span>
                <span className="text-neutral-300">
                  {executionDaemon?.lastStepUtc
                    ? fmtDateTimeSeconds(new Date(executionDaemon.lastStepUtc).getTime())
                    : '—'}
                </span>
              </div>
              <div className="flex justify-between items-center text-[10px] text-neutral-400">
                <span>阻断原因:</span>
                <span className={executionDaemon?.blockedReason ? 'text-rose-400 font-semibold' : 'text-neutral-400'}>
                  {(() => {
                    if (executionDaemon?.blockedReason) return executionDaemon.blockedReason;
                    const isRunning = Boolean(
                      executionDaemon?.running ||
                      (executionDaemon?.status === 'running' && executionDaemon?.pid)
                    );
                    if (!isRunning) return '执行守护未运行 / 进程离线';
                    return isGateRunning ? '无阻断 · 正常流转' : '新开仓已暂停';
                  })()}
                </span>
              </div>
            </div>
          </details>

          {/* Account Details Box —— 严格仅显示用户指定的5大核心指标，其余放折叠详情 */}
          <div
            className="p-3 rounded-lg bg-neutral-950 border border-neutral-800 text-xs space-y-2 font-mono text-neutral-400"
            data-testid="paper-account-box"
            data-cash={totalCashAll === null ? '' : totalCashAll}
            data-equity={totalEquityAll === null ? '' : totalEquityAll}
            data-n-positions={paperPositions.length}
            data-n-pending={allPendingList.length}
          >
            {/* 1. 总资产 */}
            <div className="flex justify-between items-center">
              <span className="text-neutral-400 whitespace-nowrap">总资产(盯市):</span>
              <span className="text-neutral-100 font-bold whitespace-nowrap text-sm">
                {fmtUsd(viewStats.equity)}
              </span>
            </div>
            {/* 2. 可用资金 */}
            <div className="flex justify-between items-center">
              <span className="text-neutral-400 whitespace-nowrap">可用资金:</span>
              <span className="text-neutral-200 font-medium whitespace-nowrap">
                {fmtUsd(viewStats.cash)}
              </span>
            </div>
            {/* 3. 占用资金 */}
            <div className="flex justify-between items-center">
              <span className="text-neutral-400 whitespace-nowrap">占用资金:</span>
              <span className="text-neutral-200 font-medium whitespace-nowrap">
                {fmtUsd(viewStats.usedMargin)}
              </span>
            </div>
            {/* 4. 盈亏 */}
            <div className="flex justify-between items-center">
              <span className="text-neutral-400 whitespace-nowrap">盈亏(未实现/已实现):</span>
              <span className="whitespace-nowrap font-semibold">
                <span
                  className={
                    viewStats.unrealizedPnl === null || viewStats.unrealizedPnl === 0
                      ? 'text-[var(--ds-text-2)]'
                      : viewStats.unrealizedPnl < 0
                        ? 'text-[var(--ds-neg)]'
                        : 'text-[var(--ds-pos)]'
                  }
                >
                  {fmtSignedUsd(viewStats.unrealizedPnl)}
                </span>
                <span className="text-[var(--ds-text-4)] mx-1">/</span>
                <span
                  className={
                    viewStats.realizedPnl === null || viewStats.realizedPnl === 0
                      ? 'text-[var(--ds-text-2)]'
                      : viewStats.realizedPnl < 0
                        ? 'text-[var(--ds-neg)]'
                        : 'text-[var(--ds-pos)]'
                  }
                >
                  {fmtSignedUsd(viewStats.realizedPnl)}
                </span>
              </span>
            </div>
            {/* 5. 持仓数量 */}
            <div className="flex justify-between items-center">
              <span className="text-neutral-400 whitespace-nowrap">持仓数量:</span>
              <span className="text-neutral-200 font-medium whitespace-nowrap">
                {scopedPositions.length} 仓在持 / {viewStats.nClosed ?? 0} 笔已平
              </span>
            </div>

            {/* 更多指标折叠层 */}
            <details className="pt-2 border-t border-neutral-800/80 group">
              <summary className="text-[10px] text-neutral-500 hover:text-neutral-300 cursor-pointer select-none flex items-center justify-between">
                <span>账户详情 (手续费/资金费/回撤/参数)</span>
                <span className="text-[9px] text-neutral-600 group-open:hidden">▼</span>
                <span className="text-[9px] text-neutral-600 hidden group-open:inline">▲</span>
              </summary>
              <div className="pt-2 space-y-1.5 text-[11px] text-neutral-400">
                <div className="flex justify-between items-center pb-1 border-b border-neutral-900 text-[10px]">
                  <span className="text-neutral-500 whitespace-nowrap">账户/策略身份:</span>
                  <span className="text-emerald-400 font-semibold whitespace-nowrap" title="模拟实盘服务端官方账本 · FT-LOW4H-EXP-V2.2">
                    模拟账户(paper) · FT-LOW4H-EXP-V2.2
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-neutral-400 whitespace-nowrap">手续费:</span>
                  <span className="text-neutral-200 whitespace-nowrap">{fmtUsd(viewStats.feesPaid)}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-neutral-400 whitespace-nowrap">资金费:</span>
                  <span
                    className={`whitespace-nowrap font-medium ${
                      viewStats.fundingPaid && viewStats.fundingPaid > 0
                        ? 'text-amber-500'
                        : 'text-neutral-200'
                    }`}
                  >
                    {fmtSignedUsd(viewStats.fundingPaid)}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-neutral-400 whitespace-nowrap">最大回撤:</span>
                  <span className="text-neutral-200 font-medium whitespace-nowrap">
                    {viewStats.maxDrawdownPct !== null ? `${fmt(viewStats.maxDrawdownPct, 2)}%` : '—'}
                  </span>
                </div>
                <div className="flex justify-between items-center gap-2 min-w-0">
                  <span className="text-[var(--ds-text-3)] whitespace-nowrap shrink-0">参数来源:</span>
                  <span
                    className="min-w-0 truncate text-[var(--ds-text-3)]"
                    title={`服务端返回的生效参数来源；含 fallback 表示未走冻结视图｜${viewStats.paramsSource ?? '—'}`}
                  >
                    {viewStats.paramsSource ?? '—'}
                  </span>
                </div>
              </div>
            </details>
          </div>



          {/* Quick Trade —— 按「当前选中信号」开仓（WORK-04：Paper NOT_CONNECTED 时一律禁用） */}
          <div className="flex gap-2">
            <button
              onClick={() => onOpenQuickPosition('long')}
              disabled={orderBusy || executionDisabled}
              title={
                executionDisabled
                  ? disabledReason
                  : '按当前选中信号在服务端账本开多仓'
              }
              className={`flex-1 py-1.5 rounded-lg border text-xs font-semibold flex items-center justify-center gap-1 transition-colors ${
                orderBusy || executionDisabled
                  ? 'bg-neutral-800/60 text-neutral-500 border-neutral-700 cursor-not-allowed'
                  : 'bg-[var(--ds-pos-bg-soft)] text-[var(--ds-pos)] border-[var(--ds-border)] hover:border-[var(--ds-pos)]'
              }`}
            >
              <TrendingUp className="w-3.5 h-3.5" />
              按选中信号开多
            </button>
            <button
              onClick={() => onOpenQuickPosition('short')}
              disabled={orderBusy || executionDisabled}
              title={
                executionDisabled
                  ? disabledReason
                  : '按当前选中信号在服务端账本开空仓'
              }
              className={`flex-1 py-1.5 rounded-lg border text-xs font-semibold flex items-center justify-center gap-1 transition-colors ${
                orderBusy || executionDisabled
                  ? 'bg-neutral-800/60 text-neutral-500 border-neutral-700 cursor-not-allowed'
                  : 'bg-[var(--ds-neg-bg-soft)] text-[var(--ds-neg)] border-[var(--ds-border)] hover:border-[var(--ds-neg)]'
              }`}
            >
              <TrendingDown className="w-3.5 h-3.5" />
              按选中信号开空
            </button>
          </div>

          {/* 服务端提示 / 状态反馈：支持一键关闭 */}
          {showNotice && (
            <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-[10px] text-amber-300 leading-relaxed flex items-center justify-between gap-1.5">
              <div className="flex items-center gap-1.5 min-w-0">
                <AlertCircle className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                <span className="truncate">{orderNotice}</span>
              </div>
              <button
                type="button"
                onClick={() => setDismissedNotice(orderNotice)}
                className="text-amber-400/70 hover:text-amber-200 shrink-0 p-0.5 rounded"
                title="关闭提示"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )}

          {/* Positions List Header */}
          <div className="flex items-center justify-between text-[10px] text-neutral-400 pt-0.5">
            <span className="flex items-center gap-1 font-medium text-neutral-300">
              <Layers className="w-3 h-3 text-emerald-400" />
              <span>实际持仓 ({scopedPositions.length})</span>
            </span>
            <span className="font-mono text-neutral-500">点击定位 K 线</span>
          </div>

          {/* Positions List */}
          <div className="space-y-2">
            {scopedPositions.length === 0 ? (
              <div className="p-4 rounded-lg bg-neutral-950/60 border border-neutral-800 border-dashed text-center text-xs text-neutral-500">
                {paperNotConnected ? '模拟实盘未连接' : '暂无持仓'}
              </div>
            ) : (
              scopedPositions.map((pos) => {
                const isLong = pos.direction === 'long';
                const cleanSym = pos.symbol.replace('USDT', '');
                const pnl = pos.unrealizedPnl;

                /**
                 * **真实止损状态**（2026-10-15 任务四）。
                 *
                 * 旧实现在这里用 `entryPrice ∓ 2.0×ATR` **前端自行推导**一个「建议止损位」——
                 * 该值与账本/冻结规则（3×ATR 结构止损）无关，属凭空建议，已删除。
                 * 现只展示**账本真实记录的 `stop_price`**，并按真实 `mark_price` 如实说明现价与止损的位置关系
                 * （纯数值比较，不预测、不生成新价位）。
                 */
                const stopState = (() => {
                  if (pos.stopPrice === null || pos.stopPrice === undefined) {
                    return { text: '账本未记录止损', cls: 'text-neutral-400' };
                  }
                  if (pos.markPrice === null || pos.markPrice === undefined) {
                    return { text: '账本止损价（现价未知）', cls: 'text-amber-500' };
                  }
                  const through = isLong ? pos.markPrice <= pos.stopPrice : pos.markPrice >= pos.stopPrice;
                  return through
                    ? { text: '现价已在止损之外（账本尚未触发）', cls: 'text-rose-400' }
                    : { text: '现价在止损内', cls: 'text-emerald-400' };
                })();

                /** 旧版/未验收/规则违规 标注（后端只读追加；缺失则不标）。 */
                const isLegacy =
                  pos.excludedFromOfficial ||
                  (pos.verificationStatus !== null && pos.verificationStatus !== 'verified');

                return (
                  <div
                    key={pos.key}
                    onClick={() => onSelectTokenSymbol(cleanSym)}
                    className="p-3 rounded-lg bg-neutral-950 border border-neutral-800 hover:border-emerald-500/40 cursor-pointer space-y-2 transition-all text-xs"
                    data-testid="paper-position-row"
                    data-position-id={pos.positionId}
                    data-signal-uid={pos.signalUid ?? ''}
                    data-verification-status={pos.verificationStatus ?? ''}
                  >
                    {/* 头部：代币图标 + 代币名称 + 旧版标记 + 平仓按钮 */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TokenIcon symbol={cleanSym} size="sm" />
                        <span className="text-sm font-bold text-neutral-100 whitespace-nowrap">
                          {cleanSym}
                        </span>
                        {isLegacy && (
                          <span
                            className="text-[9px] px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 font-sans shrink-0"
                            title={`${pos.label ?? '旧版/未验收'}${pos.violations.length ? ' · 违规：' + pos.violations.join('、') : ''}`}
                          >
                            {pos.label ?? '旧版/未验收'}
                          </span>
                        )}
                      </div>

                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onClosePosition(pos.arm, pos.positionId);
                        }}
                        disabled={orderBusy}
                        className={`px-2.5 py-1 rounded border transition-colors text-xs shrink-0 font-medium ${
                          orderBusy
                            ? 'text-neutral-600 border-neutral-800 cursor-wait'
                            : 'text-neutral-300 border-neutral-700 hover:text-rose-400 hover:border-rose-500/50 hover:bg-rose-500/10'
                        }`}
                        title="按当前市价立即平仓"
                      >
                        平仓
                      </button>
                    </div>

                    {/* 旧版/未验收 说明：排除正式策略表现 */}
                    {isLegacy && (
                      <div className="text-[9px] text-amber-400/80 leading-relaxed font-sans">
                        旧版/未验收：已排除正式策略表现；原账本未修改
                        {pos.violations.length > 0 && <span> · 违规项 {pos.violations.join('、')}</span>}
                      </div>
                    )}

                    {/* 核心信息列表 */}
                    <div className="space-y-1.5 text-xs font-mono bg-neutral-900/60 p-2.5 rounded border border-neutral-800/80">
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">开仓价格:</span>
                        <span className="text-neutral-100 font-medium whitespace-nowrap">{fmtPriceSmart(pos.entryPrice)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">实时标记:</span>
                        <span className="text-neutral-100 font-medium whitespace-nowrap">{fmtPriceSmart(pos.markPrice)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">持仓金额:</span>
                        <span className="text-neutral-100 font-medium whitespace-nowrap">{fmt(pos.notional, 1)} U</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">未实现盈亏:</span>
                        <span
                          className={`font-bold whitespace-nowrap ${
                            pnl === null
                              ? 'text-neutral-400'
                              : pnl >= 0
                                ? 'text-emerald-500'
                                : 'text-rose-500'
                          }`}
                        >
                          {fmtSignedUsd(pnl, 2)}
                          {pos.unrealizedPnlPct !== null
                            ? ` (${pos.unrealizedPnlPct >= 0 ? '+' : ''}${fmt(pos.unrealizedPnlPct, 2)}%)`
                            : ''}
                        </span>
                      </div>
                      {/* 真实止损：只展示账本记录的 stop_price 与入场 ATR 基数（无前端推导） */}
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">账本止损价:</span>
                        <div className="flex items-center gap-1.5">
                          <span className="text-amber-500 font-medium whitespace-nowrap">{fmtPriceSmart(pos.stopPrice)}</span>
                          <button
                            type="button"
                            data-testid={`btn-stop-trail-${pos.positionId}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleStopHistory(pos.positionId, pos.arm);
                            }}
                            className="text-[10px] text-emerald-400 hover:text-emerald-300 underline cursor-pointer"
                          >
                            {expandedStops[pos.positionId] ? '收起轨迹' : '止损轨迹'}
                          </button>
                        </div>
                      </div>
                      {expandedStops[pos.positionId] && (
                        <div data-testid={`stop-trail-panel-${pos.positionId}`} className="my-1 p-2 bg-neutral-900 rounded border border-neutral-800 text-[10px] space-y-1.5">
                          <div className="text-neutral-400 font-semibold flex justify-between items-center border-b border-neutral-800 pb-1">
                            <span>FT-LOW4H 止损上移轨迹</span>
                            {loadingStops[pos.positionId] && <span className="text-amber-400 animate-pulse">加载中...</span>}
                          </div>
                          {stopsError[pos.positionId] ? (
                            <div data-testid="stop-trail-error" className="text-rose-400 font-mono py-1">
                              ⚠ {stopsError[pos.positionId]}
                            </div>
                          ) : loadingStops[pos.positionId] ? (
                            <div className="text-neutral-500 py-1">正在读取账本止损事件...</div>
                          ) : (stopsHistory[pos.positionId] || []).length === 0 ? (
                            <div data-testid="stop-trail-empty" className="text-neutral-500 py-1">初始止损生效中，暂无上移记录</div>
                          ) : (
                            <div data-testid="stop-trail-records" className="space-y-1">
                              {(stopsHistory[pos.positionId] || []).map((h: any, idx: number) => {
                                const parseMs = (v: any) => {
                                  if (!v) return null;
                                  if (typeof v === 'number') return v;
                                  const t = new Date(v).getTime();
                                  return Number.isFinite(t) ? t : null;
                                };
                                const refTime = fmtDateTimeSeconds(parseMs(h.ref_bar_open_ms));
                                const decTime = fmtDateTimeSeconds(parseMs(h.decision_at));
                                const effTime = fmtDateTimeSeconds(parseMs(h.effective_at));
                                return (
                                  <div key={idx} className="bg-neutral-950/60 p-1.5 rounded border border-neutral-800/60 text-neutral-300">
                                    <div className="flex justify-between font-mono">
                                      <span className="text-neutral-400 font-sans">{h.reason === 'initial_stop' ? '初始建仓止损' : '4hK线触发上移'}</span>
                                      <span className="text-amber-400 font-bold">
                                        {h.old_stop ? `${fmtPriceSmart(h.old_stop)} → ` : ''}{fmtPriceSmart(h.new_stop)}
                                      </span>
                                    </div>
                                    <div className="text-[9px] text-neutral-500 mt-1 space-y-0.5">
                                      <div>引用4h: <span className="text-neutral-400">{refTime}</span></div>
                                      <div className="flex justify-between">
                                        <span>决策: {decTime}</span>
                                        <span className="text-emerald-500/80">生效: {effTime}</span>
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">止损基数(ATR@入场):</span>
                        <span className="text-neutral-300 font-medium whitespace-nowrap">{fmtPriceSmart(pos.atrAtEntry)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">止损状态:</span>
                        <span className={`font-medium whitespace-nowrap ${stopState.cls}`}>{stopState.text}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-neutral-400 whitespace-nowrap">信号身份(signal_uid):</span>
                        <span className="text-neutral-300 font-medium whitespace-nowrap truncate max-w-[60%]" title={pos.signalUid ?? ''}>
                          {pos.signalUid ?? '—'}
                        </span>
                      </div>
                      <div className="flex justify-between items-center pt-1 border-t border-neutral-800/60 text-[11px]">
                        <span className="text-neutral-400 whitespace-nowrap">开仓时间:</span>
                        <span className="text-neutral-200 font-medium whitespace-nowrap">{fmtDateTimeSeconds(pos.entryTs)}</span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* 未来待成交挂单（服务端登记）——**计划下单**层：未成交，不冒充已买入。
              三态必须可区分（UI-PENDING-01）：
                · has_pending —— 账本读到且挂单表非空
                · empty       —— 账本读到且挂单表为空（**这是「没有挂单」，不是「没读到」**）
                · unreadable  —— 账本读取失败/未就绪（**不得渲染上一次缓存的挂单**，
                                 也不得显示成「0 笔」，否则旧快照会被当成当前挂单） */}
          {(paperConnected || paperMissingReason) && (
            <div
              className="space-y-1.5 pt-1"
              data-testid="paper-pending-block"
              data-state={
                !paperConnected ? 'unreadable' : scopedPending.length > 0 ? 'has_pending' : 'empty'
              }
              data-n-pending={paperConnected ? scopedPending.length : -1}
            >
              <div className="text-[10px] text-neutral-300 flex items-center justify-between">
                <span className="flex items-center gap-1 font-medium">
                  <Clock className="w-3 h-3 text-amber-400" />
                  <span data-testid="paper-pending-title">
                    {!paperConnected
                      ? '待成交订单（状态未知）'
                      : `待成交订单 (${scopedPending.length})`}
                  </span>
                </span>
              </div>

              {!paperConnected ? (
                <div
                  data-testid="paper-pending-error"
                  className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/30 text-[10px] text-rose-300 leading-relaxed space-y-1"
                >
                  <div className="flex items-center gap-1.5 font-bold text-rose-200">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                    <span>挂单状态未知（账本读取失败）</span>
                  </div>
                  <div className="break-all">{paperMissingReason ?? '账本未就绪'}</div>
                </div>
              ) : scopedPending.length === 0 ? (
                <div
                  data-testid="paper-pending-empty"
                  className="p-3 rounded-lg bg-neutral-950/60 border border-neutral-800 border-dashed text-center text-xs text-neutral-500"
                >
                  暂无待成交订单
                </div>
              ) : (
                <>
                  {scopedPending.map((p) => (
                    <div
                      key={`${p.arm ?? "unknown"}:${p.symbol}:${p.pendingId}`}
                      className="p-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-[10px] font-mono text-neutral-400 space-y-0.5"
                      data-testid="paper-pending-row"
                      data-pending-id={p.pendingId}
                      data-arm={p.arm ?? ""}
                      data-symbol={p.symbol}
                      data-origin={p.origin ?? ''}
                      data-status={p.status}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-neutral-200 font-bold truncate">
                          <span
                            data-testid="paper-pending-id"
                            className="text-[9px] text-neutral-500 mr-1"
                            title="服务端账本 paper_pending.pending_id"
                          >
                            #{p.pendingId}
                          </span>
                          {p.symbol}
                        </span>
                        <span className="text-amber-300 shrink-0">
                          <span data-testid="paper-pending-status">
                            {PENDING_STATUS[p.status] ?? p.status}
                          </span>
                          <span className="text-[9px] text-amber-400/70"> · {p.status}</span>
                        </span>
                      </div>
                      <div className="flex justify-between gap-2">
                        <span className="shrink-0">来源:</span>
                        <span className="text-neutral-300 truncate" data-testid="paper-pending-origin">
                          {pendingOriginText(p.origin)} · {p.arm ?? "来源臂未知"}
                        </span>
                      </div>
                      <div data-testid="paper-pending-direction">方向: {p.direction === "long" ? "多" : p.direction === "short" ? "空" : "未提供"}</div>
                      {/* 等待原因：`reason` 是契约字段；挂单在等时服务端通常留 null
                          ⇒ 如实展示「结构性等待条件」，不用 0/— 冒充已有原因。 */}
                      <div className="flex justify-between gap-2">
                        <span className="shrink-0">等待原因:</span>
                        <span
                          className={p.reason ? 'text-amber-500 text-right' : 'text-neutral-400 text-right'}
                          data-testid="paper-pending-reason"
                        >
                          {p.reason ?? '等待最早可成交 15m 闭合（服务端时钟推进）'}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span>最早可执行 15m:</span>
                        <span className="text-amber-500" data-testid="paper-pending-earliest">
                          {fmtCompactUtcMs(p.earliestFillMs)}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span>请求锚点时刻:</span>
                        <span className="text-neutral-300">{fmtCompactUtcMs(p.requestedAt)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>信号确认/可得:</span>
                        <span className="text-neutral-400">
                          {fmtCompactUtcMs(p.eventTime)} / {fmtCompactUtcMs(p.availableAt)}
                        </span>
                      </div>
                      <div className="flex justify-between gap-2">
                        <span className="shrink-0">信号身份:</span>
                        <span
                          className="text-neutral-400 truncate max-w-[62%]"
                          title={p.signalUid ?? ''}
                          data-testid="paper-pending-signal-uid"
                        >
                          {p.signalUid ?? '—'}
                        </span>
                      </div>
                    </div>
                  ))}
                  {/* 契约缺口如实标注：不在此处用「预期方向/估算数量」补位（那是新增交易语义）。 */}
                  <div className="text-[9px] text-neutral-500 leading-relaxed">
                    数量与成交价待实际成交时确定。
                  </div>
                </>
              )}
            </div>
          )}

          <div className="text-[10px] text-neutral-500 flex items-center justify-between pt-1">
            <span>持仓历史 → 回测复盘</span>
            {paperNotConnected ? <span>NOT_CONNECTED · 未接新核心</span> : null}
          </div>
        </div>

        {/* Section 2: 实盘持仓（只读） */}
        <div className="p-3.5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-neutral-100 flex items-center gap-1.5">
              <Shield className="w-4 h-4 text-neutral-400" />
              <span>实盘持仓（只读）</span>
            </h3>
            <span className="text-[10px] px-1.5 py-0.5 rounded font-mono bg-neutral-800 text-neutral-400 border border-neutral-700">
              {liveAccounts.length > 0 ? `${liveAccounts.length} 交易所` : '无快照'}
            </span>
          </div>

          {liveAccounts.length === 0 ? (
            <details className="group rounded-lg border border-neutral-850 bg-neutral-950/40 p-2.5">
              <summary className="flex items-center justify-between cursor-pointer select-none text-xs text-neutral-400 hover:text-neutral-200">
                <span className="flex items-center gap-1.5">
                  <Lock className="w-3.5 h-3.5 text-neutral-500" />
                  <span>暂无实盘快照（仅读取，不交易）</span>
                </span>
                <span className="text-[10px] text-neutral-500 group-open:hidden">点击展开</span>
              </summary>
              <div className="mt-2.5 pt-2.5 border-t border-neutral-800/80 text-center space-y-1.5">
                <div className="text-xs text-neutral-300 font-medium">
                  {liveMissingReason ?? '尚无实盘账户快照'}
                </div>
                <p className="text-[11px] text-neutral-500 leading-relaxed">
                  只读：仅读取余额 / 权益 / 持仓，不提交任何真实订单、不修改 API 权限。
                </p>
              </div>
            </details>
          ) : (
            <div className="space-y-2">
              {liveAccounts.map((acc) => {
                const meta = LIVE_STATUS[acc.status] ?? {
                  text: acc.status,
                  cls: 'bg-neutral-800 text-neutral-400 border-neutral-700',
                };
                return (
                  <div
                    key={acc.exchange + ':' + acc.accountLabel}
                    data-testid="live-account-row"
                    data-exchange={acc.exchange}
                    data-status={acc.status}
                    data-n-positions={acc.nPositions}
                    className="p-2.5 rounded-lg bg-neutral-950 border border-neutral-800 space-y-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-neutral-100">{acc.exchange}</span>
                      <span className={`text-[10px] px-1.5 py-0.2 rounded font-mono border ${meta.cls}`}>
                        {meta.text}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-1 text-[11px] font-mono text-neutral-400">
                      <div>权益: <span className="text-neutral-200">{fmtUsd(acc.equityUsd)}</span></div>
                      <div>可用: <span className="text-neutral-200">{fmtUsd(acc.availableUsd)}</span></div>
                      <div>已用保证金: <span className="text-neutral-200">{fmtUsd(acc.usedMarginUsd)}</span></div>
                      <div>持仓数: <span className="text-neutral-200">{acc.nPositions}</span></div>
                    </div>
                    {acc.status === 'connected_no_positions' && (
                      <div className="text-[10px] text-emerald-300/90">已连接、当前无持仓</div>
                    )}
                    {acc.detail && acc.status !== 'connected_no_positions' && (
                      <div className="text-[10px] text-amber-300/90 break-all">{acc.detail}</div>
                    )}
                    {acc.positions.length > 0 && (
                      <div className="space-y-1 pt-1 border-t border-neutral-850">
                        {acc.positions.map((p, i) => (
                          <div
                            key={(p.symbol ?? 'pos') + i}
                            className="text-[10px] font-mono text-neutral-300 flex justify-between gap-2"
                          >
                            <span className="truncate">{p.symbol ?? '—'}</span>
                            <span className="shrink-0">
                              {p.side ?? '—'} {fmt(p.contracts, 4)} @ {fmtUsd(p.entryPrice, 4)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="text-[9px] text-neutral-600 font-mono">
                      只读快照 {acc.readAt ?? '—'}
                      {acc.ageSeconds === null ? '' : ` · ${fmt(acc.ageSeconds, 0)}s 前`}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="text-[10px] text-neutral-500 flex items-center justify-between pt-1">
            <span className="flex items-center gap-1">
              <History className="w-3 h-3" />
              点击持仓 → 定位中间K线
            </span>
            <span>仓位历史 → 回测复盘</span>
          </div>
        </div>
      </div>
    </aside>
  );
};
