import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { ThemeToggle } from '../theme/ThemeToggle';
import { useIsMobile } from '../hooks/useMediaQuery';

interface NavigationSidebarProps {
  currentNav: string;
  onSelectNav: (nav: string) => void;
  /** 实时行情采集链路：最新价格事件距今秒数（来自 /health），未知为 null */
  realtimeAgeSeconds: number | null;
  /** 实时行情采集链路状态（ok / stale / ...），未知为 null */
  realtimeStatus: string | null;
  /** 移动端抽屉是否展开（< 768px 才有意义；桌面端忽略） */
  mobileOpen?: boolean;
  /** 移动端请求关闭抽屉（点击遮罩 / Esc / 选中导航项 / 关闭按钮） */
  onMobileClose?: () => void;
}

/** 新鲜度阈值：与数据层一致，超过 60s 不再显示“在线”态。 */
const FRESH_SECONDS = 60;

/**
 * 左侧导航 —— 三档响应式（2026-10-18 对齐设计稿 gm-layout.html）：
 *   ≥ 1024px  176px 纯文字导航（标题 16px/700 + 4 个导航项 + 底部链路状态）
 *   768–1023  140px（设计稿 ≤1180 的收窄值，文字保留）
 *   < 768     离屏抽屉（默认收起，由顶栏汉堡键唤出；遮罩 / Esc / 选中后自动关闭）
 *
 * 设计稿的侧栏**没有图标、没有分组标题、没有徽标**，因此这里不再渲染它们。
 * 断点与 `useMediaQuery.ts` 严格同源；具体尺寸写在 `design/shell.css` 的 `.appnav`
 * 段落，组件只负责结构与行为（打开/关闭、焦点、`inert`），不写宽度字面量。
 */
export const NavigationSidebar: React.FC<NavigationSidebarProps> = ({
  currentNav,
  onSelectNav,
  realtimeAgeSeconds,
  realtimeStatus,
  mobileOpen = false,
  onMobileClose,
}) => {
  const navItems = [
    { id: 'workbench', label: '交易工作台' },
    { id: 'datacenter', label: '数据中心' },
    { id: 'strategy', label: '策略管理' },
    { id: 'backtest', label: '回测复盘' },
  ];

  const hasAge = realtimeAgeSeconds !== null && Number.isFinite(realtimeAgeSeconds);
  const isFresh = hasAge && realtimeStatus === 'ok' && (realtimeAgeSeconds as number) <= FRESH_SECONDS;
  const ageText = hasAge ? `${realtimeAgeSeconds}s` : '—';

  const isMobile = useIsMobile();
  const drawerRef = useRef<HTMLElement | null>(null);

  /**
   * 抽屉打开时：把焦点移进来（否则键盘用户停在背后的汉堡键上，
   * 按 Tab 会一路走过整个页面才回到抽屉）；Esc 关闭。
   * 只在移动端生效，桌面端这里永不订阅。
   */
  useEffect(() => {
    if (!isMobile || !mobileOpen) return;
    const el = drawerRef.current;
    if (!el) return;
    const first = el.querySelector<HTMLElement>('[data-nav-close]');
    first?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onMobileClose?.();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [isMobile, mobileOpen, onMobileClose]);

  const handleSelect = (id: string) => {
    onSelectNav(id);
    // 移动端选完就收起，避免抽屉挡住刚打开的页面
    if (isMobile) onMobileClose?.();
  };

  return (
    <>
      {/* 遮罩：只在移动端且展开时可见/可点（其余情况由 CSS 置为 pointer-events:none） */}
      <div
        className="appnav__scrim"
        data-open={mobileOpen ? 'true' : 'false'}
        onClick={() => onMobileClose?.()}
        aria-hidden="true"
      />

      <aside
        ref={drawerRef}
        id="app-nav"
        className="appnav"
        data-open={mobileOpen ? 'true' : 'false'}
        aria-label="主导航"
        // 抽屉关着时整块不可聚焦（否则 Tab 会跳进屏幕外的链接）
        inert={isMobile && !mobileOpen ? true : undefined}
      >
        <div className="appnav__top">
          {/* 品牌区：设计稿只有一行标题（无图标块、无版本行、无分隔线） */}
          <div className="appnav__brand">
            <div className="appnav__brandtext">
              <h2 className="appnav__title">趋势研判系统</h2>
            </div>
            {/* 抽屉里的关闭键：移动端专属 */}
            <button
              type="button"
              data-nav-close
              onClick={() => onMobileClose?.()}
              aria-label="关闭导航"
              className="appnav__close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <nav className="appnav__list">
            {navItems.map((item) => {
              const isActive = currentNav === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => handleSelect(item.id)}
                  aria-current={isActive ? 'page' : undefined}
                  aria-label={item.label}
                  title={item.label}
                  className={`appnav__item ${isActive ? 'is-active' : ''}`}
                >
                  <span className="appnav__label">{item.label}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* 底部：设计稿 `.wsb` —— 一个绿点 + 「WebSocket 3s」一行灰字。
            主题开关是既有能力，压到同一档样式后放在它上面。 */}
        <div className="appnav__foot">
          <ThemeToggle />
          <div className="appnav__status">
            <span className="appnav__statusleft">
              <span className={`appnav__dot ${isFresh ? 'is-fresh' : ''}`} />
              <span className="appnav__label">WebSocket</span>
              <span className={`appnav__age ${isFresh ? 'is-fresh' : ''}`}>{ageText}</span>
            </span>
          </div>
        </div>
      </aside>
    </>
  );
};
