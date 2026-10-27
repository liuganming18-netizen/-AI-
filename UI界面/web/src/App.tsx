/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { CryptoSignal } from './types/crypto';
import { tokenId } from './api/adapters';
import { useMarketData } from './api/useMarketData';
import { useLiveTrading } from './api/useLiveTrading';
import { useEndpoint } from './api/useEndpoint';
import { toObserveView } from './api/liveTrading';
import { NavigationSidebar } from './components/NavigationSidebar';
import { SignalListPanel } from './components/SignalListPanel';
import { TokenTable } from './components/TokenTable';
import { PositionPanel } from './components/PositionPanel';
import { DataCenterView } from './components/DataCenterView';
import { StrategyManagementView } from './components/StrategyManagementView';
import { ErrorBoundary } from './components/ErrorBoundary';
import { BacktestView } from './components/BacktestView';
import { TopStatusBar } from './components/TopStatusBar';
import {
  Database,
  Sliders,
  History,
  CheckCircle2,
  TrendingUp,
  Image as ImageIcon,
  Activity,
  Layers,
  Sparkles,
  Menu
} from 'lucide-react';
import { ThemeToggle } from './theme/ThemeToggle';
import { useIsMobile } from './hooks/useMediaQuery';

/** 移动端顶栏标题：与左侧导航的文案保持一致，避免两处口径不同 */
const NAV_TITLE: Record<string, string> = {
  workbench: '交易工作台',
  datacenter: '数据中心',
  strategy: '策略管理',
  backtest: '回测复盘',
};

/**
 * 模拟实盘支持的臂标识（与服务端纸面账本 `paper_3a~3d` 对齐）。
 *
 * **必须是模块级常量**：它原先写在组件体内，每次渲染都新建一个数组 ⇒
 * `handleOpenQuickPosition` 的 `useCallback` 依赖里那一项每帧都变 ⇒ 回调每帧重建
 * ⇒ 恰好把「依赖数组漏了 `isGateRunning`」这个缺陷**偶然兜住**了。
 * 一旦该回调被 memo 化或依赖数组被整理，陈旧闭包立刻显形：
 * 总闸从「暂停」切到「运行中」后，按钮仍按旧的 `isGateRunning=false` 拒绝发单
 * （或反过来：暂停后仍按旧值放行，发一次注定被服务端拒的请求）。
 * 因此这里把常量提到模块级，并把 `isGateRunning` 显式写进依赖。
 */
const PAPER_ARMS: readonly string[] = ['3a', '3b', '3c', '3d'];

