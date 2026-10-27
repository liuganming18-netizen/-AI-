/**
 * 主题（深浅）上下文
 * ----------------------------------------------------------------------------
 * 全站只有一条事实源：`<html data-theme="dark|light">`。CSS 侧 tokens.css 用
 * `:root`（深色默认）+ `[data-theme="light"]`（浅色覆盖）两套值响应它。
 *
 * 三个刻意的设计：
 *   1. **先写 DOM，再 setState**。图表组件在 render 期/effect 里用
 *      getComputedStyle 读 `--ds-chart-*`，如果先 setState，子组件会在 DOM
 *      还没换肤时就读到旧值，表现为「文字变了、图还留着上一套颜色」。
 *   2. **不 remount 图表**。切主题只 applyOptions + 重设 markers，缩放位置、
 *      十字线、已加载数据全部保留。
 *   3. **首屏不闪白**。index.html 里有一段同步内联脚本，在 React 挂载前就把
 *      data-theme 写好；这里的 state 初值只是从 DOM 读回来，不再二次设置。
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

export type Theme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'wk-theme';

/**
 * 缺省浅色（2026-10-18）：设计稿 `gm-layout.html` 是浅色版，用户要求
 * 「页面与渲染的颜色一模一样」⇒ 首次访问必须落在浅色。深色仍保留为可切换项，
 * 令牌在 `[data-theme="dark"]` 里，不影响浅色取值。
 */
const DEFAULT_THEME: Theme = 'light';

/** 读当前 DOM 上的主题（内联脚本已经设好，这里只是取回）。 */
export function readDomTheme(): Theme {
  if (typeof document === 'undefined') return DEFAULT_THEME;
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

export function applyThemeToDom(theme: Theme): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.theme = theme;
  // 原生控件（select 下拉、滚动条、日期选择器）的配色靠这个属性跟随
  document.documentElement.style.colorScheme = theme;
}

function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === 'dark' || v === 'light' ? v : null;
  } catch {
    return null;
  }
}

/**
 * `?theme=light` / `?theme=dark` 显式指定主题。
 * 用途：分享某一种观感的截图链接、自动化截图验收、以及在没有 localStorage
 * 的环境（无痕窗口 / 内嵌 webview）里强制某一套。优先级最高。
 */
function readUrlTheme(): Theme | null {
  if (typeof window === 'undefined') return null;
  try {
    const v = new URLSearchParams(window.location.search).get('theme');
    return v === 'dark' || v === 'light' ? v : null;
  } catch {
    return null;
  }
}

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [theme, setThemeState] = useState<Theme>(
    () => readUrlTheme() ?? readStoredTheme() ?? readDomTheme(),
  );

  // 首次挂载对齐一次 DOM（处理内联脚本未执行 / SSR 之类的边角）
  useEffect(() => {
    applyThemeToDom(theme);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setTheme = useCallback((next: Theme) => {
    // 顺序不能反：先让 DOM 换肤，子组件随后读 getComputedStyle 才是新值
    applyThemeToDom(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      /* 隐私模式下写不进去，忽略：本次会话仍然生效 */
    }
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(readDomTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  const value = useMemo(() => ({ theme, setTheme, toggleTheme }), [theme, setTheme, toggleTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme 必须在 <ThemeProvider> 内使用');
  return ctx;
}

/**
 * 不抛错的版本：供「可能被单独挂载 / 单测」的底层组件（如图表）使用。
 * 返回 null 时调用方自行退回 DOM 属性判断。
 */
export function useThemeOptional(): ThemeContextValue | null {
  return useContext(ThemeContext);
}
