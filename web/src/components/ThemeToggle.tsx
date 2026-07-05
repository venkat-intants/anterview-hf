// ThemeToggle — picks the app's signal-accent theme (Black / Black+Purple /
// Black+Blue). All three keep the midnight surfaces; only the accent changes.
//
// The choice is written to <html data-theme> (CSS vars in index.css recolour
// the whole UI) and persisted to localStorage. A pre-paint script in index.html
// applies the saved theme before React mounts, so there is no colour flash.
// 'intants:themechange' is dispatched so chart colours (useAccentColor) update.
//
// Rendered in the app TopBar next to LanguageSwitcher — visible on all pages.

import { useState } from 'react';
import { Palette, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { THEME_CHANGE_EVENT } from '@/lib/useAccentColor';

type ThemeId = 'blue' | 'purple' | 'black';

const THEME_KEY = 'intants:theme';

const THEMES: { id: ThemeId; label: string; swatch: string }[] = [
  { id: 'black', label: 'Black', swatch: 'linear-gradient(135deg,#2a2b2f,#0a0a0b)' },
  { id: 'purple', label: 'Black + Purple', swatch: 'linear-gradient(135deg,#a855f7,#0a0a0b)' },
  { id: 'blue', label: 'Black + Blue', swatch: 'linear-gradient(135deg,#0088ff,#0a0a0b)' },
];

function readTheme(): ThemeId {
  const t = document.documentElement.getAttribute('data-theme');
  return t === 'purple' || t === 'black' ? t : 'blue';
}

function applyTheme(id: ThemeId): void {
  document.documentElement.setAttribute('data-theme', id);
  try {
    localStorage.setItem(THEME_KEY, id);
  } catch {
    /* storage may be unavailable (private mode) — theme still applies for the session */
  }
  // Let CSS-var-blind consumers (recharts/SVG colours via useAccentColor) re-read.
  window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
}

export default function ThemeToggle() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeId>(() => readTheme());

  const active = THEMES.find((t) => t.id === theme) ?? THEMES[2];

  function choose(id: ThemeId) {
    applyTheme(id);
    setTheme(id);
    setOpen(false);
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Theme"
        title="Theme"
        className="flex items-center gap-1.5 rounded-[10px] border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-[13px] text-[#b8babf] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors"
      >
        <Palette size={15} aria-hidden="true" />
        <span
          className="h-3.5 w-3.5 rounded-full ring-1 ring-white/20"
          style={{ background: active.swatch }}
          aria-hidden="true"
        />
      </button>

      {open && (
        <>
          {/* Backdrop to dismiss */}
          <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute right-0 top-[calc(100%+8px)] z-40 w-48 overflow-hidden rounded-[12px] border border-white/[0.1] bg-[#0f0f10] p-1 shadow-2xl"
          >
            {THEMES.map(({ id, label, swatch }) => {
              const isActive = theme === id;
              return (
                <button
                  key={id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={isActive}
                  onClick={() => choose(id)}
                  className={cn(
                    'flex w-full items-center gap-2.5 rounded-[8px] px-3 py-2 text-left text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors',
                    isActive ? 'bg-white/[0.06] text-white' : 'text-[#b8babf] hover:bg-white/[0.04] hover:text-white',
                  )}
                >
                  <span
                    className="h-4 w-4 flex-none rounded-full ring-1 ring-white/20"
                    style={{ background: swatch }}
                    aria-hidden="true"
                  />
                  <span className="flex-1 truncate">{label}</span>
                  {isActive && <Check size={14} aria-hidden="true" className="flex-none text-[var(--accent)]" />}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
