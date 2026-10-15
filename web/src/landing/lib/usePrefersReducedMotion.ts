import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

function read(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(QUERY).matches
}

/** Live `prefers-reduced-motion: reduce`. A tiny local hook (no framer-motion)
 *  so the product demo's JavaScript stays small and testable. */
export function usePrefersReducedMotion(): boolean {
  const [reduce, setReduce] = useState(read)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined
    const mq = window.matchMedia(QUERY)
    const onChange = () => setReduce(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduce
}
