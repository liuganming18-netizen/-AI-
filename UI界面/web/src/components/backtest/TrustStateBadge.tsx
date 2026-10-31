import React from 'react';
import { ShieldCheck, AlertTriangle, HelpCircle, ShieldAlert } from 'lucide-react';
import { normalizeTrustState, NormalizedTrustState } from '../../api/labBacktest';

/** 三态视觉与文案契约配置 */
export const TRUST_STATE_CONFIG: Record<
  NormalizedTrustState,
  {
    label: string;
    shortLabel: string;
    tone: 'pos' | 'neutral' | 'danger';
    badgeClass: string;
    textClass: string;
    bgClass: string;
    borderClass: string;
    description: string;
  }
> = {
  TRUSTED: {
    label: '可信验证通过',
    shortLabel: '可信通过',
    tone: 'pos',
    badgeClass: 'ds-badge ds-badge--pos text-emerald-400 border-emerald-600 bg-emerald-950/40',
    textClass: 'text-emerald-400',
    bgClass: 'bg-emerald-950/30',
    borderClass: 'border-emerald-700/60',
    description: '通过 HMAC 密码学防伪证明与 PIT 因果闭包检验，执行实例绑定自洽。',
  },
  UNVERIFIED: {
    label: '未经验证，仅供隔离研究',
    shortLabel: '未经验证',
    tone: 'neutral',
    badgeClass: 'ds-badge ds-badge--neutral text-neutral-400 border-neutral-600 bg-neutral-900/60',
    textClass: 'text-neutral-400',
    bgClass: 'bg-neutral-900/40',
    borderClass: 'border-neutral-700/60',
    description: '缺少执行期密码学证明或属于历史研究运行，仅供隔离复盘，不得用于决策。',
  },
  REJECTED: {
    label: '可信验证失败，结果不得用于决策',
    shortLabel: '验证失败',
    tone: 'danger',
    badgeClass: 'ds-badge ds-badge--warn text-rose-300 border-rose-500 bg-rose-950/60',
    textClass: 'text-rose-400',
    bgClass: 'bg-rose-950/40',
    borderClass: 'border-rose-600',
    description: '可信证明未通过或数据存在篡改、伪造，已被系统硬拒绝，严禁用于决策！',
  },
};

export interface TrustStateBadgeProps {
  /** 后端返回的可信状态字符串（支持 VERIFIED_TRUSTED, TRUSTED, UNVERIFIED, REJECTED 或 null/undefined） */
  state?: string | null;
  /** 自定义类名 */
  className?: string;
  /** 是否展示图标（默认展示） */
  showIcon?: boolean;
  /** 是否使用短标签（适合紧凑表格） */
  short?: boolean;
  /** 覆盖默认浮层提示 */
  tooltip?: string;
  /** 测试用 ID */
  testid?: string;
}

/**
 * 统一可信状态徽标组件（三态规范）
 * - TRUSTED: 绿色，展示“可信验证通过”
 * - UNVERIFIED: 灰色，展示“未经验证，仅供隔离研究”
 * - REJECTED: 红色，展示“可信验证失败，结果不得用于决策”
 * - 缺失字段严格默认 UNVERIFIED，绝不自行提升等级
 */
export const TrustStateBadge: React.FC<TrustStateBadgeProps> = ({
  state,
  className = '',
  showIcon = true,
  short = false,
  tooltip,
  testid,
}) => {
  const norm = normalizeTrustState(state);
  const cfg = TRUST_STATE_CONFIG[norm];
  const displayText = short ? cfg.shortLabel : cfg.label;

  return (
    <span
      className={`${cfg.badgeClass} inline-flex items-center gap-1 font-medium select-none px-2 py-0.5 rounded text-xs border ${className}`}
      title={tooltip || `${cfg.label}：${cfg.description}`}
      data-testid={testid || `trust-badge-${norm.toLowerCase()}`}
      data-trust-state={norm}
    >
      {showIcon && (
        norm === 'TRUSTED' ? (
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0" aria-hidden="true" />
        ) : norm === 'REJECTED' ? (
          <AlertTriangle className="w-3.5 h-3.5 text-rose-400 shrink-0 animate-pulse" aria-hidden="true" />
        ) : (
          <HelpCircle className="w-3.5 h-3.5 text-neutral-400 shrink-0" aria-hidden="true" />
        )
      )}
      <span>{displayText}</span>
    </span>
  );
};

