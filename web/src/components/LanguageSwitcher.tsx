// LanguageSwitcher — EN / हिंदी / తెలుగు picker in the app header.
// Calls i18n.changeLanguage(); persistence is handled in lib/i18n.ts via the
// 'languageChanged' event → localStorage write.
// Rendered inside TopBar (AppShell) — visible on all pages.
// Visual: Globe pill button + floating dropdown, matching the shell's controls.

import { useTranslation } from 'react-i18next';
import { Globe, ChevronDown } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

const LANGUAGES = [
  { code: 'en', label: 'EN' },
  { code: 'hi', label: 'हिंदी' },
  { code: 'te', label: 'తెలుగు' },
] as const;

type LangCode = (typeof LANGUAGES)[number]['code'];

export default function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language as LangCode;

  // Anything that is not hi/te (en, en-US, unset) is English.
  const activeCode: LangCode = current === 'hi' || current === 'te' ? current : 'en';
  const currentLabel = LANGUAGES.find(({ code }) => code === activeCode)?.label ?? 'EN';

  // A Radix menu: arrow keys + typeahead move through the options, Escape closes
  // and returns focus to the trigger, and outside click dismisses.
  return (
    <div role="group" aria-label={t('lang.label')}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t('lang.label')}
            className="group flex h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-[13px] font-medium text-[var(--ui-soft)] transition-colors hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] data-[state=open]:bg-[var(--ui-inset)] data-[state=open]:text-foreground"
          >
            <Globe size={15} aria-hidden="true" />
            {/* The label and chevron drop away below `sm`; the globe stays, so a
                phone always has a way to change language. */}
            <span className="hidden sm:inline">{currentLabel}</span>
            <ChevronDown
              size={14}
              aria-hidden="true"
              className="hidden transition-transform duration-150 group-data-[state=open]:rotate-180 sm:block"
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" sideOffset={8} className="w-40 min-w-0">
          <DropdownMenuRadioGroup
            value={activeCode}
            onValueChange={(code) => {
              void i18n.changeLanguage(code);
            }}
          >
            {LANGUAGES.map(({ code, label }) => (
              <DropdownMenuRadioItem key={code} value={code} aria-label={t(`lang.${code}`)}>
                {label}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
