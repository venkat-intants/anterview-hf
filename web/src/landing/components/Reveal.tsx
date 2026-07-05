import { motion, useReducedMotion } from 'framer-motion'
import { type PropsWithChildren } from 'react'
import { cn } from '../lib/cn'
import { reveal, type RevealKind } from '../lib/motion'

type RevealProps = PropsWithChildren<{
  kind?: RevealKind
  className?: string
  /** fraction of element visible before it animates in (0–1) */
  amount?: number
  /** re-run on every enter/exit instead of once */
  once?: boolean
}>

/** Scroll-in wrapper. <Reveal kind="left">…</Reveal> */
export function Reveal({ children, kind = 'fade', className, amount = 0.3, once = false }: RevealProps) {
  const reduce = useReducedMotion()
  if (reduce) return <div className={className}>{children}</div>
  return (
    <motion.div
      className={cn(className)}
      variants={reveal}
      custom={kind}
      initial="hidden"
      whileInView="show"
      viewport={{ amount, once }}
    >
      {children}
    </motion.div>
  )
}
