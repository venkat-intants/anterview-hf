import { motion, useReducedMotion } from 'framer-motion'
import { type PropsWithChildren } from 'react'
import { reveal } from '../lib/motion'

type RevealProps = PropsWithChildren<{
  className?: string
  /** fraction of the element visible before it animates in (0–1) */
  amount?: number
}>

/** Scroll-in wrapper, played once. Renders plain under prefers-reduced-motion. */
export function Reveal({ children, className, amount = 0.2 }: RevealProps) {
  const reduce = useReducedMotion()
  if (reduce) return <div className={className}>{children}</div>
  return (
    <motion.div
      className={className}
      variants={reveal}
      initial="hidden"
      whileInView="show"
      viewport={{ amount, once: true }}
    >
      {children}
    </motion.div>
  )
}
