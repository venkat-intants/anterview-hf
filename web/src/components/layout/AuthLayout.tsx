// AuthLayout — premium split-screen shell shared by Login & Register.
//
// Left  (lg+): aurora brand panel — gradient matches the design's AuthSplit
//              (black → deep-navy → indigo → lavender → rose), animated glow,
//              value props, trust line.
// Right (all): dark form column (passed as `children`), centered on the canvas.
//
// The brand panel is decorative and hidden below `lg`; on small screens the
// form fills the viewport and each page renders its own compact mobile logo.

import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import { Sparkles, Languages, ShieldCheck } from '@/design/components/icons';

interface Feature {
  icon: typeof Sparkles;
  titleKey: string;
  descKey: string;
}

const FEATURES: Feature[] = [
  { icon: Sparkles,    titleKey: 'auth.feature1Title', descKey: 'auth.feature1Desc' },
  { icon: Languages,   titleKey: 'auth.feature2Title', descKey: 'auth.feature2Desc' },
  { icon: ShieldCheck, titleKey: 'auth.feature3Title', descKey: 'auth.feature3Desc' },
];

// ── Brand showcase (desktop only) ────────────────────────────────────────────
function BrandPanel() {
  const { t } = useTranslation();

  return (
    <aside
      className="relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-between lg:p-12 xl:p-14"
      style={{
        background:
          'linear-gradient(160deg,#000000 0%,#112d72 30%,#4b52aa 50%,#a887dc 72%,#e6c4e7 96%,#fcdbef 107%)',
      }}
    >
      {/* Ambient glow blob */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -right-24 top-1/3 h-80 w-80 rounded-full bg-white/20 blur-3xl" />
        {/* Hairline grid for depth */}
        <div
          className="absolute inset-0 opacity-[0.05]"
          style={{
            backgroundImage:
              'linear-gradient(to right,#fff 1px,transparent 1px),linear-gradient(to bottom,#fff 1px,transparent 1px)',
            backgroundSize: '44px 44px',
          }}
        />
      </div>

      {/* Top — brand lockup */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="relative z-10"
      >
        <Link
          to="/"
          className="inline-flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
          aria-label="Anterview home"
        >
          {/* Dot-in-rounded-square logo mark — matches AuthSplit design */}
          <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-black/30 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.3)] backdrop-blur">
            <span className="h-2.5 w-2.5 rounded-full bg-white" />
          </span>
          <span className="text-[17px] font-semibold tracking-tight text-white">Anterview</span>
        </Link>
      </motion.div>

      {/* Middle — value proposition + features */}
      <div className="relative z-10 mt-auto max-w-md">
        <motion.h2
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, delay: 0.1 }}
          className="text-[34px] font-semibold leading-[1.1] tracking-[-1.4px] text-black"
        >
          {t('auth.brandTagline')}
        </motion.h2>

        <ul className="mt-8 flex flex-col gap-4">
          {FEATURES.map((f, i) => {
            const Icon = f.icon;
            return (
              <motion.li
                key={f.titleKey}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.45, delay: 0.2 + i * 0.1 }}
                className="flex items-center gap-3"
              >
                <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-black/15 backdrop-blur">
                  <Icon className="h-[17px] w-[17px] text-black/80" aria-hidden="true" />
                </span>
                <div>
                  <p className="text-[14px] font-semibold text-black/90">{t(f.titleKey)}</p>
                  <p className="mt-0.5 text-[13px] leading-relaxed text-black/65">{t(f.descKey)}</p>
                </div>
              </motion.li>
            );
          })}
        </ul>
      </div>

      {/* Bottom — trust line */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.55 }}
        className="relative z-10 mt-10 flex items-center gap-2 text-[13px] text-black/65"
      >
        <ShieldCheck className="h-4 w-4 shrink-0 text-black/70" aria-hidden="true" />
        {t('auth.trustNote')}
      </motion.div>
    </aside>
  );
}

interface AuthLayoutProps {
  children: React.ReactNode;
}

export default function AuthLayout({ children }: AuthLayoutProps) {
  return (
    // Dark canvas matching the design's `bg-black font-sans text-white`
    <main className="min-h-screen w-full bg-black font-sans text-white lg:grid lg:grid-cols-[1.05fr_1fr] xl:grid-cols-[1.15fr_1fr]">
      <BrandPanel />

      {/* Form column — dark panel, vertically centered */}
      <div className="flex min-h-screen items-center justify-center px-6 py-12 lg:min-h-0">
        <div className="w-full max-w-[380px]">{children}</div>
      </div>
    </main>
  );
}
