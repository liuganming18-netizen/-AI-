/**
 * 顶部状态条（对齐设计稿 `gm-layout.html` 的 `.topbar`）
 * ----------------------------------------------------------------------------
 * 设计稿里它是**全站常驻**的一条白底细条，横跨内容区（侧栏右侧）：
 *
 *   BTC 102,312 +3.42%   ETH 3,852 +1.85%   模拟盘权益 100,399.12
 *   行情连接 正常（3 秒前）   电报提醒 未配置              〔只读〕
 *
 * 本组件只**展示**，不计算任何交易数字：
 *   · 币价与涨跌幅原样取 `TokenMarketItem.price / change24h`（后者已是百分数）。
 *   · 模拟盘权益取服务端账本可用现金合计；账本不可用时为 `null` ⇒ `—`。
 *   · 行情连接取 `/health` 的实时链路状态与距今秒数，超过阈值即标「延迟」。
 *   · 电报提醒后端未接 ⇒ 固定「未配置」，不显示成已启用。
 *
 * 设计稿右侧那颗黄底徽标写「示意数据」；本应用接的是真实数据，
 * 因此改成中性「只读」，含义是「本页不写库、不下单」。
 */

import React, { useEffect, useState, useMemo } from 'react';

interface TopStatusBarProps {
  btcPrice: number | null;
  btcChange: number | null;
  ethPrice: number | null;
  ethChange: number | null;
  paperBalance: number | null;
  /** 以下三项设计稿顶栏没有，保留入参只为不改动调用方；不再渲染。 */
  networkOiUsd?: number | null;
  oiUsdCoverage?: number;
  oiPoolTotal?: number;
  smartMoneyRatio?: number | null;
  avgFundingRate?: number | null;
  /** 后端 as_of（刷新时间） */
  asOf: string | null;
  /** 取数错误（仅用于控制台提示，不新增界面元素） */
  feedError?: string | null;
  /** 实时链路状态（ok / stale / ...），未知为 null */
  realtimeStatus?: string | null;
  /** 最新价格事件距今秒数，未知为 null */
  realtimeAgeSeconds?: number | null;
}

/** 与侧栏同源的新鲜度阈值：超过 60s 不再算「正常」。 */
const FRESH_SECONDS = 60;

/** 币价按量级取小数位：BTC 要 0 位，PEPE 要 8 位。 */
const fmtPrice = (v: number | null): string => {
  if (v === null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 8;
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

/** 涨跌幅：`change24h` 已是百分数（adapters 里 ×100 过），这里只补符号。 */
const fmtPct = (v: number | null): string => {
  if (v === null || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
};

/** 权益用**完整数字**（设计稿写 `100,399.12`，不缩成「10.04 万」——
 *  顶栏是「一眼看余额」的位置，缩写会让人读不出量级差）。 */
const fmtMoney = (v: number | null): string => {
  if (v === null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

export const TopStatusBar: React.FC<TopStatusBarProps> = ({
  btcPrice,
  btcChange,
  ethPrice,
  ethChange,
  paperBalance,
  feedError,
  realtimeStatus = null,
  realtimeAgeSeconds = null,
}) => {
  const [timeStr, setTimeStr] = useState<string>('');

  useEffect(() => {
    // 该位置自述为 UTC，故按 UTC 呈现（后端时间均为 UTC）。
    const updateTime = () => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'UTC' }));
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (feedError) console.warn('[真实数据] 取数失败：', feedError);
  }, [feedError]);

  const hasAge = realtimeAgeSeconds !== null && Number.isFinite(realtimeAgeSeconds);
  const isFresh = hasAge && realtimeStatus === 'ok' && (realtimeAgeSeconds as number) <= FRESH_SECONDS;
  const linkText = !hasAge
    ? '—'
    : isFresh
      ? '正常'
      : realtimeStatus === 'ok'
        ? '延迟'
        : (realtimeStatus ?? '—');

  const tickers = useMemo(
    () => [
      { sym: 'BTC', price: btcPrice, chg: btcChange },
      { sym: 'ETH', price: ethPrice, chg: ethChange },
    ],
    [btcPrice, btcChange, ethPrice, ethChange],
  );

  return (
    <header className="ds-topbar" data-testid="top-status-bar">
      {tickers.map((t) => {
        const cls = t.chg === null || !Number.isFinite(t.chg) ? 'nu' : t.chg >= 0 ? 'up' : 'dn';
        return (
          <span key={t.sym} className="ds-topbar__item">
            {t.sym} <b>{fmtPrice(t.price)}</b> <span className={cls}>{fmtPct(t.chg)}</span>
          </span>
        );
      })}

      <span className="ds-topbar__item">
        模拟盘权益 <b>{fmtMoney(paperBalance)}</b>
      </span>

      <span className="ds-topbar__item">
        行情连接 <b className={isFresh ? 'up' : 'nu'}>{linkText}</b>
        {hasAge ? `（${realtimeAgeSeconds} 秒前）` : ''}
      </span>

      <span className="ds-topbar__item">
        电报提醒 <b>未配置</b>
      </span>

      <span className="ds-topbar__spacer" />

      <span className="ds-topbar__item ds-topbar__clock">
        {timeStr || '—'} <span className="ds-topbar__unit">UTC</span>
      </span>
      <span className="ds-topbar__tag">只读</span>
    </header>
  );
};

export default TopStatusBar;
