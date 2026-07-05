// lucide-react (this version) dropped brand icons — use the closest available
// glyphs for the social row. `X` is the actual Twitter/X mark.
import { X, AtSign, Globe } from 'lucide-react'

const COLS = [
  { h: 'Product', links: ['Features', 'How it works', 'Languages'] },
  { h: 'Solutions', links: ['For Colleges', 'For Corporate HR', 'For Govt Skilling', 'Book a demo'] },
  { h: 'Legal', links: ['Privacy', 'DPDP Compliance', 'Terms', 'Data Residency'] },
]

export function Footer() {
  return (
    <footer className="relative z-10 border-t border-white/[0.07] bg-midnight">
      <div className="mx-auto max-w-[1200px] px-6 pb-10 pt-14">
        <div className="mb-12 grid grid-cols-1 gap-8 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <div className="mb-3.5 flex items-center gap-2.5">
              <span className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-[9px] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]" style={{ background: 'linear-gradient(135deg,#112d72,#a887dc)' }}>
                <span className="h-[9px] w-[9px] rounded-full bg-white shadow-[0_0_10px_#fff]" />
              </span>
              <span className="text-[17px] font-semibold tracking-[-0.5px]">Anterview</span>
            </div>
            <p className="mb-4 max-w-[260px] text-[13.5px] leading-relaxed text-fog">Voice-first AI interviews for every candidate in Bharat. Fair, fast, in your language.</p>
            <div className="flex gap-2.5">
              {[X, AtSign, Globe].map((Icon, i) => (
                <a key={i} href="#" className="flex h-[34px] w-[34px] items-center justify-center rounded-[9px] border border-white/[0.08] bg-charcoal text-ash transition-colors hover:text-white">
                  <Icon size={16} />
                </a>
              ))}
            </div>
          </div>
          {COLS.map((c) => (
            <div key={c.h}>
              <div className="mb-3.5 text-xs uppercase tracking-[1px] text-slate">{c.h}</div>
              <div className="flex flex-col gap-2.5 text-sm">
                {c.links.map((l) => <a key={l} href="#" className="text-ash transition-colors hover:text-white">{l}</a>)}
              </div>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-6">
          <span className="text-[13px] text-slate">© 2026 Intants Private Limited</span>
          <span className="text-[13px] text-ash">Made for Bharat</span>
        </div>
      </div>
    </footer>
  )
}
