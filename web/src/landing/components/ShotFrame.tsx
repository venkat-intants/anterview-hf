import { type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '../lib/cn'

export type Shot = {
  /** File stem under /landing/: `${stem}-light.webp` and `${stem}-dark.webp`. */
  stem: string
  /** i18n key of the alt text. */
  alt: string
  /** Intrinsic pixel size of BOTH variants (they are captured identically). */
  width: number
  height: number
}

/**
 * A real product screenshot in a quiet frame: 16px radius, hairline, soft
 * shadow. Both colour variants are in the DOM and the page's own theme
 * attribute (`data-landing-theme`, stamped on first render from the stored
 * mode) hides one with CSS, so there is no flash and no JS. The hidden one is
 * `display:none`, and browsers do not fetch a lazy image they do not render,
 * so a visitor downloads one variant only.
 */
export function ShotFrame({ shot, eager = false, className, style }: { shot: Shot; eager?: boolean; className?: string; style?: CSSProperties }) {
  const { t } = useTranslation()
  const common = {
    width: shot.width,
    height: shot.height,
    alt: t(shot.alt),
    decoding: 'async' as const,
    loading: eager ? ('eager' as const) : ('lazy' as const),
  }
  return (
    <div className={cn('overflow-hidden rounded-[16px] border border-[var(--lp-line)] bg-[var(--lp-surface)] shadow-elevated', className)} style={style}>
      <img
        {...common}
        src={`/landing/${shot.stem}-light.webp`}
        className="block h-auto w-full [[data-landing-theme=dark]_&]:hidden"
        data-shot-variant="light"
      />
      <img
        {...common}
        src={`/landing/${shot.stem}-dark.webp`}
        className="hidden h-auto w-full [[data-landing-theme=dark]_&]:block"
        data-shot-variant="dark"
      />
    </div>
  )
}
