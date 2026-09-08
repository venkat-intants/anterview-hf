import { AuroraField } from '../../components/AuroraField'
import { useLandingTheme } from '../../lib/useLandingTheme'
import { Nav } from './Nav'
import { Hero } from './Hero'
import { ProblemSolution, HowItWorks, LiveDemo } from './Sections'
import { FeatureBento, Avatars, Languages, AudienceTabs } from './Showcase'
import { ScorecardPreview, Metrics, Compliance, FAQ, FinalCTA } from './Proof'
import { Footer } from './Footer'

/**
 * AntHire landing page — full section-by-section, converted to the host
 * stack (React 18 + TS + Tailwind + Radix-ready + framer-motion + lucide).
 * Drop into your router: <Route path="/" element={<LandingPage />} />
 *
 * `data-landing-theme` on this root is what every colour below resolves
 * against (tokens in landing/styles/anthire.css). It is scoped here rather
 * than on <html> so the signed-in app's single dark theme is untouched.
 */
export function LandingPage() {
  const [theme, setTheme] = useLandingTheme()

  return (
    <div
      data-landing-theme={theme}
      className="relative min-h-screen overflow-x-hidden bg-[var(--lp-canvas)] font-inter text-[var(--lp-text)] antialiased"
    >
      <AuroraField />
      <Nav theme={theme} onThemeChange={setTheme} />
      <main className="relative">
        <Hero />
        <ProblemSolution />
        <HowItWorks />
        <LiveDemo />
        <FeatureBento />
        <Avatars />
        <Languages />
        <AudienceTabs />
        <ScorecardPreview />
        <Metrics />
        <Compliance />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  )
}

export default LandingPage
