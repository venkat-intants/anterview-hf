import { cn } from '../lib/cn'

/**
 * Stylized CSS AI-interviewer face (aurora-toned). Used as the AI tile's
 * fallback / marketing visual. In-app, replace with a Tavus CVI <iframe>
 * or a LiveKit video track (see Hero.tsx).
 */
export function AvatarFace({ className, speaking = true }: { className?: string; speaking?: boolean }) {
  return (
    <div className={cn('relative overflow-hidden rounded-full', className)}
      style={{ background: 'radial-gradient(120% 110% at 50% 18%, #1a2456 0%, #0a0e26 60%, #05060f 100%)', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.14), inset 0 -28px 56px rgba(168,135,220,0.25)' }}>
      <div className="absolute inset-0" style={{ background: 'radial-gradient(60% 50% at 50% 92%, rgba(168,135,220,0.5), transparent 70%)' }} />
      {/* shoulders */}
      <div className="absolute -bottom-[12%] left-1/2 h-[52%] w-[92%] -translate-x-1/2 rounded-t-full" style={{ background: 'linear-gradient(180deg,#3a3f7a,#1a1d3a)' }} />
      {/* head */}
      <div className="absolute left-1/2 top-[20%] h-[55%] w-[46%] -translate-x-1/2 rounded-[54px_54px_50px_50px]"
        style={{ background: 'radial-gradient(70% 60% at 50% 35%, #b9a3d8 0%, #8a76b4 55%, #5d4f86 100%)', boxShadow: 'inset -8px -6px 20px rgba(0,0,0,0.35), inset 7px 4px 16px rgba(255,255,255,0.18)' }}>
        <div className="absolute -top-2 left-1/2 h-[50%] w-[110%] -translate-x-1/2 rounded-[57px_57px_36px_36px]" style={{ background: 'linear-gradient(180deg,#15183a,#2a2550)' }} />
        <div className="absolute left-[24%] top-[44%] h-[7%] w-[14%] rounded-full bg-[#0f1330] animate-dot-pulse" style={{ boxShadow: '0 0 8px rgba(0,136,255,0.6)' }} />
        <div className="absolute right-[24%] top-[44%] h-[7%] w-[14%] rounded-full bg-[#0f1330] animate-dot-pulse" style={{ boxShadow: '0 0 8px rgba(0,136,255,0.6)' }} />
        {/* mouth */}
        <div className="absolute bottom-[20%] left-1/2 flex h-[13%] -translate-x-1/2 items-center gap-[3px]">
          {[0.55, 0.9, 1, 0.78, 0.52].map((h, i) => (
            <span key={i} className={speaking ? 'animate-wave' : ''}
              style={{ width: 3, height: `${h * 100}%`, borderRadius: 2, background: i === 2 ? '#fff' : '#fcdbef', animationDelay: `${i * 0.08}s` }} />
          ))}
        </div>
      </div>
    </div>
  )
}
