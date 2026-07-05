// AuroraBackground — the signature ambient backdrop for the redesign.
// Soft, slowly-drifting colored blobs over a faint grid, fixed behind content.
// Renders nothing interactive; sits at -z-10. Tuned to be subtle on the light
// Fog canvas and luminous on dark surfaces.

import { cn } from '@/lib/utils';

interface AuroraBackgroundProps {
  className?: string;
  /** Show the faint technical grid overlay (default true). */
  grid?: boolean;
  /** Overall intensity 'subtle' | 'vivid' (default 'subtle'). */
  intensity?: 'subtle' | 'vivid';
}

export default function AuroraBackground({
  className,
  grid = true,
  intensity = 'subtle',
}: AuroraBackgroundProps) {
  const opacity = intensity === 'vivid' ? 'opacity-70' : 'opacity-40';
  return (
    <div
      aria-hidden
      className={cn('pointer-events-none fixed inset-0 -z-10 overflow-hidden', className)}
    >
      {/* Drifting aurora blobs */}
      <div className={cn('absolute inset-0', opacity)}>
        <div className="absolute -left-32 -top-40 h-[42rem] w-[42rem] rounded-full bg-electric-signal/30 blur-[120px] animate-blob" />
        <div
          className="absolute -right-40 top-10 h-[38rem] w-[38rem] rounded-full bg-lavender-mist/25 blur-[130px] animate-blob"
          style={{ animationDelay: '-7s' }}
        />
        <div
          className="absolute bottom-[-18rem] left-1/3 h-[40rem] w-[40rem] rounded-full bg-sky-wash/25 blur-[140px] animate-blob"
          style={{ animationDelay: '-13s' }}
        />
      </div>

      {/* Faint technical grid, masked to fade at the edges */}
      {grid && <div className="absolute inset-0 bg-grid mask-fade opacity-[0.4]" />}
    </div>
  );
}
