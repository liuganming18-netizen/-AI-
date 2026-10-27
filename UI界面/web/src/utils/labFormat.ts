/**
 * 回测复盘页的数值格式化
 * ----------------------------------------------------------------------------
 * 一条铁律：**未知一律 `—`**。`null` / `undefined` / `NaN` / `Infinity` 都不许
 * 变成 `0` 或 `0.00%` —— 那会把「后端没算」读成「算出来是零」。
 */

export const DASH = '—';

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 百分数（入参已是百分数本身，如 0.0635 表示 0.0635%）。带正负号。 */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}%`;
}

/** 百分数，不带正负号（用于胜率、回撤这类「本来就是正数语义」的值）。 */
export function fmtPctPlain(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return `${v.toFixed(digits)}%`;
}

/** 美元金额。小数位按量级自适应，避免 2000.00 与 0.0221 用同一精度。 */
export function fmtUsd(v: number | null | undefined, digits?: number): string {
  if (!isNum(v)) return DASH;
  const d = digits ?? (Math.abs(v) >= 100 ? 2 : Math.abs(v) >= 1 ? 2 : 4);
  return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** 普通数字，带千分位。 */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 整数（交易次数、笔数）。 */
export function fmtInt(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return Math.round(v).toLocaleString('en-US');
}

/** 价格：按量级给 2~6 位小数（低价币 0.000123 不能显示成 0.00）。 */
export function fmtPrice(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  const d = a >= 1000 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6;
  return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** 持仓时长：小时 → 「10 天」「3 小时」。 */
export function fmtDurationH(hours: number | null | undefined): string {
  if (!isNum(hours) || hours < 0) return DASH;
  if (hours >= 24) {
    const d = hours / 24;
    return `${Number.isInteger(d) ? d : d.toFixed(1)} 天`;
  }
  if (hours >= 1) return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} 小时`;
  return `${Math.round(hours * 60)} 分钟`;
}

/** UTC 时间戳（毫秒）→ `2024-08-14` */
export function fmtDate(ms: number | null | undefined): string {
  if (!isNum(ms) || ms <= 0) return DASH;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString().slice(0, 10);
}

/** UTC 时间戳（毫秒）→ `2024-08-14 12:00` */
export function fmtDateTime(ms: number | null | undefined): string {
  if (!isNum(ms) || ms <= 0) return DASH;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return DASH;
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)}`;
}

/** 紧凑计数：1.2K / 3.4M（用于成交量、OI 数量） */
export function fmtCompact(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  if (a >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  if (a >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  if (a >= 1000) return `${(v / 1000).toFixed(1)}K`;
  return v.toFixed(a >= 1 ? 2 : 4);
}

/** 涨跌方向：1 正 / -1 负 / 0 平（给颜色类用） */
export function signOf(v: number | null | undefined): -1 | 0 | 1 {
  if (!isNum(v) || v === 0) return 0;
  return v > 0 ? 1 : -1;
}
