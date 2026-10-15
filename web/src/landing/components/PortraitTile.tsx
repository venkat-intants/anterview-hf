import { useState } from 'react'
import { cn } from '../lib/cn'
import { initialsOf } from '../lib/initials'

type PortraitTileProps = {
  src: string
  /** Accessible description; also the label of the fallback tile. */
  alt: string
  /** Used for the fallback initials. */
  name: string
  width: number
  height: number
  /** Above-the-fold portrait: load eagerly and at high priority. */
  eager?: boolean
  className?: string
  imgClassName?: string
  initialsClassName?: string
}

/**
 * An interviewer portrait that can never look broken.
 *
 * The portraits are Git LFS objects. Where LFS has not been pulled (a fresh
 * checkout, a preview build) the file is a ~130-byte text pointer that the
 * browser cannot decode, and a plain <img> then shows a broken-image icon with
 * its alt text spilling out of the box. On error we swap in initials on the
 * brand gradient instead, and the tile keeps its accessible name.
 */
export function PortraitTile({ src, alt, name, width, height, eager = false, className, imgClassName, initialsClassName }: PortraitTileProps) {
  const [failed, setFailed] = useState(false)

  if (failed) {
    return (
      <div
        role="img"
        aria-label={alt}
        data-portrait-fallback=""
        className={cn('flex h-full w-full items-center justify-center bg-[image:var(--ui-grad-brand)]', className)}
      >
        <span aria-hidden="true" className={cn('font-display text-4xl font-bold tracking-tight text-[var(--ui-brand-ink)]', initialsClassName)}>
          {initialsOf(name)}
        </span>
      </div>
    )
  }

  // React 18 has no `fetchPriority` prop; the lowercase attribute is what the
  // browser reads. Spread so TypeScript's JSX attribute check does not object.
  const priority = eager ? { fetchpriority: 'high' } : {}
  return (
    <img
      src={src}
      alt={alt}
      width={width}
      height={height}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      onError={() => setFailed(true)}
      className={cn('h-full w-full object-cover', imgClassName, className)}
      {...priority}
    />
  )
}
