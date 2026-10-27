/**
 * 主题切换按钮
 * ----------------------------------------------------------------------------
 * 放在左侧导航底部（WebSocket 状态条之上）。只依赖 ThemeProvider，不收 props，
 * 因此可以被任何页面复用而不牵连数据层。
 */

import React from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from './ThemeProvider';

export const ThemeToggle: React.FC = () => {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === 'dark';

  return (
    <button
      type="button"
      onClick={toggleTheme}
      data-testid="theme-toggle"
      title={isDark ? '切换到浅色主题' : '切换到深色主题'}
      aria-label={isDark ? '切换到浅色主题' : '切换到深色主题'}
      className="appnav-toggle appnav-theme"
    >
      <span className="appnav-theme__left">
        {isDark ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
        <span className="appnav__label">{isDark ? '深色主题' : '浅色主题'}</span>
      </span>
      <span className="appnav__meta">{isDark ? 'Dark' : 'Light'}</span>
    </button>
  );
};
