// LanguageSwitcher — EN / हिंदी / తెలుగు picker in the app header.
// Calls i18n.changeLanguage(); persistence is handled in lib/i18n.ts via the
// 'languageChanged' event → localStorage write.
// Rendered inside TopBar (AppShell) — visible on all pages.
// Visual: Globe icon button + floating dropdown, matching the dark shell design.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Globe, ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';

const LANGUAGES = [
  { code: 'en', label: 'EN' },
  { code: 'hi', label: 'हिंदी' },
  { code: 'te', label: 'తెలుగు' },
] as const;

type LangCode = (typeof LANGUAGES)[number]['code'];

export default function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language as LangCode;
  const [open, setOpen] = useState(false);

  const currentLabel =
    LANGUAGES.find(
      ({ code }) => current === code || (code === 'en' && !(['hi', 'te'] as string[]).includes(current)),
    )?.label ?? 'EN';

  return (
    <div role="group" aria-label={t('lang.label')} className="relative hidden sm:block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('lang.label')}
        className="flex items-center gap-1.5 rounded-[10px] border border-border bg-[var(--ui-inset)] px-3 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors"
      >
        <Globe size={15} aria-hidden="true" />
        <span>{currentLabel}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </button>

      {open && (
        <>
          {/* Backdrop to dismiss */}
          <div
            className="fixed inset-0 z-30"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div
            role="menu"
            className="absolute right-0 top-[calc(100%+8px)] z-40 w-32 overflow-hidden rounded-[12px] border border-border bg-card p-1 shadow-2xl"
          >
            {LANGUAGES.map(({ code, label }) => {
              const isActive =
                current === code ||
                (code === 'en' && !(['hi', 'te'] as string[]).includes(current));
              return (
                <button
                  key={code}
                  type="button"
                  role="menuitemradio"
                  aria-checked={isActive}
                  aria-label={t(`lang.${code}`)}
                  onClick={() => {
                    void i18n.changeLanguage(code);
                    setOpen(false);
                  }}
                  className={cn(
                    'flex w-full items-center rounded-[8px] px-3 py-2 text-left text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors',
                    isActive
                      ? 'bg-[var(--ui-inset)] text-foreground'
                      : 'text-[var(--ui-soft)] hover:bg-[var(--ui-inset)] hover:text-foreground',
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
