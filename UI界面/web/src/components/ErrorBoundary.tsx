/**
 * 渲染异常兜底
 * ----------------------------------------------------------------------------
 * 为什么必须有：React 18/19 里**任何一次 render 期异常**都会卸载整棵树，
 * 用户看到的就是「整页白屏」。2026-10-17 就踩过 —— 某个 run 的 `chart.candles`
 * 是对象而不是数组元组，`const [t,o,h,l,c,v] = raw` 抛 TypeError，
 * 结果整个回测页消失，连左栏列表都没了。
 *
 * 边界原则：
 *   · 粒度尽量细（K 线 / 分析 / 明细 / 收益曲线各一个），一块坏不牵连其它块；
 *   · 错误信息**原样显示**（不吞），并说明「其它面板不受影响」；
 *   · 提供「重新渲染」，切 run 时由外部 key 自动重置。
 */

import React from 'react';
import { AlertTriangle, RotateCw } from 'lucide-react';

interface Props {
  children: React.ReactNode;
  /** 出问题的是哪一块（显示在标题里，例如「K 线」「交易明细」） */
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // 打到控制台，方便用 devtools 定位；不吞异常信息
    console.error(`[UI] ${this.props.label ?? '组件'} 渲染异常：`, error, info.componentStack);
  }

  private reset = (): void => this.setState({ error: null });

  render(): React.ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div
        className="ds-empty"
        style={{ padding: 'var(--ds-4)', gap: 6, textAlign: 'center', minHeight: 120 }}
        role="alert"
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            color: 'var(--ds-warn)',
            fontWeight: 600,
            fontSize: 'var(--ds-fs-sm)',
          }}
        >
          <AlertTriangle size={14} />
          {this.props.label ?? '该区域'}渲染失败
        </div>
        <div
          style={{
            fontFamily: 'var(--ds-mono)',
            fontSize: 'var(--ds-fs-2xs)',
            color: 'var(--ds-text-3)',
            maxWidth: 520,
            overflowWrap: 'anywhere',
          }}
        >
          {error.message || String(error)}
        </div>
        <div style={{ fontSize: 'var(--ds-fs-2xs)', color: 'var(--ds-text-4)', maxWidth: 520 }}>
          其它面板不受影响。可以先重新渲染，或切换回测后再回来。
        </div>
        <button type="button" className="ds-btn ds-btn--secondary ds-btn--sm" onClick={this.reset}>
          <RotateCw size={12} />
          重新渲染
        </button>
      </div>
    );
  }
}
