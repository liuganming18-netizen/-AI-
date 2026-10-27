/**
 * UI-PENDING-01 · 挂单块三态夹具页（只用于本工单截图，不进生产路由）
 *
 * 为什么需要它：真实接口联调要等 A 窗口通过（本工单不依赖 A）。为了在**不伪造后端**的前提下
 * 拿到「有挂单 / 无挂单 / 请求失败」三种画面的截图，这里直接把 **TEST FIXTURE** 作为 props
 * 灌进**真实组件** `PositionPanel`，页面顶部常驻红色夹具横幅，避免截图被误当成真实账本。
 *
 * 用法：http://127.0.0.1:3000/tests_pending/harness.html?state=has_pending|empty|unreadable
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import '../src/index.css';
import { PositionPanel } from '../src/components/PositionPanel';
import { CASES, FIXTURE_BANNER, type CaseName } from './fixtures';

const params = new URLSearchParams(window.location.search);
const raw = params.get('state') ?? 'has_pending';
const state: CaseName = (['has_pending', 'empty', 'unreadable', 'not_connected'] as const).includes(
  raw as CaseName,
)
  ? (raw as CaseName)
  : 'has_pending';
const f = CASES[state];

const App: React.FC = () => (
  <div
    style={{ background: 'var(--ds-n-950, #f4f6f5)', padding: 16, minHeight: '100vh' }}
    data-harness-state={state}
  >
    <div
      style={{
        maxWidth: 420,
        margin: '0 0 12px',
        padding: '8px 10px',
        borderRadius: 8,
        border: '1px solid #c8323c',
        background: '#fdecec',
        color: '#8d1f27',
        font: '12px/1.5 -apple-system, "PingFang SC", sans-serif',
      }}
      data-testid="fixture-banner"
    >
      <b>{FIXTURE_BANNER}</b>
      <div style={{ fontSize: 11, opacity: 0.85 }}>
        state = {state} · 标的一律 TESTFIX 前缀 · 时间戳固定 2026-10-24 UTC
      </div>
    </div>

    {/* 真实组件：props 全部来自夹具 */}
    <div
      className="workbench-columns"
      style={{ width: 420, display: 'flex', background: '#0a0a0a', borderRadius: 8, overflow: 'hidden' }}
    >
      <PositionPanel
        paperAccounts={f.accounts}
        paperPositions={[]}
        paperPending={f.pending}
        paperMissingReason={f.missingReason}
        liveAccounts={[]}
        liveMissingReason={null}
        orderBusy={false}
        onSelectTokenSymbol={() => undefined}
        onClosePosition={() => undefined}
        onOpenQuickPosition={() => undefined}
        isGateRunning={false}
      />
    </div>

    {/* 夹具页局部覆盖：真实页面里挂单区在滚动容器内，截图需要整段可见 */}
    <style>{`.workbench-position-content{max-height:none!important;overflow:visible!important}`}</style>
  </div>
);

createRoot(document.getElementById('root')!).render(<App />);
