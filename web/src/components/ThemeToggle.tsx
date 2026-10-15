// ThemeToggle — picks the signal accent (Blue / Violet / Graphite). Surfaces and
// text belong to the light/dark mode (ModeSwitch); only the accent changes here.
//
// The choice is written to <html data-theme> (CSS vars in index.css recolour
// the whole UI) and persisted to localStorage. A pre-paint script in index.html
// applies the saved theme before React mounts, so there is no colour flash.
// 'intants:themechange' is dispatched so chart colours (useAccentColor) update.
//
// Rendered in the app TopBar next to LanguageSwitcher — visible on all pages.

import { useState } from 'react';
import { Palette } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { THEME_CHANGE_EVENT } from '@/lib/useAccentColor';

// 'black' keeps its stored id (renaming it would drop a visitor's saved choice);
// it is shown as Graphite.
type ThemeId = 'blue' | 'violet' | 'black';

const THEME_KEY = 'intants:theme';

const THEMES: { id: ThemeId; label: string; swatch: string }[] = [
  { id: 'blue', label: 'Blue', swatch: 'linear-gradient(135deg,#6ea0ff,#1d4ed8)' },
  { id: 'violet', label: 'Violet', swatch: 'linear-gradient(135deg,#9d8cff,#5b38f0)' },
  { id: 'black', label: 'Graphite', swatch: 'linear-gradient(135deg,#a8b0c0,#3b4557)' },
];

function readTheme(): ThemeId {
  const t = document.documentElement.getAttribute('data-theme');
  return t === 'violet' || t === 'black' ? t : 'blue';
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
  const [theme, setTheme] = useState<ThemeId>(() => readTheme());

  const active = THEMES.find((t) => t.id === theme) ?? THEMES[0];

  function choose(id: ThemeId) {
    applyTheme(id);
    setTheme(id);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Theme"
          title="Theme"
          className="flex h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-[13px] text-[var(--ui-soft)] transition-colors hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] data-[state=open]:bg-[var(--ui-inset)] data-[state=open]:text-foreground"
        >
          <Palette size={15} aria-hidden="true" />
          <span
            className="h-3.5 w-3.5 rounded-full"
            style={{ background: active.swatch }}
            aria-hidden="true"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-48 min-w-0">
        <DropdownMenuLabel>Accent</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(id) => choose(id as ThemeId)}>
          {THEMES.map(({ id, label, swatch }) => (
            <DropdownMenuRadioItem key={id} value={id} className="gap-2.5">
              <span
                className="h-4 w-4 flex-none rounded-full"
                style={{ background: swatch }}
                aria-hidden="true"
              />
              <span className="flex-1 truncate">{label}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
