/**
 * 回测 run 的元信息解析
 * ----------------------------------------------------------------------------
 * 为什么需要：列表接口**不返回可信的时间与窗口**。
 *   · `created_at / started_at / finished_at` 是读取时刻兜底（576 条 distinct = 1）；
 *   · `window.win_start_ms / win_end_ms` 恒为 null。
 * 唯一可信的时间线索是 `run_id` 末尾的 epoch 秒（服务端落盘时写入）。
 * 因此列表上显示的是「运行时间」，真实回测窗口要等选中后从 `chart.window` 取。
 */

export interface RunIdParts {
  strategy: string;
  symbol: string;
  /** 运行时刻（毫秒）；解析不出为 null */
  epochMs: number | null;
}

/** `run_WX_EVENT_TREND_V0_1_CYBERUSDT_1792173927` → 三段 */
export function parseRunId(runId: string | null | undefined): RunIdParts {
  if (!runId) return { strategy: '', symbol: '', epochMs: null };
  const parts = runId.split('_');
  const last = parts[parts.length - 1];
  const sec = Number(last);
  const hasEpoch = Number.isFinite(sec) && sec > 1_000_000_000;
  if (hasEpoch) {
    const rest = parts.slice(1, -1);
    // 标的总是最后一段（大写字母 + USDT 之类），策略名可能含下划线
    const symbol = rest[rest.length - 1] ?? '';
    return {
      strategy: rest.slice(0, -1).join('_'),
      symbol,
      epochMs: sec * 1000,
    };
  }
  return { strategy: parts.slice(1).join('_'), symbol: '', epochMs: null };
}

export function runEpochMs(runId: string | null | undefined): number | null {
  return parseRunId(runId).epochMs;
}

const QUOTES = ['USDT', 'USDC', 'BUSD', 'FDUSD', 'BTC', 'ETH'];

/**
 * 标的拆成 base / quote。
 * 项目里的标的是币安 U 本位永续，绝大多数以 USDT 结尾；少数 meme 币带中文名
 * （如「龙虾USDT」），同样按后缀拆分，不做拼音化或翻译。
 */
export function splitSymbol(symbol: string | null | undefined): { base: string; quote: string } {
  const s = (symbol || '').trim();
  if (!s) return { base: '', quote: '' };
  for (const q of QUOTES) {
    if (s.length > q.length && s.toUpperCase().endsWith(q)) {
      return { base: s.slice(0, -q.length), quote: q };
    }
  }
  return { base: s, quote: '' };
}

/** `CYBERUSDT` → `CYBER/USDT` */
export function symbolLabel(symbol: string | null | undefined): string {
  const { base, quote } = splitSymbol(symbol);
  if (!base) return '—';
  return quote ? `${base}/${quote}` : base;
}

const STATUS_LABEL: Record<string, string> = {
  succeeded: '已完成',
  running: '运行中',
  queued: '排队中',
  failed: '失败',
  error: '出错',
  cancelled: '已取消',
  // CODE-REVIEW-02：历史批量产物无 run.json ⇒ 后端标 registered=false、状态按产物
  // 证据推断，判不了给 unknown。「未登记」≠「失败」，不许混为一谈。
  unknown: '未登记',
  interrupted: '已中断',
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return '—';
  return STATUS_LABEL[status] ?? status;
}

export type StatusTone = 'ok' | 'warn' | 'bad' | 'muted';

export function statusTone(status: string | null | undefined): StatusTone {
  if (status === 'succeeded') return 'ok';
  if (status === 'running' || status === 'queued') return 'warn';
  if (status === 'failed' || status === 'error') return 'bad';
  return 'muted';
}

/**
 * 退出原因中文化（**全站唯一一张表**）。
 *
 * 后端给的是 `max_holding_expired` / `force_close_eod` 这类枚举，不猜未知值（未知原样显示）。
 * 早先 `BacktestWorkbench` 与 `BatchTradesTable` 各自维护过一张局部表 ⇒ 同一笔交易在
 * 「⑤ 逐笔明细」显示中文、在「批次逐笔」显示英文枚举（`max_holding_expired`），
 * 看起来像字段没翻译。现在两边都走这里。
 */
const EXIT_REASON_LABEL: Record<string, string> = {
  stop_loss: '止损',
  intrabar_stop: '盘中止损',
  intrabar_take_profit: '盘中止盈',
  stop: '止损',
  protective_stop: '保护止损',
  take_profit: '止盈',
  max_holding_expired: '持仓到期',
  max_holding: '持仓到期',
  force_close_eod: '收盘强平',
  eod: '收盘平仓',
  trailing_stop: '移动止损',
  signal_exit: '信号反转',
  signal_flip: '信号反转',
  manual: '手动平仓',
  end_of_data: '数据结束',
  opposite_signal: '反向信号',
  time_exit: '超时退出',
  time_stop: '超时退出',
};

export function exitReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—';
  return EXIT_REASON_LABEL[reason] ?? reason;
}

/** 方向：后端 `/trades` 给数字 1 / -1，`/chart` 给 'long' / 'short'，两种都要认。 */
export function directionLabel(d: number | string | null | undefined): '多' | '空' | '—' {
  if (d === 1 || d === '1' || d === 'long' || d === 'LONG') return '多';
  if (d === -1 || d === '-1' || d === 'short' || d === 'SHORT') return '空';
  return '—';
}

export function directionIsLong(d: number | string | null | undefined): boolean | null {
  const l = directionLabel(d);
  return l === '多' ? true : l === '空' ? false : null;
}

/** 币种图标底色：按标的字符做稳定散列，同一标的总拿同一个色（不做品牌色猜测）。 */
const AVATAR_COLORS = [
  '#f7931a', // 橙
  '#627eea', // 蓝紫
  '#14b8a6', // 青
  '#eab308', // 黄
  '#a855f7', // 紫
  '#ec4899', // 粉
  '#22c55e', // 绿
  '#f97316', // 深橙
  '#38bdf8', // 天蓝
  '#f43f5e', // 玫红
];

export function symbolColor(symbol: string | null | undefined): string {
  const s = (symbol || '').toUpperCase();
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

/** 标的图标上显示的字：取 base 的前两个字符（中文名取首字）。 */
export function symbolBadgeText(symbol: string | null | undefined): string {
  const { base } = splitSymbol(symbol);
  if (!base) return '?';
  const chars = Array.from(base);
  return chars.length <= 2 ? base : base.slice(0, 2).toUpperCase();
}
