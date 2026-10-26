import { useLayoutEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import './StudioTheme.css';

export type StudioTheme = 'light' | 'dark';
export const STUDIO_THEME_STORAGE_KEY = 'material-studio-theme-v1';
const THEME_EVENT = 'material-studio-theme-change';
function savedTheme(): StudioTheme | null {
  try { const value = localStorage.getItem(STUDIO_THEME_STORAGE_KEY); return value === 'light' || value === 'dark' ? value : null; }
  catch { return null; }
}
function systemTheme(): StudioTheme {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
let chosenTheme: StudioTheme | null = null;
function currentTheme(): StudioTheme { return chosenTheme ?? savedTheme() ?? systemTheme(); }
function applyTheme(theme: StudioTheme): void { document.documentElement.dataset.studioTheme = theme; }
/** Independent of App routing: changing appearance does not recreate or clear creation state. */
export function useStudioTheme() {
  const [theme, setThemeState] = useState<StudioTheme>(currentTheme);
  useLayoutEffect(() => {
    const sync = () => { const next = currentTheme(); applyTheme(next); setThemeState(next); };
    const onStorage = (event: StorageEvent) => { if (event.key === STUDIO_THEME_STORAGE_KEY || event.key === null) { chosenTheme = null; sync(); } };
    const media = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;
    sync();
    media?.addEventListener('change', sync);
    window.addEventListener(THEME_EVENT, sync);
    window.addEventListener('storage', onStorage);
    return () => { media?.removeEventListener('change', sync); window.removeEventListener(THEME_EVENT, sync); window.removeEventListener('storage', onStorage); };
  }, []);
  const setTheme = (next: StudioTheme) => {
    chosenTheme = next;
    try { localStorage.setItem(STUDIO_THEME_STORAGE_KEY, next); } catch { /* Appearance still changes for this browser session. */ }
    applyTheme(next); setThemeState(next); window.dispatchEvent(new Event(THEME_EVENT));
  };
  return { theme, setTheme, toggle: () => setTheme(theme === 'dark' ? 'light' : 'dark') };
}
export function StudioThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggle } = useStudioTheme();
  return <button type="button" className={'studio-theme-toggle ' + className} onClick={toggle} aria-label="切换深浅主题" aria-pressed={theme === 'dark'} title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}>{theme === 'dark' ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}<span>{theme === 'dark' ? '浅色' : '深色'}</span></button>;
}
export default StudioThemeToggle;
