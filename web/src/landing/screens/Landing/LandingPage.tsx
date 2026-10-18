import { useLandingTheme } from '../../lib/useLandingTheme'
import { Nav } from './Nav'
import { Hero } from './Hero'
import { ProofBand } from './Sections'
import { Walkthrough } from './Walkthrough'
import { ForCandidates, Audiences, Avatars, Languages } from './Showcase'
import { Privacy, FAQ, FinalCTA } from './Proof'
import { Footer } from './Footer'

/**
 * AntHire landing page. A cool neutral canvas, white cards, one blue for the
 * primary action — the same identity as the signed-in app, because every
 * `--lp-*` colour is an alias of an app token (landing/styles/anthire.css).
 *
 * `data-landing-theme` on this root mirrors the shared light/dark mode and
 * scopes those variables to the landing tree.
 */
export function LandingPage() {
  const [theme, setTheme] = useLandingTheme()

  return (
    <div data-landing-theme={theme} className="min-h-screen overflow-x-clip bg-[var(--lp-canvas)] text-[var(--lp-text)] antialiased">
      <Nav theme={theme} onThemeChange={setTheme} />
      <main id="main">
        <Hero />
        <ProofBand />
        <Walkthrough />
        <Audiences />
        <ForCandidates />
        <Avatars />
        <Languages />
        <Privacy />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  )
}

export default LandingPage
