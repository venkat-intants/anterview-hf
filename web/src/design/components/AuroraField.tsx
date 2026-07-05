import { memo } from 'react';
import { cn } from '../lib/cn';

interface AuroraFieldProps {
  /** extra classes on the fixed wrapper */
  className?: string;
  /** dim the field (use behind dense dashboards) */
  subtle?: boolean;
}

/** Ambient drifting aurora blobs. Fixed, non-interactive, behind content. */
function AuroraFieldBase({ className, subtle = false }: AuroraFieldProps): JSX.Element {
  const o = subtle ? 0.5 : 1;
  return (
    <div
      aria-hidden="true"
      className={cn('pointer-events-none fixed inset-0 overflow-hidden', className)}
      style={{ zIndex: 0 }}
    >
      <div
        className="av-aurora-blob absolute rounded-full"
        style={{
          top: '-28%', left: '6%', width: '52vw', height: '52vw',
          background: 'radial-gradient(circle,rgba(75,82,170,0.22),transparent 65%)',
          filter: 'blur(90px)', opacity: o,
        }}
      />
      <div
        className="av-aurora-blob absolute rounded-full"
        style={{
          bottom: '-30%', right: '4%', width: '46vw', height: '46vw',
          background: 'radial-gradient(circle,rgba(168,135,220,0.16),transparent 65%)',
          filter: 'blur(95px)', opacity: o, animationDelay: '-9s',
        }}
      />
    </div>
  );
}

export const AuroraField = memo(AuroraFieldBase);
