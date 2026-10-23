import { Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { useThemeMode, type ThemeMode } from '@/lib/useThemeMode';

/**
 * Light / dark switch for the two AntHire designs.
 *
 * Separate from `ThemeToggle`, which picks the signal accent WITHIN a mode.
 * Rendered app-wide: every surface but the live interview honours the choice,
 * and the live interview has no shell to render it in.
 *
 * A two-position segmented control, not a single toggling icon: the current
 * mode is then readable without working out whether the icon means state or
 * action.
 */
export default function ModeSwitch({ className }: { className?: string }) {
  const { t } = useTranslation();
  const [mode, setMode] = useThemeMode();

  const options: { id: ThemeMode; label: string; title: string; Icon: typeof Sun }[] = [
    { id: 'light', label: t('shell.modeLight'), title: t('shell.modeLightTitle'), Icon: Sun },
    { id: 'dark', label: t('shell.modeDark'), title: t('shell.modeDarkTitle'), Icon: Moon },
  ];

  return (
    <div
      role="radiogroup"
      aria-label={t('shell.colourMode')}
      className={cn(
        'flex h-9 items-center gap-0.5 rounded-full border border-border bg-card p-[3px]',
        className,
      )}
    >
      {options.map(({ id, label, title, Icon }) => {
        const active = mode === id;
        // A radio segment, not an action button (UI conventions, "Buttons":
        // raw <button> for toggles), so the selected look follows its
        // aria-checked state rather than a button variant.
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={title}
            data-testid={`mode-${id}`}
            onClick={() => setMode(id)}
            className="inline-flex h-7 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] aria-checked:bg-primary aria-checked:text-primary-foreground aria-checked:hover:text-primary-foreground"
          >
            <Icon size={14} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