export default function App() {
  /* 深链：`?run=<run_id>` ⇒ 直接落在「回测复盘」并打开该 run（由 BacktestWorkbench 读取）。
   * 用途：⑥ 实验记录列表只取最近 50 条且排序不稳定，指定的 run 可能不在列表里
   * ⇒ 深链是「打开某一笔」的确定性入口（验收/复现用）。无该参数时行为完全不变。
   *
   * 深链：`?nav=<workbench|datacenter|strategy|backtest>` ⇒ 直接落在指定视图。
   * 用途：画布 `system-flow.html` 每张卡片要给出「打开工作台对应入口」，
   * 而 `currentNav` 原本只是组件内部状态、没有任何 URL 表达 ⇒ 画布只能一律链到首页，
   * 用户落地后还得自己找左侧导航。这里只读取 `nav` 且用 `NAV_TITLE` 白名单校验，
   * 非法值静默回落 workbench，行为与改动前一致。 */
  const [currentNav, setCurrentNav] = useState(() => {
    try {
      const qs = new URLSearchParams(window.location.search);
      if (qs.get('run')) return 'backtest';
      const nav = qs.get('nav');
      if (nav && nav in NAV_TITLE) return nav;
      return 'workbench';
    } catch {
      return 'workbench';
    }
  });

  // 真实数据：全池行情 + 信号 + 顶部总览聚合；刷新由数据层按 5s 周期驱动。
  const {
    tokens,
    signals,
    signalsError,
    signalsState,
    error,
    asOf,
    aggregates,
    health,
    ensureDetail,
    setActiveDetailId,
    noteVisibleRows,
    setWorkbenchActive,
    setRefreshInterval,
    publicExtras,
    importedAt,
  } = useMarketData();

  /**
   * 模拟持仓 = **服务端纸面账本**（2026-10-13 信号与持仓接通）。
   *
   * 历史沿革：2026-10-11 移除了浏览器内存里按固定 0.1 张 / 10 倍 / 2% 止损的本地造仓路径；
   * 当时后端无 accounts 模块 ⇒ 只能保持空态。现在后端已接通 `paper_engine` 服务端账本
   * （四臂各 5000U 初始、仅多、1 倍、3×ATR 固定止损、0.5% 风险、无 ROI），
   * 于是持仓与开平仓**全部来自服务端**：浏览器不造仓、不本地增删。
   */
  const {
    paperAccounts,
    paperPositions,
    paperPending,
    paperMissingReason,
    paperNote,
    newEntryGate,
    legacyLedger,
    liveAccounts,
    liveMissingReason,
    orderNotice,
    orderBusy,
    openPosition,
    closePosition,
    // ── WORK-04 退役：Paper/Live 未接新共享核心 ⇒ NOT_CONNECTED（fail-closed）──
    paperNotConnected,
    paperNewCore,
    paperRetiredKeys,
    toggleGate,
    clearSignals,
    isGateRunning,
    executionDaemon,
    // 首次拉取是否已完成（用于区分「诊断还在读」与「诊断取不到」，SIGNAL-UI-LINK-07）
    loaded: liveLoaded,
  } = useLiveTrading();

  /**
   * 观察区（SIGNAL-OBSERVE-FIX-12）：处于观察状态的事件（BOX/SWEEP/RETEST）。
   *
   * 为什么单独一个接口、单独一条轮询：
   *   · 状态文件每 2 秒全量重写，346 条明细塞进去约每天 10 GB 写盘 ⇒ 明细走独立低频文件；
   *   · 明细只在**闭合 K 线推进 / 阶段迁移**时变，1h 节奏下 15 秒轮询足够新鲜，
   *     又不会让页面每 5 秒搬 300 KB。
   * 观察区与信号区**并列常显**：没有满足入场条件时，观察区照样要「有反应」。
   */
  const observeEnv = useEndpoint<any>('/signals/observe?arm=3a', 15000);
  const observeView = useMemo(
    () => (observeEnv.data ? toObserveView(observeEnv.data) : null),
    [observeEnv.data],
  );

  const [expandedTokenId, setExpandedTokenId] = useState<string | null>(null);  const [selectedSignal, setSelectedSignal] = useState<CryptoSignal | undefined>(undefined);
  /** 本地下单前置提示（如未选信号）；服务端回执/拒绝由 useLiveTrading 的 orderNotice 提供。 */
  const [hintNotice, setHintNotice] = useState<string | null>(null);

  /**
   * 移动端抽屉导航开关（< 768px 才有意义）。
   * 桌面端这个 state 恒为 false 且不起作用 —— 侧栏由 CSS 决定长什么样，
   * 这里只控制「离屏抽屉是否拉开」，不改任何布局宽度。
   */
  const [navOpen, setNavOpen] = useState(false);
  const isMobile = useIsMobile();
  const closeNav = useCallback(() => setNavOpen(false), []);
  // 从移动端切回桌面时把抽屉状态复位，避免再次变窄时抽屉自己弹开
  useEffect(() => {
    if (!isMobile) setNavOpen(false);
  }, [isMobile]);

  /**
   * 原版默认展开 BTC。只在**首次**拿到真实池时初始化一次：
   * 之后用户主动「收起」置为 null 必须保持收起，5 秒轮询刷新不得把展开状态重置回 BTC。
   */
  const didInitExpand = useRef(false);
  useEffect(() => {
    if (didInitExpand.current) return;
    if (tokens.length === 0) return;
    didInitExpand.current = true;
    const btc = tokens.find((t) => t.symbol === 'BTC');
    if (btc) setExpandedTokenId(btc.id);
  }, [tokens]);

  /**
   * 展开行 → 按需加载该合约的 K 线与 OI 序列；收起时告知数据层停止有界刷新。
   *
   * 2026-10-17 全局体检修复：**离开交易工作台时必须置空**。
   * 原实现只依赖 `expandedTokenId`，切到数据中心/策略管理/回测复盘后它仍是
   * 上次展开的合约 ⇒ `useMarketData` 的有界明细刷新（5s 补缺 + 30s 整体重取）
   * 会在用户完全看不见的页面上继续打 `/candles` 与 `/oi`。实测切页后 22 秒内
   * 仍有 29 次 K 线请求。改后：离开即停，回到工作台自动恢复上次展开项。
   */
  useEffect(() => {
    setActiveDetailId(currentNav === 'workbench' ? expandedTokenId : null);
  }, [currentNav, expandedTokenId, setActiveDetailId]);

  useEffect(() => {
    if (expandedTokenId && currentNav === 'workbench') ensureDetail(expandedTokenId);
  }, [expandedTokenId, currentNav, ensureDetail]);

  /**
   * 同理：**可见行集合只对工作台有意义**。列表卸载时不会主动上报空集合，
   * 若不清空，迷你图 pump（20s 一轮、每轮最多 24 行）会继续为上次那批可见行
   * 重取 `1h&limit=24` 的 K 线。这里在离开工作台时显式清空一次；
   * 回到工作台由 `TokenTable` 挂载时的 `reportVisible()` 重新上报。
   */
  const noteVisibleRowsRef = useRef(noteVisibleRows);
  noteVisibleRowsRef.current = noteVisibleRows;
  useEffect(() => {
    if (currentNav === 'workbench') return;
    noteVisibleRowsRef.current([]);
  }, [currentNav]);

  /**
   * 按行取数（迷你图 / 资金费率周期 / public-extras）的前台闸门。
   * 这三条链路只服务工作台行情表；切走就整批停，切回立刻补一轮。
   * 与上面两条 effect 合起来，离开工作台后**不再有任何行情明细请求**。
   */
  useEffect(() => {
    setWorkbenchActive(currentNav === 'workbench');
  }, [currentNav, setWorkbenchActive]);

  /**
   * 全池行情轮询按页面分级（2026-10-17 全局体检）。
   *
   * `/markets` 单次约 4.3MB、默认 5s 一轮。工作台与数据中心要看实时行情，
   * 保持 5s；策略管理与回测复盘不展示行情表，降到 30s —— 顶部状态条仍然更新，
   * 但不再每 5s 和后端单线程抢一次带宽（回测页首屏 10s 就吃亏在这）。
   * 只改频率，不改任何数值来源与口径。
   */
  useEffect(() => {
    const live = currentNav === 'workbench' || currentNav === 'datacenter';
    setRefreshInterval(live ? 5000 : 30000);
  }, [currentNav, setRefreshInterval]);

  /**
   * 选中信号**随刷新重新解析**（UI04 修复 2026-10-11；信号区清理 2026-10-14 收紧）。
   *
   * 按 signal_id 在当前（`scope=current`）列表中重解析，取最新生命周期字段。
   * **已不在当前列表中 ⇒ 清除选中引用**（返回 undefined）：
   * 被移出的记录（历史研究 / 启动前仅展示 / 结构提示 / 失效过期）不得再作为
   * 可开仓对象留在缓存里，否则空态下仍可能用旧信号卡触发模拟开仓。
   */
  useEffect(() => {
    setSelectedSignal((prev) => {
      if (!prev) return prev;
      const fresh = signals.find((s) => s.id === prev.id);
      if (!fresh) return undefined;
      return fresh;
    });
  }, [signals]);

  // When user clicks a signal -> locate corresponding token & expand K-line chart
  const handleSelectSignal = (signal: CryptoSignal) => {
    setSelectedSignal(signal);
    /**
     * UI02 修复（2026-10-11）：用**原始交易所 + market_id（合约身份）**精确定位，
     * 不再按“去倍率后的简称”找第一个匹配 —— 同名跨所（Binance/OKX 的 PEPE）
     * 或倍率合约（1000PEPE）会被打开成错误行情。
     * 无目标合约时明确不展开，**不回退首币**。
     */
    const target = tokens.find(
      (t) => t.id === tokenId(signal.rawExchange ?? '', signal.rawSymbol)
    );
    if (target) setExpandedTokenId(target.id);
  };

  /**
   * When user clicks a token row in the table -> toggle expand
   *
   * 引用必须稳定：`TokenTable` 的行组件是 memo 化的，若这个回调每次渲染都换新引用，
   * 全部已渲染行都会因 props 变化而重渲染，memo 失效（列表性能整改 2026-10-07）。
   */
  const handleToggleExpand = useCallback((tokenId: string) => {
    setExpandedTokenId((prev) => (prev === tokenId ? null : tokenId));
  }, []);

  /**
   * Close position handler：**服务端账本**平仓（按最近一根已闭合 15m 收盘）。
   * 成功/失败均由服务端回执决定，本地不预扣盈亏、不先行移除。
   *
   * **WORK-04 退役**：Paper 未接新共享核心 ⇒ 前端 fail-closed，不发单、不回退旧执行通道。
   */
  const handleClosePosition = useCallback(
    (arm: string, positionId: number) => {
      setHintNotice(null);
      if (paperNotConnected) {
        setHintNotice(
          '模拟实盘尚未接通新策略核心（NOT_CONNECTED）：已禁止平仓请求与真实下单，不回退旧执行通道。',
        );
        return;
      }
      void closePosition(arm, positionId, 'manual_ui');
    },
    [closePosition, paperNotConnected],
  );

  /**
   * 模拟实盘支持的臂标识列表（与服务端纸面账本 paper_3a~3d 对齐）。
   * 常量已提到模块级 `PAPER_ARMS`，此处仅做本地别名，不再每帧新建数组。
   */
  const ARMS = PAPER_ARMS;

  /**
   * 按「当前选中信号」在服务端纸面账本开仓。
   *
   * **WORK-04 退役（fail-closed）**：Paper 未接新共享核心 ⇒ 一律 NOT_CONNECTED：
   * 不新开仓、不真实下单、**不回退旧 3a–3d / exec_bridge**。以下旧臂路由逻辑仅作历史保留，
   * 在 `paperNotConnected` 为真时不可达。
   */
  const handleOpenQuickPosition = useCallback(
    (direction: 'long' | 'short') => {
      setHintNotice(null);
      // ── WORK-04：Paper 未接新核心 ⇒ 禁止新开仓（第一道闸，先于任何信号/臂判定）──
      if (paperNotConnected) {
        setHintNotice(
          '模拟实盘尚未接通新策略核心（NOT_CONNECTED）：已禁止新开仓与真实下单，不回退旧执行通道。',
        );
        return;
      }
      const sig = selectedSignal;
      /* 服务端新开仓总闸（第二道闸）：暂停时后端对自动 / 手工 / 挂单三路一律拒绝，
         前端在这里先挡住，避免发一次注定被拒的请求。 */
      if (!isGateRunning) {
        setHintNotice(
          '实盘模拟已暂停：服务端阻断一切新开仓（自动 / 手工 / 挂单三路一致）。先点右栏「已暂停」启动，再开仓。',
        );
        return;
      }
      if (!sig) {
        setHintNotice('请先在信号区选择一个信号：开仓需要该信号的 ATR 作为止损基数。');
        return;
      }
      // ── 当前交易信号区身份校验（2026-10-14 信号区清理）────────────────────────
      // 只有「实时真实臂入场」信号可开仓；历史研究 / 启动前仅展示 / 结构提示
      // （display_only / not_this_arm_structure 等）一律拒绝，不依赖 UI 是否隐藏。
      if (
        sig.origin !== 'live' ||
        sig.executableState !== 'enterable' ||
        sig.role !== 'enterable_arm_entry'
      ) {
        setHintNotice('该信号不在当前交易信号区（历史研究 / 启动前仅展示 / 结构提示），不可开仓。');
        return;
      }
      if (sig.executableState && sig.executableState !== 'enterable') {
        setHintNotice(`该信号当前可执行态为「${sig.executableState}」，不可开仓（后端会拒绝）。`);
        return;
      }
      if (sig.detectionAtr === null || sig.detectionAtr === undefined || !Number.isFinite(sig.detectionAtr)) {
        setHintNotice(
          `信号 ${sig.tokenSymbol} 未携带 ATR，无法计算止损；服务端会以 missing_atr_and_stop 拒绝。`,
        );
        return;
      }
      // 默认选用信号指定臂或主模拟臂 '3a'
      const arm = sig.arm && ARMS.includes(sig.arm) ? sig.arm : '3a';
      void openPosition({
        arm,
        symbol: sig.rawSymbol,
        direction,
        atr: sig.detectionAtr,
        signalUid: sig.id,
      });
    },
    /* `isGateRunning` 必须显式在依赖里：总闸是「能不能开仓」的第二道闸，
       漏掉它会让守卫读到上一帧的闸门值（陈旧闭包）。
       原实现靠 `ARMS` 每帧新建数组偶然兜住，属隐性缺陷，已一并修掉。 */
    [selectedSignal, openPosition, ARMS, paperNotConnected, isGateRunning],
  );

  /** 顶栏「模拟账户可用」= 服务端账本可用现金（无账本时 null ⇒ '—'）。
   *  生产规则是一个账户、总资金 5,000；3a–3d 共用同一份本金 ⇒ 本金只算一次，
   *  各臂只贡献「相对本金的变动」。直接求和会把 5,000 数成 20,000。 */
  const paperBalance = paperAccounts.length > 0
    ? paperAccounts[0].initialEquity
      + paperAccounts.reduce((a, x) => a + (x.cash - x.initialEquity), 0)
    : null;

  /**
   * 顶部状态条的两条主流币（对齐设计稿 `.topbar`）。
   * 只取 `BTC` / `ETH` 两条；池里没有该标的时给 `null`，顶栏显示 `—`（不补 0、不借别的币顶替）。
   */
  const topBtc = tokens.find((t) => t.symbol === 'BTC');
  const topEth = tokens.find((t) => t.symbol === 'ETH');

  return (
    <div className="flex h-[100dvh] w-screen overflow-hidden bg-neutral-950 text-neutral-100 font-sans selection:bg-emerald-500/30">
      {/* 1. Left Sidebar: 大类导航 (Navigation) */}
      <NavigationSidebar
        currentNav={currentNav}
        onSelectNav={setCurrentNav}
        realtimeAgeSeconds={health.realtimeAgeSeconds}
        realtimeStatus={health.realtimeStatus}
        mobileOpen={navOpen}
        onMobileClose={closeNav}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col h-full overflow-hidden min-w-0">

        {/* 2. 移动端顶栏（< 768px 才显示）：汉堡键 + 当前页 + 主题切换。
            桌面端由 CSS 隐藏，DOM 存在但不占位、不参与 Tab（见 index.css）。 */}
        <header className="ds-mobilebar">
          <button
            type="button"
            className="ds-mobilebar__btn"
            aria-label="打开导航"
            aria-expanded={navOpen}
            aria-controls="app-nav"
            onClick={() => setNavOpen(true)}
          >
            <Menu className="w-5 h-5" />
          </button>
          <span className="ds-mobilebar__title">{NAV_TITLE[currentNav] ?? '趋势研判系统'}</span>
          <div className="ds-mobilebar__theme">
            <ThemeToggle />
          </div>
        </header>

        {/* 2b. 顶部状态条（设计稿 `.topbar`）：桌面常驻，< 768px 交给移动端顶栏 */}
        <TopStatusBar
          btcPrice={topBtc?.price ?? null}
          btcChange={topBtc?.change24h ?? null}
          ethPrice={topEth?.price ?? null}
          ethChange={topEth?.change24h ?? null}
          paperBalance={paperBalance}
          asOf={asOf}
          feedError={error}
          realtimeStatus={health.realtimeStatus}
          realtimeAgeSeconds={health.realtimeAgeSeconds}
        />

        {/* 3. Center Workspace Body —— 页面级兜底：任何一页崩了也不能整窗白屏 */}
        <ErrorBoundary label="当前页面">
        {currentNav === 'workbench' ? (
          <div className="workbench-columns flex-1 min-h-0 flex overflow-hidden">
            {/* 3A. Left Sub-Column: 信号区 (Signals) */}
            <SignalListPanel
              signals={signals}
              signalsError={signalsError}
              signalsState={signalsState}
              tokens={tokens}
              selectedSignalId={selectedSignal?.id || null}
              onSelectSignal={handleSelectSignal}
              paperPositions={paperPositions}
              paperPending={paperPending}
              newEntryGate={newEntryGate}
              paperNotConnected={paperNotConnected}
              paperNewCore={paperNewCore}
              onToggleGate={toggleGate}
              onClearSignals={clearSignals}
              isGateRunning={isGateRunning}
              diagnostics={executionDaemon}
              diagnosticsPending={!liveLoaded}
              observe={observeView}
              observePending={observeEnv.loading && !observeEnv.data}
            />

            {/* 3B. Center Column: 全市场代币排序表 + 行内展开K线 */}
            <TokenTable
              tokens={tokens}
              expandedTokenId={expandedTokenId}
              onToggleExpand={handleToggleExpand}
              activeSignal={selectedSignal}
              onVisibleTokenIdsChange={noteVisibleRows}
              publicExtras={publicExtras}
            />

            {/* 3C. Right Sub-Column: 模拟实盘持仓（服务端账本） + 实盘只读仓位 */}
            <PositionPanel
              paperAccounts={paperAccounts}
              paperPositions={paperPositions}
              paperPending={paperPending}
              paperMissingReason={paperMissingReason}
              paperNote={paperNote}
              newEntryGate={newEntryGate}
              legacyLedger={legacyLedger}
              liveAccounts={liveAccounts}
              liveMissingReason={liveMissingReason}
              orderBusy={orderBusy}
              paperNotConnected={paperNotConnected}
              paperNewCore={paperNewCore}
              paperRetiredKeys={paperRetiredKeys}
              onSelectTokenSymbol={(sym) => {
                const target = tokens.find((t) => t.symbol === sym);
                if (target) setExpandedTokenId(target.id);
              }}
              onClosePosition={handleClosePosition}
              onOpenQuickPosition={handleOpenQuickPosition}
              orderNotice={hintNotice ?? orderNotice}
              onToggleGate={toggleGate}
              isGateRunning={isGateRunning}
              executionDaemon={executionDaemon}
            />
          </div>
        ) : currentNav === 'datacenter' ? (
          /* 数据中心 (按数据中心全页面草图.svg渲染) */
          <DataCenterView importedAt={importedAt} activeMarketKeys={tokens.map((token) => token.id)} />
        ) : currentNav === 'strategy' ? (
          /* 策略管理 (按策略管理全页面草图.svg渲染) */
          <StrategyManagementView />
        ) : (
          /* 回测复盘 (WORK-05：完整回测工作台 + 验收复盘) */
          <BacktestView />
        )}
        </ErrorBoundary>
      </div>

    </div>
  );
}
