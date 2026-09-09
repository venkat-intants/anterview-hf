import { Moon, Sun } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useThemeMode, type ThemeMode } from '@/lib/useThemeMode';

/**
 * Light / dark switch for the two AntHire designs.
 *
 * Separate from `ThemeToggle`, which picks the signal accent WITHIN a mode.
 * Rendered only where a light design exists (the auth pages and the candidate
 * shell); on a staff console the mode is forced dark by `ThemeModeGate`, and
 * offering a control that visibly does nothing is worse than not offering one.
 *
 * A two-position segmented control, not a single toggling icon: the current
 * mode is then readable without working out whether the icon means state or
 * action.
 */
export default function ModeSwitch({ className }: { className?: string }) {
  const [mode, setMode] = useThemeMode();

  const options: { id: ThemeMode; label: string; Icon: typeof Sun }[] = [
    { id: 'light', label: 'Light', Icon: Sun },
    { id: 'dark', label: 'Dark', Icon: Moon },
  ];

  return (
    <div
      role="radiogroup"
      aria-label="Colour mode"
      className={cn(
        'flex items-center gap-0.5 rounded-[10px] border border-border bg-[var(--ui-inset)] p-[3px]',
        className,
      )}
    >
      {options.map(({ id, label, Icon }) => {
        const active = mode === id;
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={`${label} mode`}
            data-testid={`mode-${id}`}
            onClick={() => setMode(id)}
            className={cn(
              'inline-flex h-[26px] w-[30px] items-center justify-center rounded-[8px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              active
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon size={14} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