export interface TrustStateAlertBannerProps {
  state?: string | null;
  reasons?: Array<{ code: string; detail?: string; message?: string }> | null;
  runId?: string | null;
  className?: string;
}

/**
 * REJECTED 显著告警横幅组件
 * 当运行处于可信验证失败 (REJECTED) 状态时必须显著展示，不能仅靠微弱颜色区分。
 */
export const TrustStateAlertBanner: React.FC<TrustStateAlertBannerProps> = ({
  state,
  reasons,
  runId,
  className = '',
}) => {
  const norm = normalizeTrustState(state);
  if (norm !== 'REJECTED') {
    return null;
  }

  const reasonList = reasons && reasons.length > 0 ? reasons : null;

  return (
    <div
      className={`p-3.5 rounded-lg border-2 border-rose-600 bg-rose-950/70 text-rose-200 shadow-lg shadow-rose-950/40 my-3 ${className}`}
      data-testid="trust-state-alert-rejected"
      role="alert"
    >
      <div className="flex items-start gap-2.5">
        <ShieldAlert className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm tracking-wide text-rose-300">
              可信验证失败，结果不得用于决策
            </span>
            <span className="text-[11px] font-mono px-1.5 py-0.2 bg-rose-900/80 text-rose-300 border border-rose-700/80 rounded">
              REJECTED
            </span>
            {runId && (
              <span className="text-xs font-mono text-rose-400/90 truncate max-w-xs" title={runId}>
                ({runId})
              </span>
            )}
          </div>
          <p className="text-xs text-rose-300/90 mt-1 leading-relaxed">
            该回测记录未能通过后端密码学防伪证明核查，可能存在收益篡改、门禁逃逸或回执伪造。
            <strong>其产出的所有指标、收益曲线、K线图表及交易明细均不可采信，严禁用于策略选型、实盘配置或模拟下单！</strong>
          </p>

          {reasonList && (
            <div className="mt-2 pt-2 border-t border-rose-800/60 text-xs">
              <span className="font-semibold text-rose-400">拒绝原因明细：</span>
              <ul className="list-disc list-inside mt-1 space-y-0.5 font-mono text-[11px] text-rose-300/80">
                {reasonList.map((r, idx) => (
                  <li key={idx}>
                    <span className="font-bold text-rose-300">{r.code}</span>
                    {r.detail || r.message ? ` — ${r.detail || r.message}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export interface TrustStateBlockedResourceBoxProps {
  resourceTitle: string;
  trustState?: string | null;
  error?: string | null;
  className?: string;
}

/**
 * 子资源阻断提示卡片
 * 当后端拒绝返回 REJECTED 子资源或子资源被硬拦截时呈现，彻底杜绝回退到旧缓存冒充正常结果。
 */
export const TrustStateBlockedResourceBox: React.FC<TrustStateBlockedResourceBoxProps> = ({
  resourceTitle,
  trustState,
  error,
  className = '',
}) => {
  const norm = normalizeTrustState(trustState);
  const isRejected = norm === 'REJECTED';

  return (
    <div
      className={`p-6 my-2 rounded-lg border text-center ${
        isRejected
          ? 'border-rose-700/80 bg-rose-950/30 text-rose-300'
          : 'border-neutral-700/80 bg-neutral-900/40 text-neutral-400'
      } ${className}`}
      data-testid="trust-state-blocked-resource"
    >
      <div className="flex flex-col items-center justify-center gap-2">
        <AlertTriangle className={`w-6 h-6 ${isRejected ? 'text-rose-400 animate-pulse' : 'text-neutral-400'}`} />
        <h4 className="font-semibold text-sm">
          {resourceTitle}：{isRejected ? '可信验证未通过，数据已安全阻断' : '数据暂不可用'}
        </h4>
        <p className="text-xs max-w-md leading-relaxed text-neutral-400">
          {isRejected
            ? '当前回测已被判定为 REJECTED（可信验证失败）。根据防御策略，系统已切断指标、图表及交易明细的提取通道，严禁使用旧缓存展示。'
            : error || '暂无法读取该回测的明细数据。'}
        </p>
      </div>
    </div>
  );
};
