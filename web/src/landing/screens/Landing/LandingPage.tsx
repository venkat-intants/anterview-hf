import { AuroraField } from '../../components/AuroraField'
import { Nav } from './Nav'
import { Hero } from './Hero'
import { TrustMarquee, ProblemSolution, HowItWorks, LiveDemo } from './Sections'
import { FeatureBento, Avatars, Languages, AudienceTabs } from './Showcase'
import { ScorecardPreview, Metrics, Testimonials, Compliance, FAQ, FinalCTA } from './Proof'
import { Footer } from './Footer'

/**
 * Anterview landing page — full section-by-section, converted to the host
 * stack (React 18 + TS + Tailwind + Radix-ready + framer-motion + lucide).
 * Drop into your router: <Route path="/" element={<LandingPage />} />
 */
export function LandingPage() {
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-midnight font-inter text-white antialiased">
      <AuroraField />
      <Nav />
      <main className="relative">
        <Hero />
        <TrustMarquee />
        <ProblemSolution />
        <HowItWorks />
        <LiveDemo />
        <FeatureBento />
        <Avatars />
        <Languages />
        <AudienceTabs />
        <ScorecardPreview />
        <Metrics />
        <Testimonials />
        <Compliance />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  )
}

export default LandingPage
