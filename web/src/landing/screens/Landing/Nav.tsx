import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '../../lib/cn'
import { NAV_LINKS } from '../../data/landing'

export function Nav() {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 40)
    window.addEventListener('scroll', on, { passive: true })
    on()
    return () => window.removeEventListener('scroll', on)
  }, [])

  return (
    <div className="fixed left-1/2 top-4 z-50 w-[calc(100%-32px)] max-w-[1160px] -translate-x-1/2">
      <div
        className={cn(
          'flex items-center justify-between gap-4 rounded-pill border border-white/10 backdrop-blur-xl transition-all duration-300',
          scrolled ? 'bg-obsidian/80 px-3 py-1.5 shadow-[0_8px_32px_rgba(0,0,0,0.6)]' : 'bg-obsidian/60 px-5 py-2.5 shadow-[0_8px_32px_rgba(0,0,0,0.45)]',
        )}
      >
        <a href="#top" className="flex items-center gap-2.5 text-white">
          <span className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-[9px] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]" style={{ background: 'linear-gradient(135deg,#112d72,#a887dc)' }}>
            <span className="h-[9px] w-[9px] rounded-full bg-white shadow-[0_0_10px_#fff]" />
          </span>
          <span className="text-[17px] font-semibold tracking-[-0.5px]">Anterview</span>
        </a>
        <div className="hidden items-center gap-[26px] text-[13.5px] md:flex">
          {NAV_LINKS.map((l, i) => (
            <a key={i} href={l.href} className="text-ash transition-colors hover:text-white">{l.label}</a>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-pill border border-white/[0.08] bg-charcoal/80 p-[3px] text-[11px] font-medium text-ash">
            <span className="rounded-pill bg-white px-[9px] py-1 text-black">EN</span>
            <span className="cursor-pointer px-2 py-1">हि</span>
            <span className="cursor-pointer px-2 py-1">తె</span>
          </div>
          <Link to="/login" className="px-3.5 py-2 text-[13.5px] text-ash transition-colors hover:text-white">Login</Link>
          <Link to="/register" className="rounded-pill bg-white px-4 py-2 text-[13.5px] font-semibold text-black shadow-[0_0_0_1px_rgba(255,255,255,0.6),0_4px_18px_rgba(168,135,220,0.4)] transition-transform hover:-translate-y-px">Get Started</Link>
        </div>
      </div>
    </div>
  )
}
