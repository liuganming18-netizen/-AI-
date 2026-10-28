/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from 'react';
import { CryptoSignal, SignalArm, TokenMarketItem } from '../types/crypto';
import { TokenIcon } from './TokenIcon';
import type {
  ExecutionDaemonView,
  ObserveView,
  PaperPendingView,
  PaperPositionView,
} from '../api/liveTrading';
import ObservePanel from './ObservePanel';
// 09-WORK：历史研究信号只读折叠区（与当前交易区物理分离，无任何执行入口）
import ResearchSignalSection from './ResearchSignalSection';
import {
  SIGNALS_FAULT_TEXT,
  signalsStateKey,
  type SignalsState,
} from '../api/signalsState';
import {
  Target,
  ArrowRight,
  ShieldCheck,
  Search,
  Layers,
  Activity,
  Clock,
  Play,
  Pause,
  Trash2,
} from 'lucide-react';

interface SignalListPanelProps {
  signals: CryptoSignal[];
  signalsError?: string | null;
  tokens?: TokenMarketItem[];
  selectedSignalId: string | null;
  onSelectSignal: (signal: CryptoSignal) => void;
  /**
   * 服务端账本未平仓持仓（用于按 `signal_uid` 关联「实际成交」）。
   * **「已买入」标记只能来自这里**——浏览器不得自行造仓。
   */
  paperPositions?: PaperPositionView[];
  /** 服务端登记的未成交挂单（用于「计划下单」层）。 */
  paperPending?: PaperPendingView[];
  /** 服务端新开仓总闸（fail-closed）；用于如实说明「新开仓已被阻断」。 */
  newEntryGate?: { allowNewEntries: boolean; reason: string | null } | null;
  /**
   * **WORK-04 退役**：Paper 未接新共享核心 ⇒ `NOT_CONNECTED`。
   * 为真时本面板**不提供任何开仓路径**（信号仅展示，执行入口一律关闭）。
   */
  paperNotConnected?: boolean;
  /** 新共享核心策略 id（用于文案：为什么当前没有可执行入口）。 */
  paperNewCore?: string | null;
  /** 切换模拟实盘开始 / 暂停 */
  onToggleGate?: (action?: 'toggle' | 'start' | 'pause') => Promise<boolean>;
  /** 清理信号库 */
  onClearSignals?: (scope?: 'all' | 'expired' | 'current') => Promise<number>;
  /** 是否处于运行状态 */
  isGateRunning?: boolean;
  /**
   * 信号引擎逐币诊断（`/paper/status` → `signal_engine`）。
   *
   * **为什么需要它**（SIGNAL-UI-LINK-07）：没有它时，空列表只能显示一句
   * 「暂无实时信号」——用户无法区分「策略还在等条件」「候选产生了但过期/被门禁拒」
   * 「接口挂了」三种完全不同的情况。三种情况显示成一句话，等于把信息删掉。
   */
  diagnostics?: ExecutionDaemonView | null;
  /**
   * 实时信号区的**三态**（SIGNAL-UI-LINK-07-R1）：`loading` / `ok`（含正常 0 条）/ `fault`。
   *
   * **为什么必须传它而不是只传一句错误文案**：一句文案分不清「业务异常 200」和
   * 「HTTP 500」和「连不上」，也无法表达「还没加载」。传三态，页面才能把
   * 正常空列表 / 加载中 / 接口异常**三种情况显示成三种不同的东西**。
   */
  signalsState?: SignalsState | null;
  /**
   * 逐币诊断**是否仍在读取中**（SIGNAL-UI-LINK-07）。
   *
   * 为什么必须区分：`diagnostics === null` 有两种完全不同的含义 ——
   *   ① 第一次拉取还没回来（实测首次约 13 秒，因为要排在账本/实盘只读之后）；
   *   ② 拉取失败了。
   * 旧写法把两者都写成「守护状态接口未返回」，等于把「加载中」诬告成「故障」。
   * 有 `pending` 时先说「正在读取」，只有 `pending === false` 且仍为 null 才说「未取到」。
   */
  diagnosticsPending?: boolean;
  /**
   * 观察区（SIGNAL-OBSERVE-FIX-12）：处于观察状态的事件（BOX/SWEEP/RETEST）逐条明细。
   *
   * **为什么单独一条通道**：状态文件每 2 秒全量重写，塞 346 条明细约每天 10 GB 写盘，
   * 所以明细走独立低频接口 `/signals/observe`。观察区**与信号区并列常显**，
   * 不放在折叠诊断里 —— 用户明确要求「没有满足入场条件，不应导致观察区没有反应」。
   */
  observe?: ObserveView | null;
  /** 观察区是否仍在首次读取中（区分「加载中」与「真的没有」）。 */
  observePending?: boolean;
}

/** 数字格式化：null / 非有限值一律 '—'，绝不用 0 冒充真实值。 */
const fmtPrice = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 5 : 6;
  return v.toLocaleString(undefined, {
    minimumFractionDigits: Math.min(2, digits),
    maximumFractionDigits: digits,
  });
};

