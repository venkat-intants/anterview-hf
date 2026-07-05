import { motion, useScroll, useTransform, useReducedMotion } from 'framer-motion'

/**
 * Fixed ambient layer: two aurora blobs (parallax on scroll) + a drifting
 * particle field + two slow light beams. Lives behind all content (z-0).
 */
export function AuroraField() {
  const reduce = useReducedMotion()
  const { scrollY } = useScroll()
  const yA = useTransform(scrollY, [0, 2000], [0, 120])
  const yB = useTransform(scrollY, [0, 2000], [0, -90])

  const particles = Array.from({ length: 26 }).map((_, i) => {
    const r = (n: number) => {
      const v = Math.sin(i * 12.9898 + n * 78.233) * 43758.5453
      return v - Math.floor(v)
    }
    const blue = r(3) > 0.45
    return {
      left: `${(r(1) * 100).toFixed(1)}%`,
      top: `${(r(2) * 100).toFixed(1)}%`,
      size: `${(2 + r(4) * 3.5).toFixed(1)}px`,
      dur: `${(11 + r(5) * 15).toFixed(1)}s`,
      delay: `-${(r(6) * 20).toFixed(1)}s`,
      color: blue ? 'rgba(0,136,255,0.6)' : 'rgba(168,135,220,0.55)',
      glow: `${(4 + r(7) * 7).toFixed(0)}px`,
    }
  })

  return (
    <div className="at-anim pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <motion.div
        style={reduce ? undefined : { y: yA }}
        className="absolute -left-[10%] -top-[20%] h-[60vw] w-[60vw] rounded-full blur-[60px] animate-aurora"
      >
        <div className="h-full w-full rounded-full" style={{ background: 'radial-gradient(circle at center, rgba(75,82,170,0.55), rgba(17,45,114,0.18) 45%, transparent 70%)' }} />
      </motion.div>
      <motion.div
        style={reduce ? undefined : { y: yB }}
        className="absolute -right-[15%] top-[10%] h-[55vw] w-[55vw] rounded-full blur-[70px] animate-aurora-2"
      >
        <div className="h-full w-full rounded-full" style={{ background: 'radial-gradient(circle at center, rgba(168,135,220,0.42), rgba(221,85,231,0.14) 45%, transparent 70%)' }} />
      </motion.div>

      <div className="absolute inset-0" style={{ background: 'radial-gradient(120% 80% at 50% -10%, rgba(0,136,255,0.10), transparent 55%)' }} />
      <div className="absolute left-0 top-0 h-full w-[36%] animate-beam-sweep blur-[26px]" style={{ background: 'linear-gradient(90deg, transparent, rgba(0,136,255,0.06), transparent)' }} />
      <div className="absolute left-0 top-0 h-full w-[30%] animate-beam-sweep blur-[30px] [animation-delay:7s]" style={{ background: 'linear-gradient(90deg, transparent, rgba(168,135,220,0.06), transparent)' }} />

      {!reduce &&
        particles.map((p, i) => (
          <span
            key={i}
            className="absolute rounded-full animate-float-up"
            style={{
              left: p.left,
              top: p.top,
              width: p.size,
              height: p.size,
              background: p.color,
              boxShadow: `0 0 ${p.glow} ${p.color}`,
              animationDuration: p.dur,
              animationDelay: p.delay,
            }}
          />
        ))}
    </div>
  )
}
