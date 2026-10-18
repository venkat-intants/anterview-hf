import { Link } from 'react-router-dom'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '../../lib/cn'
import { mailto } from '../../data/landing'
import { Chip } from '../../components/primitives'
import { ProductDemo } from './ProductDemo'

export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-title" className="mx-auto max-w-[1200px] px-4 pb-16 pt-10 sm:px-6 sm:pb-24 sm:pt-16">
      <div className="grid grid-cols-1 items-center gap-14 lg:grid-cols-[1.05fr_0.95fr] lg:gap-10">
        <div>
          <Chip className="mb-6">AI-assisted hiring, from application to decision</Chip>
          <h1 id="hero-title" className="text-[clamp(36px,6.4vw,64px)] font-extrabold leading-[1.04] tracking-[-0.03em] text-[var(--lp-text)]">
            Hire faster, with a fair first round for every applicant.
          </h1>
          <p className="mt-5 max-w-[520px] text-lg leading-relaxed text-[var(--lp-text-muted)]">
            AntHire scores CVs against the role, runs assessments and lifelike AI voice interviews, and gives your team a scorecard for each candidate. Your people make the decision.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href={mailto('AntHire demo request')} className={cn(buttonVariants({ size: 'lg' }), 'h-12 px-8 text-base')}>
              Book a demo
            </a>
            <Link to="/register" className={cn(buttonVariants({ variant: 'outline', size: 'lg' }), 'h-12 px-8 text-base')}>
              Candidate sign up
            </Link>
          </div>
          <p className="mt-5 text-sm text-[var(--lp-text-muted)]">Companies are set up with our team. Candidates need nothing but a browser.</p>
        </div>
        <ProductDemo className="mx-auto w-full max-w-[560px] lg:mx-0 lg:ml-auto" />
      </div>
    </section>
  )
}