/** 将 ISO/UTC 时间字符串格式化为紧凑可读的 `MM-DD HH:mm UTC`，无法解析则原样返回。 */
const fmtCompactUtc = (raw: string | null | undefined): string => {
  if (!raw) return '—';
  const ms = Date.parse(raw);
  if (Number.isNaN(ms)) return raw;
  const d = new Date(ms);
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const da = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${mo}-${da} ${hh}:${mm} UTC`;
};

/** 结构类型与掩码 → 可读中文标签与色彩样式（白底浅色） */
const kindBadge = (
  kind: string | null | undefined,
  masks?: string[] | null,
): { label: string; cls: string } | null => {
  const maskList = (masks ?? []).filter((m): m is string => !!m);
  if (maskList.length > 1) {
    const names = maskList.map((m) =>
      m === 'base' ? '突破' : m === 'spring' ? 'Spring' : m === 'sc' ? 'SC' : m,
    );
    return {
      label: names.join('+'),
      cls: 'text-indigo-700 bg-indigo-50 border-indigo-200',
    };
  }
  const k = (kind ?? maskList[0] ?? '').toLowerCase();
  if (k === 'base' || k === 'donchian') {
    return {
      label: '突破',
      cls: 'text-sky-700 bg-sky-50 border-sky-200',
    };
  }
  if (k === 'spring') {
    return {
      label: 'Spring',
      cls: 'text-amber-700 bg-amber-50 border-amber-200',
    };
  }
  if (k === 'sc') {
    return {
      label: '恐慌SC',
      cls: 'text-purple-700 bg-purple-50 border-purple-200',
    };
  }
  if (k) {
    return {
      label: k,
      cls: 'text-neutral-400 bg-neutral-850 border-neutral-800',
    };
  }
  return null;
};

/**
 * 格式化信号时间：`MM-DD HH:mm (周期)`（无年份，紧凑专业）。
 *
 * **2026-10-15 任务四**：删除「无时间就用浏览器当前时刻」的兜底（那是**伪造的确认时刻**）。
 * 无真实时间 ⇒ 返回 `'—'`；无周期 ⇒ 不补默认 `1h`。
 */
const fmtSignalTime = (raw: string | null | undefined, timeframe: string | null | undefined): string => {
  if (!raw) return '—';
  const ms = Date.parse(raw);
  if (Number.isNaN(ms)) return '—';
  const d = new Date(ms);
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const tf = timeframe ? ` (${timeframe})` : '';
  return `${m}-${day} ${hh}:${mm}${tf}`;
};

/**
 * 解析信号展示价格：触发价 / 现价 / 止损价 / 目标价。
 *
 * **2026-10-15 闭环整改·阶段A（假展示清理）**：
 *   原实现含大量**凭空价格**：ARKM 硬编码 1.425 / 1.420 / 1.350 / 1.575；
 *   其余币种缺价时用 `trigger*1.003`、`entry±5%`、`entry+1.5R` 等**推导**补出，
 *   并把 `currentPrice` 直接当作「入场价」展示（**用现价冒充入场价**）。
 *   这些都属「无账本成交却画出买卖点」的假展示。
 *   现改为：**只用信号自带的真实字段**；缺失 ⇒ 返回 `null`，页面显示 `—`。
 *   另：**入场/成交价只由账本 fill 产生**，本面板不再以任何形式代填入场价；
 *   现价列如实标注为「现价」（取信号自带现价，或该币真实行情价）。
 */
const resolveSignalPrices = (
  signal: CryptoSignal,
  tokenPrice?: number | null,
): {
  triggerPrice: number | null;
  currentPrice: number | null;
  stopLossPrice: number | null;
  targetPrice: number | null;
} => {
  const fin = (v: number | null | undefined): number | null =>
    v !== null && v !== undefined && Number.isFinite(v) ? v : null;
  const anySig = signal as any;
  return {
    triggerPrice: fin(signal.triggerPrice ?? signal.detectionLow ?? anySig.breakout_price ?? anySig.price),
    currentPrice: fin(signal.currentPrice ?? tokenPrice ?? anySig.price),
    stopLossPrice: fin(signal.stopLossPrice ?? anySig.stop_loss),
    targetPrice: fin(signal.targetPrice),
  };
};

/**
 * 解析交易理由文本。
 *
 * **2026-10-15 闭环整改·阶段A**：删除全部**编造理由**（原实现在缺理由时拼出
 * 「大户持仓做多(1.62)背离散户(0.81) · OI增24% · 现价贴近巨鲸均价(1.40) · 插针收回」，
 * 其中 1.62 / 0.81 / 24% / 1.40 均为**常量兜底**，并非真实证据）。
 * 现只**原样透出后端结构化证据**；无结构化证据 ⇒ 返回 `null`，页面显示
 * 「—（无结构化理由，缺失即未知）」。AI 只解释已有结构化证据，不补造。
 */
const resolveReason = (signal: CryptoSignal): string | null => {
  const raw = (signal.actionableReason ?? signal.validityReason ?? '').trim();
  if (!raw) return null;
  if (raw === 'enterable' || raw === 'base') return null; // 状态代号不是理由
  return raw;
};

/**
 * 信号区（**当前交易信号**，2026-10-14 信号区清理与信息展示深化）。
 *
 * 本面板只呈现「当前有效信号」：
 *   · 数据入口已按 `scope=current` 请求（后端在分页 LIMIT 与 total/has_more **之前**过滤）；
 *   · 这里再保留一层**防御性判定** `isCurrentSignal` —— 即使后端口径回归，
 *     历史研究 / 启动前仅展示 / 结构提示（display_only / not_this_arm_structure 等）
 *     也不会重新出现在本区（含刷新、轮询与任何筛选切换）。
 */
export const SignalListPanel: React.FC<SignalListPanelProps> = ({
  signals,
  signalsError = null,
  signalsState = null,
  tokens = [],
  selectedSignalId,
  onSelectSignal,
  paperPositions = [],
  paperPending = [],
  newEntryGate = null,
  paperNotConnected = false,
  paperNewCore = null,
  onToggleGate,
  onClearSignals,
  isGateRunning = false,
  diagnostics = null,
  diagnosticsPending = false,
  observe = null,
  observePending = false,
}) => {
  /**
   * **WORK-04 退役**：旧策略臂（3a–3d）筛选入口已下线。
   * 保留 `kindFilter`（结构类型）与搜索；不再有「按旧臂筛选」这一可运行入口。
   */
  const [kindFilter, setKindFilter] = useState<'ALL' | 'base' | 'spring' | 'sc'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  /**
   * 当前有效信号判定：
   * 确保服务端 `/signals?scope=current` 返回的主策略有效信号正常呈现，杜绝双重误杀。
   */
  const isCurrentSignal = (s: CryptoSignal): boolean => {
    // 排除测试合成伪数据
    if (s.id && (s.id.startsWith('sig_closeloop_') || s.id.startsWith('sig_ui_test_'))) return false;
    // 凡由当前区接口返回的有效信号均呈现
    if (s.origin === 'live' || s.origin === 'recovered') return true;
    if (s.executableState === 'enterable' || s.actionable === true) return true;
    return s.role === 'enterable_arm_entry';
  };

  /**
   * 账本联动索引：`signal_uid → 持仓 / 挂单`（2026-10-15 任务四）。
   *
   * 「已买入」标记只能由**服务端账本成交**（`paperPositions`）产生；
   * 「计划下单」只能由**服务端登记的挂单**（`paperPending`）产生。前端不造任何一条。
   */
  const posBySignalUid = useMemo(() => {
    const m = new Map<string, PaperPositionView[]>();
    for (const p of paperPositions) {
      if (!p.signalUid) continue;
      const arr = m.get(p.signalUid) ?? [];
      arr.push(p);
      m.set(p.signalUid, arr);
    }
    return m;
  }, [paperPositions]);

  const pendBySignalUid = useMemo(() => {
    const m = new Map<string, PaperPendingView[]>();
    for (const p of paperPending) {
      if (!p.signalUid) continue;
      const arr = m.get(p.signalUid) ?? [];
      arr.push(p);
      m.set(p.signalUid, arr);
    }
    return m;
  }, [paperPending]);

  const tokenMap = useMemo(() => {
    const map = new Map<string, TokenMarketItem>();
    tokens.forEach((t) => {
      map.set(t.symbol.toUpperCase(), t);
      if (t.originalSymbol) map.set(t.originalSymbol.toUpperCase(), t);
    });
    return map;
  }, [tokens]);

  const currentSignals = useMemo(() => signals.filter(isCurrentSignal), [signals]);

  /** 同买点合卡：一张卡列全部命中臂与各臂去向。 */
  const armList = (s: CryptoSignal): SignalArm[] => {
    if (s.arms && s.arms.length > 0) return s.arms;
    if (s.arm) {
      return [
        {
          arm: s.arm,
          signalId: s.id,
          status: s.status,
          executableState: s.executableState ?? null,
          actionable: s.actionable ?? null,
          actionableReason: s.actionableReason ?? null,
          structuralKind: s.structuralKind ?? null,
          sourceMasks: s.sourceMasks ?? null,
          executionLabel: s.executionLabel ?? null,
        },
      ];
    }
    return [];
  };

  /** 卡上臂数合计（用于标题「N 张卡 · M 臂」）。 */
  const totalArms = useMemo(
    () =>
      currentSignals.reduce(
        (n, s) => n + (s.armCount ?? (s.arms ? s.arms.length : s.arm ? 1 : 0)),
        0,
      ),
    [currentSignals],
  );

  /**
   * 按结构类型与搜索词过滤当前有效信号。
   *
   * **WORK-04 退役**：删除「按旧策略臂（3a–3d）筛选」——旧臂不再是可运行入口。
   */
  const filteredSignals = useMemo(() => {
    const q = searchQuery.trim().toUpperCase();
    return currentSignals.filter((s) => {
      const arms = armList(s);
      if (kindFilter !== 'ALL') {
        const k = (s.structuralKind ?? '').toLowerCase();
        const masks = (s.sourceMasks ?? []).map((m) => m.toLowerCase());
        const armMatch = arms.some(
          (a) =>
            (a.structuralKind ?? '').toLowerCase() === kindFilter ||
            (a.sourceMasks ?? []).some((m) => m.toLowerCase() === kindFilter),
        );
        if (k !== kindFilter && !masks.includes(kindFilter) && !armMatch) {
          return false;
        }
      }
      if (q) {
        const sym = (s.tokenSymbol ?? '').toUpperCase();
        const rawSym = (s.rawSymbol ?? '').toUpperCase();
        const ex = (s.exchange ?? '').toUpperCase();
        if (!sym.includes(q) && !rawSym.includes(q) && !ex.includes(q)) {
          return false;
        }
      }
      return true;
    });
  }, [currentSignals, kindFilter, searchQuery]);

  /** 六态 → 展示标签与配色（未知/null 显式标「未决」，不冒充失效）。 */
  const statusLabel = (s: CryptoSignal): { text: string; cls: string } => {
    switch (s.status) {
      case 'confirmed':
        return { text: '已确认', cls: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' };
      case 'candidate':
        return { text: '候选', cls: 'bg-sky-500/20 text-sky-400 border-sky-500/30' };
      case 'waiting_retest':
        return { text: '待回踩', cls: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };
      case 'invalidated':
        return { text: '已失效', cls: 'bg-rose-500/20 text-rose-400 border-rose-500/30' };
      case 'expired':
        return { text: '已过期', cls: 'bg-rose-500/20 text-rose-400 border-rose-500/30' };
      default:
        return { text: '未决', cls: 'bg-neutral-800 text-neutral-300 border-neutral-700' };
    }
  };

  /** 执行状态（后端判定；用可读中文，不暴露内部代号）。 */
  const execLabel = (s: CryptoSignal): string => {
    if (!s.executableState) return '—';
    if (s.executableState === 'enterable') return '可执行（满足入场门禁）';
    if (s.executableState.startsWith('blocked')) return '阻断（不可开仓）';
    return '不可执行';
  };

  /** 某一臂的去向（合卡卡面上的短标签）。 */
  const armOutcome = (a: SignalArm): { text: string; textCls: string } => {
    if (a.executableState === 'enterable') return { text: '可开仓', textCls: 'text-emerald-300' };
    if (a.executableState && a.executableState.startsWith('blocked')) {
      return { text: '阻断', textCls: 'text-rose-300' };
    }
    return { text: '不可执行', textCls: 'text-neutral-400' };
  };

  const armShortKind = (a: SignalArm): string => {
    const k = (a.structuralKind ?? '').toLowerCase();
    if (k === 'spring') return '·Spring';
    if (k === 'sc') return '·SC';
    if (k === 'base') return '·突破';
    return '';
  };

  /** 结构类型可读名（结构发现阶段）。 */
  const kindName = (s: CryptoSignal): string => {
    const k = (s.structuralKind ?? '').toLowerCase();
    if (k === 'spring') return 'Spring（威科夫弹簧）';
    if (k === 'sc') return 'SC（恐慌抛售）';
    if (k === 'base' || k === 'donchian') return '突破（唐奇安）';
    return s.structuralKind ?? '—';
  };

  /**
   * **四阶段事件链**（2026-10-15 任务四）——逐阶段只引用真实字段：
   *   ① 结构发现：结构类型 + 结构证据 bar（`barOpenTime`/`barCloseTime`，ms 纪元）
   *   ② 信号确认：`confirmedAt`/`eventTime` + 六态
   *   ③ 计划下单：服务端登记的**未成交挂单**（按 `signal_uid` 关联；无 ⇒ 未登记）
   *   ④ 实际成交：服务端账本**已成交持仓**（按 `signal_uid` 关联；无 ⇒ 未成交）
   * 任何阶段无真实数据一律显示 `—`，绝不推断下一阶段。
   */
  const eventChain = (s: CryptoSignal) => {
    const pos = posBySignalUid.get(s.id) ?? [];
    const pend = pendBySignalUid.get(s.id) ?? [];
    const barOpen = s.barOpenTime ? new Date(s.barOpenTime).toISOString().slice(0, 16).replace('T', ' ') : null;
    const barClose = s.barCloseTime ? new Date(s.barCloseTime).toISOString().slice(0, 16).replace('T', ' ') : null;
    return {
      structure: {
        filled: Boolean(barOpen || barClose),
        text: kindName(s),
        bar: barOpen || barClose ? `结构bar ${barOpen ?? '—'}→${barClose ?? '—'} UTC` : '结构bar 无记录',
      },
      confirm: {
        text: s.confirmedAt ?? s.eventTime ?? null,
        status: statusLabel(s).text,
      },
      plan: pend.length > 0
        ? { filled: true, text: `已登记挂单 ${pend.length} 笔（未成交）`, ids: pend.map((p) => `#${p.pendingId}`).join(' ') }
        : { filled: false, text: '未登记挂单', ids: '' },
      fill: pos.length > 0
        ? { filled: true, text: `已成交（账本）`, ids: pos.map((p) => `${p.arm}#${p.positionId}`).join(' ') }
        : { filled: false, text: '未成交', ids: '' },
    };
  };

  const gateBlocked = !!newEntryGate && !newEntryGate.allowNewEntries;

  /**
   * 有效三态（SIGNAL-UI-LINK-07-R1）。
   *
   * 优先用 `signalsState`；调用方只给了旧的 `signalsError` 字符串时，按
   * 「未知类型故障」降级 —— 至少不会再把错误当空列表。
   * **`null` 表示「调用方没提供状态」**（老用法），此时按 `ok` 处理以保持向后兼容。
   */
  const effSignalsState: SignalsState | null =
    signalsState ??
    (signalsError
      ? { kind: 'fault', fault: 'error', message: signalsError, status: null, detail: null }
      : null);

  return (
    <div
      className="workbench-side-panel workbench-signal-panel bg-neutral-900 border-r border-neutral-800 flex flex-col justify-between shrink-0 select-none overflow-hidden"
      data-testid="signal-panel"
      data-signal-count={currentSignals.length}
      data-signal-scope="current"
      data-signals-state={signalsStateKey(effSignalsState)}
    >
      <div className="workbench-signal-content flex flex-col flex-1 overflow-hidden">
        {/* Header + 极简搜索区（白底无特效） */}
        <div className="p-3 border-b border-neutral-800 space-y-2 bg-neutral-900">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold text-neutral-100 flex items-center gap-1.5 uppercase tracking-wider">
              <span>交易信号</span>
            </h3>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-neutral-400 font-mono">
                {filteredSignals.length} 笔
              </span>
              {/* 实盘模拟总闸（与右栏「模拟实盘持仓」旁那颗是**同一个开关**，同一份状态）。
                  只表达「现在是什么状态」，不表达「点下去会怎样」——后者由 title 说明。
                  配色只给中性：整块铺绿/铺琥珀会让一个开关的视觉权重压过旁边的信号列表，
                  而状态是二值的，一颗 6px 的点就够了。动作留给鼠标悬停和 title。 */}
              {onToggleGate && (
                <button
                  type="button"
                  onClick={() => onToggleGate()}
                  aria-pressed={isGateRunning}
                  title={isGateRunning ? "当前运行中：点击暂停（阻断新开仓，已有持仓继续盯市）" : "当前已暂停：点击启动（放开新开仓）"}
                  className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium bg-[var(--ds-surface-2)] text-[var(--ds-text-2)] border transition-colors hover:bg-[var(--ds-surface-3)] hover:text-[var(--ds-text)] ${
                    isGateRunning ? 'border-[var(--ds-border)]' : 'border-[var(--ds-warn)]'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 shrink-0 rounded-full ${isGateRunning ? 'bg-[var(--ds-pos)]' : 'bg-[var(--ds-warn)]'}`}
                    aria-hidden="true"
                  />
                  <span>{isGateRunning ? '运行中' : '已暂停'}</span>
                </button>
              )}
              {/* 清理信号选项按键 */}
              {onClearSignals && (
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm("确定要清理当前所有历史信号记录吗？")) {
                      void onClearSignals('all');
                    }
                  }}
                  title="清理历史信号数据"
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border border-neutral-700 bg-neutral-800 text-neutral-400 hover:text-rose-300 hover:border-rose-900 hover:bg-rose-950/40 transition-colors"
                >
                  <Trash2 className="w-2.5 h-2.5" />
                  <span>清理</span>
                </button>
              )}
            </div>
          </div>

          {/* 币种搜索 + 结构类型快筛 */}
          <div className="flex items-center gap-1.5">
            <div className="relative flex-1">
              <Search className="w-3.5 h-3.5 text-neutral-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索代币..."
                className="w-full bg-neutral-850 border border-neutral-800 focus:bg-neutral-900 focus:border-neutral-700 rounded pl-7 pr-2 py-1 text-[11px] text-neutral-300 placeholder-neutral-500 outline-none font-mono"
              />
            </div>
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value as 'ALL' | 'base' | 'spring' | 'sc')}
              className="bg-neutral-850 border border-neutral-800 rounded px-2 py-1 text-[11px] text-neutral-300 outline-none cursor-pointer"
            >
              <option value="ALL">全部形态</option>
              <option value="base">突破</option>
              <option value="spring">Spring</option>
              <option value="sc">恐慌SC</option>
            </select>
          </div>
        </div>

        {/* WORK-04 退役：Paper/Live 未接新共享核心 ⇒ 信号仅展示，执行入口一律关闭 */}
        {paperNotConnected && (
          <div
            data-testid="signal-not-connected"
            className="mx-2 mt-1.5 px-2 py-1 rounded border border-amber-500/30 bg-amber-500/10 text-[10px] text-amber-300 flex items-center justify-between"
          >
            <span className="flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 shrink-0 text-amber-400" />
              <span>模拟实盘未连接 · 信号仅供展示（禁止开仓）</span>
            </span>
          </div>
        )}

        {/* 观察区（SIGNAL-OBSERVE-FIX-12）：与信号区**并列常显**，不在折叠诊断里。
            观察与执行分开判定：这里只展示「正在等入场条件」的标的，不构成下单。 */}
        <ObservePanel view={observe} pending={observePending} />

        {/* Signals List */}
        <div className="flex-1 overflow-y-auto p-2.5 space-y-2.5 bg-neutral-850/50">
          {effSignalsState?.kind === 'fault' ? (
            <SignalsFaultBox state={effSignalsState} />
          ) : effSignalsState?.kind === 'loading' ? (
            <div
              data-testid="signal-loading"
              className="mt-6 mx-1 p-5 rounded-lg border border-neutral-800 bg-neutral-900 text-center space-y-2"
            >
              <div className="text-xs font-semibold text-neutral-300">正在读取实时信号…</div>
              <div className="text-[10px] text-neutral-500">
                首次响应回来之前不显示「暂无信号」—— 未取到不等于没有。
              </div>
            </div>
          ) : currentSignals.length === 0 ? (
            <EmptyStateWithDiagnostics diagnostics={diagnostics} pending={diagnosticsPending} />
          ) : filteredSignals.length === 0 ? (
            <div className="mt-6 mx-1 p-4 rounded-lg border border-dashed border-neutral-800 bg-neutral-900 text-center space-y-1.5">
              <p className="text-[11px] text-neutral-400">当前筛选条件下无匹配信号</p>
              <button
                type="button"
                onClick={() => {
                  setKindFilter('ALL');
                  setSearchQuery('');
                }}
                className="text-[10px] text-emerald-600 hover:underline"
              >
                重置筛选条件
              </button>
            </div>
          ) : (
            filteredSignals.map((signal) => {
              const isSelected = selectedSignalId === signal.id;
              const isLong = signal.direction === 'long';
              const isShort = signal.direction === 'short';
              const kBadge = kindBadge(signal.structuralKind, signal.sourceMasks);

              const matchedToken =
                tokenMap.get((signal.tokenSymbol || '').toUpperCase()) ||
                tokenMap.get((signal.rawSymbol || '').toUpperCase());

              const prices = resolveSignalPrices(signal, matchedToken?.price);
              const reasonText = resolveReason(signal);
              const chain = eventChain(signal);
              const filled = chain.fill.filled;
              const timeDisplay = fmtSignalTime(
                signal.confirmedAt ?? signal.eventTime ?? signal.triggerTime,
                signal.timeframe,
              );

              return (
                <div
                  key={signal.id}
                  onClick={() => onSelectSignal(signal)}
                  data-testid="signal-row"
                  data-origin={signal.origin ?? ''}
                  data-executable-state={signal.executableState ?? ''}
                  data-symbol={signal.tokenSymbol}
                  data-signal-uid={signal.id}
                  data-direction={signal.direction ?? ''}
                  data-confirmed-at={signal.confirmedAt ?? ''}
                  data-filled={filled ? '1' : '0'}
                  className={`p-3 rounded-lg border cursor-pointer transition-colors space-y-2.5 ${
                    isSelected
                      ? 'bg-emerald-50/20 border-2 border-emerald-600'
                      : 'bg-neutral-900 border border-neutral-800 hover:border-neutral-700'
                  }`}
                >
                  {/* 第 1 行：代币图标 + 代币名称 + 紧凑复制按钮 + 做多标签 */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <TokenIcon symbol={signal.tokenSymbol} size="sm" />
                      <span className="font-bold text-neutral-100 text-[14px] font-mono tracking-tight shrink-0">
                        {signal.tokenSymbol}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void navigator.clipboard?.writeText(signal.id);
                        }}
                        className="text-neutral-500 hover:text-neutral-300 p-0.5 rounded text-[9px] font-mono shrink-0 transition-colors"
                        title={`复制ID: ${signal.id}`}
                      >
                        [ID]
                      </button>
                      {/* 「已买入」只来自服务端账本成交 */}
                      {filled && (
                        <span
                          className="text-[9px] px-1 py-0.2 rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 font-sans font-medium shrink-0"
                          title={chain.fill.ids}
                        >
                          已买入
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      {((signal as any).resonance_score != null || (signal as any).resonanceScore != null) && (
                        <span
                          className="text-[9px] px-1.5 py-0.5 rounded font-mono font-bold border text-amber-300 bg-amber-500/15 border-amber-500/40"
                          title={`多因子全数据共振得分: ${(signal as any).resonance_score ?? (signal as any).resonanceScore}分 | 仓位调节: ${(signal as any).position_multiplier ?? (signal as any).positionMultiplier ?? 1.0}x`}
                        >
                          共振 {(signal as any).resonance_score ?? (signal as any).resonanceScore}分
                        </span>
                      )}
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded font-medium border ${
                          isLong
                            ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
                            : isShort
                              ? 'text-rose-400 bg-rose-500/10 border-rose-500/30'
                              : 'text-neutral-400 bg-neutral-800 border-neutral-700'
                        }`}
                      >
                        {isLong ? '做多' : isShort ? '做空' : '观望'}
                      </span>
                    </div>
                  </div>

                  {/* 第 2 行：时间与形态标签 */}
                  <div className="flex items-center justify-between text-[10px] font-mono text-neutral-400">
                    <span>{timeDisplay}</span>
                    {kBadge && (
                      <span
                        className={`text-[9px] px-1 py-0.2 rounded border font-sans font-medium ${kBadge.cls}`}
                      >
                        {kBadge.label}
                      </span>
                    )}
                  </div>

                  {/* 第 3 行：四阶段紧凑状态胶囊条 */}
                  <div className="flex items-center justify-between gap-1 text-[9px] py-1 px-1.5 rounded bg-neutral-950/70 border border-neutral-800 font-sans">
                    <span
                      className={`truncate max-w-[24%] ${chain.structure.filled ? 'text-neutral-300 font-medium' : 'text-neutral-600'}`}
                      title={`结构: ${chain.structure.text}`}
                    >
                      1.发现
                    </span>
                    <span className="text-neutral-700">→</span>
                    <span
                      className={`truncate max-w-[24%] ${chain.confirm.status === '已确认' ? 'text-sky-400 font-medium' : 'text-neutral-600'}`}
                      title={`确认: ${chain.confirm.status} ${chain.confirm.text || ''}`}
                    >
                      2.确认
                    </span>
                    <span className="text-neutral-700">→</span>
                    <span
                      className={`truncate max-w-[24%] ${chain.plan.filled ? 'text-amber-400 font-medium' : 'text-neutral-600'}`}
                      title={`下单: ${chain.plan.text} ${chain.plan.ids || ''}`}
                    >
                      3.计划
                    </span>
                    <span className="text-neutral-700">→</span>
                    <span
                      className={`truncate max-w-[24%] ${chain.fill.filled ? 'text-emerald-400 font-bold' : 'text-neutral-600'}`}
                      title={`成交: ${chain.fill.text} ${chain.fill.ids || ''}`}
                    >
                      4.成交
                    </span>
                  </div>

                  {/* 第 3.5 行：共振/理由提示（仅在有明确理由时单行轻量展示） */}
                  {reasonText && (
                    <div className="text-[10px] leading-tight text-neutral-300 bg-neutral-850/80 px-2 py-1 rounded border border-neutral-800 font-sans truncate" title={reasonText}>
                      <span className="text-neutral-400 mr-1">共振:</span>
                      {reasonText}
                    </div>
                  )}

                  {/* 第 4 行：触发价 现价 止损价 目标价
                      —— 仅展示信号自带真实字段；缺失显示 `—`。
                      **入场/成交价不在此展示**（只由服务端账本 fill 产生，杜绝现价冒充入场价）。 */}
                  <div className="grid grid-cols-4 gap-1.5 pt-1.5 text-[11px] font-mono border-t border-neutral-800">
                    <div>
                      <div className="text-[10px] text-neutral-500 font-sans">触发价</div>
                      <div className="text-neutral-300 font-medium mt-0.5">
                        {fmtPrice(prices.triggerPrice)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-neutral-500 font-sans">现价</div>
                      <div className="text-emerald-600 font-bold mt-0.5">
                        {fmtPrice(prices.currentPrice)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-neutral-500 font-sans">止损价</div>
                      <div className="text-rose-600 font-medium mt-0.5">
                        {fmtPrice(prices.stopLossPrice)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-neutral-500 font-sans">目标价</div>
                      <div className="text-sky-600 font-medium mt-0.5">
                        {fmtPrice(prices.targetPrice)}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
        {/* 历史研究信号（09-WORK）：**放在当前区列表容器之外**的只读折叠区。
            这样当前区的 `signal-row` 与 `currentSignals.length` 完全不受研究行影响，
            研究记录只可能出现在 `research-signal-row` 上。 */}
        <ResearchSignalSection />
      </div>

      {/* 底部紧凑状态 */}
      <div className="px-3 py-2 border-t border-neutral-800 bg-neutral-900 text-[10px] text-neutral-400 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-neutral-400">
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
          <span>{paperNotConnected ? '信号仅展示 · 执行入口已关闭' : '点击信号联动定位行情与开仓'}</span>
        </span>
        <span className="font-mono text-neutral-500">实时推送</span>
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// 空态诊断（SIGNAL-UI-LINK-07）
// ─────────────────────────────────────────────────────────────────────────────
/**
 * 「没有可执行信号」时**必须说清是哪一种没有**。
 *
 * 三种情况在旧版里全显示成一句「暂无实时信号」，等于把信息删掉：
 *   ① 引擎在正常等待（箱体没成形 / 冷却中 / 等下一根 4h）；
 *   ② 候选产生过，但过期或早于启用时点 ⇒ 按纪律不补开；
 *   ③ 接口失败（这一种由 `signalsError` 分支单独表达，不会落到本组件）。
 *
 * 数据全部来自 `/paper/status` 的 `signal_engine`，**不在前端重算任何判据**；
 * 字段缺失时显式写「未提供」，不拿 0 或「无」冒充。
 */
const STAGE_CN: Record<string, string> = {
  IDLE: '空转',
  BOX: '箱体观察',
  SWEEP: '已扫低',
  RETEST: '待突破',
  BREAKOUT_EVAL: '突破评估',
  CANDIDATE: '候选',
};

const IDLE_CN: Record<string, string> = {
  IDLE_COOLING: '冷却中（距上次事件不足 24h）',
  IDLE_WAIT_BOX: '箱体条件未满足（宽度或 ER 超阈）',
  IDLE_READY_BOX: '条件已满足，等下一根 4h 收盘建档',
  IDLE_DEAD_BARS: '数据坏死（连续零成交平价 bar）',
  IDLE_ATR_INVALID: 'ATR 无效（无法定价）',
};

const DISCARD_CN: Record<string, string> = {
  before_paper_epoch_no_backfill: '早于模拟盘启用时点（按纪律不补开）',
  missed_fill_moment_no_backfill: '已错过成交时点（超出 1h 成交时效）',
};

const DiagRow: React.FC<{ label: string; value: React.ReactNode; mono?: boolean }> = ({
  label,
  value,
  mono = true,
}) => (
  <div className="flex items-start justify-between gap-2">
    <span className="text-neutral-500 shrink-0">{label}</span>
    <span className={`text-right text-neutral-300 break-all ${mono ? 'font-mono' : ''}`}>{value}</span>
  </div>
);

const EmptyStateWithDiagnostics: React.FC<{
  diagnostics: ExecutionDaemonView | null;
  pending?: boolean;
}> = ({ diagnostics, pending = false }) => {
  const [showDiscarded, setShowDiscarded] = useState(false);
  const d = diagnostics;

  const funnel = (d?.funnel ?? null) as {
    since_start?: Record<string, number>;
    history?: Record<string, number>;
    last_candidate_utc?: string | null;
  } | null;
  const idle = (d?.idleBreakdown ?? null) as {
    counts?: Record<string, number>;
    sample?: Record<string, { reason?: string; actual?: string; threshold?: string }>;
  } | null;

  const ss = funnel?.since_start ?? null;
  const hi = funnel?.history ?? null;
  const num = (v: unknown): string => (typeof v === 'number' ? String(v) : '—');

  const stageText = d?.stateCounter
    ? Object.entries(d.stateCounter)
        .filter(([, n]) => typeof n === 'number' && n > 0)
        .sort((a, b) => (b[1] as number) - (a[1] as number))
        .map(([k, n]) => `${STAGE_CN[k] ?? k} ${n}`)
        .join(' · ')
    : '未提供';

  const idleText = idle?.counts
    ? Object.entries(idle.counts)
        .filter(([, n]) => typeof n === 'number' && n > 0)
        .map(([k, n]) => `${IDLE_CN[k] ?? k} ${n}`)
        .join('；')
    : '未提供';

  const sampleEntry = idle?.sample ? Object.entries(idle.sample)[0] : undefined;
  const discarded = d?.discardedCandidates ?? [];

  return (
    <div
      data-testid="signal-empty"
      className="mt-6 mx-1 p-6 rounded-lg border border-dashed border-neutral-800 bg-neutral-900/60 text-center"
    >
      <div className="text-xs font-medium text-neutral-300">暂无交易信号</div>

      <details className="mt-3 group text-left">
        <summary className="text-[10px] text-neutral-500 hover:text-neutral-300 cursor-pointer select-none inline-flex items-center gap-1">
          <span>查看诊断与历史候选明细</span>
          <span className="text-[9px] text-neutral-600 group-open:hidden">▼</span>
          <span className="text-[9px] text-neutral-600 hidden group-open:inline">▲</span>
        </summary>

        <div className="mt-2.5 pt-2.5 border-t border-neutral-800/80">
          <div className="text-[10px] text-neutral-500 mb-2 leading-relaxed">
            诊断说明：展示引擎处理进度与未触发原因。历史候选均已丢弃不可执行，不混入当前有效信号。
          </div>

          {!d ? (
            pending ? (
              <div
                className="py-1 text-[10px] text-neutral-400 leading-relaxed"
                data-testid="signal-empty-pending"
              >
                正在读取逐币诊断（守护状态接口首次返回较慢）…
              </div>
            ) : (
              <div
                className="py-1 text-[10px] text-amber-400 leading-relaxed"
                data-testid="signal-empty-nodiag"
              >
                逐币诊断未取到（解释暂时缺失，不代表接口故障）。
              </div>
            )
          ) : (
            <div className="space-y-1.5 text-[10px]" data-testid="signal-empty-diagnostics">
              <DiagRow
                label="最近处理到"
                value={`1h ${fmtCompactUtc(d.watermark?.h1Utc)} · 4h ${fmtCompactUtc(d.watermark?.h4Utc)}`}
              />
              <DiagRow
                label="行情最新"
                value={`1h ${fmtCompactUtc(d.marketLatest?.close1hUtc)} · 4h ${fmtCompactUtc(
                  d.marketLatest?.close4hUtc,
                )}`}
              />
              <DiagRow label="当前阶段" value={stageText} mono={false} />
              <DiagRow
                label="本进程漏斗"
                value={`箱体 ${num(ss?.BOX)} → 扫低 ${num(ss?.SWEEP)} → 回踩 ${num(
                  ss?.RETEST,
                )} → 候选 ${num(ss?.CANDIDATE)}`}
              />
              <DiagRow
                label="历史回放"
                value={`箱体 ${num(hi?.BOX)} → 扫低 ${num(hi?.SWEEP)} → 回踩 ${num(
                  hi?.RETEST,
                )} → 候选 ${num(hi?.CANDIDATE)}（丢弃 ${num(d.replayCandidates)} · 恢复 ${num(
                  d.recoveredCandidates,
                )}）`}
              />
              <DiagRow
                label="最近候选"
                value={
                  d.lastCandidateUtc
                    ? `${fmtCompactUtc(d.lastCandidateUtc)}`
                    : '本次启动后没有产生候选'
                }
              />
              <DiagRow label="空转原因" value={idleText} mono={false} />
              {sampleEntry ? (
                <DiagRow
                  label="样例"
                  value={`${sampleEntry[0]}：${IDLE_CN[sampleEntry[1]?.reason ?? ''] ?? sampleEntry[1]?.reason ?? '—'}（实际 ${
                    sampleEntry[1]?.actual ?? '—'
                  } / 阈值 ${sampleEntry[1]?.threshold ?? '—'}）`}
                  mono={false}
                />
              ) : null}

              {discarded.length > 0 ? (
                <div className="pt-1.5 border-t border-neutral-800">
                  <button
                    type="button"
                    data-testid="signal-discarded-toggle"
                    onClick={() => setShowDiscarded((v) => !v)}
                    className="text-[10px] text-sky-400 hover:text-sky-300 hover:underline"
                  >
                    {showDiscarded ? '收起' : '展开'}历史回放候选明细（{discarded.length} 条，均不可执行）
                  </button>
                  {showDiscarded ? (
                    <div className="mt-1.5 max-h-56 overflow-y-auto space-y-1" data-testid="signal-discarded-list">
                      {discarded.map((c) => (
                        <div
                          key={c.candidateId}
                          data-testid="signal-discarded-row"
                          data-candidate-id={c.candidateId}
                          className="rounded border border-neutral-800 bg-neutral-950 px-2 py-1 font-mono text-[9px] text-neutral-400 space-y-0.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-neutral-200">{c.symbol}</span>
                            <span>{fmtCompactUtc(c.snapshotUtc)}</span>
                          </div>
                          <div className="text-neutral-500 break-all">{c.candidateId}</div>
                          <div className="text-rose-400/80 font-sans">
                            {c.discardReasons.length > 0
                              ? c.discardReasons.map((r) => DISCARD_CN[r] ?? r).join('；')
                              : '原因未提供'}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </details>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// 故障块（SIGNAL-UI-LINK-07-R1）
// ─────────────────────────────────────────────────────────────────────────────
/**
 * 接口出问题时**必须说清是哪一种问题**。
 *
 * 旧实现把所有异常压成一句「实时信号接口读取异常」，而业务异常（HTTP 200 +
 * `quality.status=no_watermark`）连这句都没有 —— 直接被当成空列表。
 *
 * 六种故障各有独立标题与「这不是什么」提示，`data-error-kind` 供验收脚本确定性读取。
 */
const SignalsFaultBox: React.FC<{ state: Extract<SignalsState, { kind: 'fault' }> }> = ({
  state,
}) => {
  const t = SIGNALS_FAULT_TEXT[state.fault];
  return (
    <div
      data-testid="signal-error"
      data-error-kind={state.fault}
      data-error-status={state.status ?? ''}
      className="mt-4 mx-1 p-3 rounded-lg border border-rose-500/30 bg-rose-950/20 text-center space-y-1.5"
    >
      <div className="flex items-center justify-center gap-2 text-xs font-semibold text-rose-400">
        <span>信号读取失败</span>
        <span className="text-rose-500/50">·</span>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="text-rose-300 hover:underline cursor-pointer"
        >
          重试
        </button>
      </div>
      <details className="text-left group pt-1 border-t border-rose-900/30">
        <summary className="text-[10px] text-neutral-400 hover:text-neutral-200 cursor-pointer select-none inline-flex items-center gap-1">
          <span>错误详情 ({t.title})</span>
          <span className="text-[9px] text-neutral-500">▼</span>
        </summary>
        <div className="mt-1.5 space-y-1 text-[10px] text-neutral-400 font-mono">
          <div className="break-all">{state.message}</div>
          <div className="text-neutral-500 font-sans">{t.hint}</div>
          {state.detail && state.detail !== state.message ? (
            <div className="text-neutral-500 break-all text-[9px] pt-1">
              接口原文: {state.detail}
            </div>
          ) : null}
        </div>
      </details>
    </div>
  );
};
