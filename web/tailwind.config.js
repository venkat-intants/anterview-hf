import tailwindcssAnimate from 'tailwindcss-animate';

/** @type {import('tailwindcss').Config} */
export default {
  // shadcn/ui requires darkMode: 'class'. lib/useThemeMode.ts keeps the class
  // in step with html[data-mode].
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: {
        '2xl': '1400px',
      },
    },
    extend: {
      colors: {
        // The numbered indigo scale is kept so `primary-600`-style references
        // still resolve. `primary` (no number) is the action colour from
        // --primary: the brand blue, lifted (with ink text) in dark mode.
        primary: {
          50: '#eef2ff',
          100: '#e0e7ff',
          200: '#c7d2fe',
          300: '#a5b4fc',
          400: '#818cf8',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
          800: '#3730a3',
          900: '#312e81',
          // shadcn CSS-variable shim
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },

        // shadcn/ui semantic tokens — values live in index.css
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        // shadcn's hover/active surface. Points at --surface-accent, not
        // --accent: --accent is the brand signal HEX, and `hsl(<hex>)` is not a
        // colour, so every `bg-accent` hover silently rendered nothing.
        accent: {
          DEFAULT: 'hsl(var(--surface-accent))',
          foreground: 'hsl(var(--surface-accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          // The button fill. See --destructive-solid in index.css for why it
          // is not simply DEFAULT.
          solid: 'hsl(var(--destructive-solid))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',

        // The highlight tint — a fill with its own ink, never a text colour.
        // Valued per mode, so never assume it is light.
        highlight: {
          DEFAULT: 'var(--ui-highlight)',
          foreground: 'var(--ui-highlight-ink)',
        },

        // ── Signal accent aliases ───────────────────────────────────────────
        // `electric*` is the signal and `lavender*` its gradient partner. Both
        // read the accent CSS vars (index.css + the header accent picker), so
        // `bg-electric-signal/30`, `text-lavender`, etc. recolour per theme.
        'electric-signal': 'rgb(var(--accent-rgb-sp) / <alpha-value>)',
        electric: 'rgb(var(--accent-rgb-sp) / <alpha-value>)',
        'lavender-mist': 'rgb(var(--accent-2-rgb-sp) / <alpha-value>)',
        lavender: 'rgb(var(--accent-2-rgb-sp) / <alpha-value>)',
        'sky-wash': '#60a5fa',
        ember: '#e6714f',
        'amber-glow': '#ffb764',
        sunburst: '#ffdd00',
        'forest-pulse': '#16c253',
        'vivid-mint': '#27c93f',
        'pink-static': '#dd55e7',
        // Neutrals / surfaces
        midnight: '#000000',
        obsidian: '#0f0f10',
        charcoal: '#1c1d1f',
        graphite: '#333333',
        slate: '#595959',
        fog: '#70757c',
        ash: '#888b91',
        silver: '#999999',
        mist: '#b8babf',
        pearl: '#cccccc',
        bone: '#e5e7eb',
        'deep-navy': '#001b33',
        'midnight-indigo': '#030719',
        // Bare aliases the anterview-ui landing design references directly
        // (sit alongside the -wash / -pulse / -glow / -static variants above).
        sky: '#60a5fa',
        forest: '#27c93f',
        amber: '#ffb764',
        pink: '#dd55e7',

      },

      // Fractional spacing steps the anterview-ui landing design uses
      // (py-15, p-6.5, px-5.5, mt-5.5, gap-4.5, …).
      spacing: {
        '4.5': '1.125rem',
        '5.5': '1.375rem',
        '6.5': '1.625rem',
        '7.5': '1.875rem',
        '15': '3.75rem',
      },

      backgroundImage: {
        // The single defining atmosphere of the brand.
        aurora:
          'linear-gradient(#000000 0.85%, #112d72 33.4%, #4b52aa 49.68%, #a887dc 70.84%, #e6c4e7 95.8%, #fcdbef 107.19%)',
        'aurora-wash':
          'linear-gradient(90deg, rgba(25,153,232,0.15) 2.75%, rgba(164,91,242,0.15) 99.26%)',
        'signal-blue': 'linear-gradient(#0fb7fa, #0072fb)',
      },

      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'system-ui', 'Segoe UI', 'Roboto', 'sans-serif'],
        // Headings. h1–h3 get this by default (index.css); use `font-display`
        // for a display-weight line that is not a heading element.
        display: ['"Plus Jakarta Sans"', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // mono prefers JetBrains Mono (loaded by design/styles/anthire.css).
        inter: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        flow: ['Inter Tight', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },

      // Inter type scale with the signature aggressive negative tracking.
      fontSize: {
        micro: ['10px', { lineHeight: '1.6', letterSpacing: '0.25px' }],
        caption: ['12px', { lineHeight: '1.56', letterSpacing: '0.3px' }],
        'body-sm': ['14px', { lineHeight: '1.5' }],
        body: ['16px', { lineHeight: '1.5', letterSpacing: '-0.16px' }],
        'body-lg': ['18px', { lineHeight: '1.5', letterSpacing: '-0.18px' }],
        subheading: ['24px', { lineHeight: '1.25', letterSpacing: '-0.6px' }],
        heading: ['30px', { lineHeight: '1.2', letterSpacing: '-1.2px' }],
        'heading-lg': ['48px', { lineHeight: '1.07', letterSpacing: '-2.4px' }],
        display: ['60px', { lineHeight: '1.06', letterSpacing: '-3.42px' }],
      },

      letterSpacing: {
        tightest: '-0.057em',
        display: '-3.42px',
        'heading-lg': '-2.4px',
        heading: '-1.2px',
        subheading: '-0.6px',
        body: '-0.16px',
      },

      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
        // The app's card radius. Controls use --radius (12px); buttons are pills.
        card: '16px',
        pill: '9999px',
        'sm-card': '12px',
      },

      boxShadow: {
        // The system's signature 1px white-alpha inset ring that defines edges
        // on dark surfaces.
        'inset-hairline': 'inset 0 0 0 1px rgb(255 255 255 / 0.14)',
        'inset-hairline-strong': 'inset 0 0 0 1px rgb(255 255 255 / 0.22)',
        'aurora-card': '0 1px 4px 0 rgb(0 0 0 / 0.25), 0 4px 59px 0 rgb(0 0 0 / 0.10)',

        // ── App elevation ────────────────────────────────────────────────────
        // Two steps, both per-mode tokens: a resting card, and a raised panel
        // (dialogs, popovers, a hovered interactive card).
        card: 'var(--ui-shadow-soft)',
        'card-hover': 'var(--ui-shadow-card)',
        elevated: 'var(--ui-shadow-card)',
        // A hovered interactive card: lifts a step above `card` without going
        // as far as a dialog. Per-mode token in index.css.
        lift: 'var(--shadow-lift)',


        // ── Futuristic glow elevation (Wave 1 redesign) ──────────────────────
        // Ring-tinted glows for primary actions and "live"/focus surfaces.
        glow: '0 0 0 1px hsl(var(--ring) / 0.18), 0 8px 30px -8px hsl(var(--ring) / 0.45)',
        'glow-lg': '0 0 0 1px hsl(var(--ring) / 0.22), 0 16px 50px -10px hsl(var(--ring) / 0.55)',
        'glow-soft': '0 10px 40px -12px hsl(var(--ring) / 0.35)',
        // Floating elevated panel that works on the light Fog canvas.
        float: '0 1px 2px rgb(29 29 31 / 0.04), 0 12px 32px -12px rgb(29 29 31 / 0.18)',
        'float-lg': '0 1px 2px rgb(29 29 31 / 0.04), 0 24px 60px -16px rgb(29 29 31 / 0.24)',
      },

      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        // ── Futuristic motion system (Wave 1 redesign) ───────────────────────
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(14px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.96)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'glow-pulse': {
          '0%, 100%': { opacity: '0.5' },
          '50%': { opacity: '1' },
        },
        'gradient-pan': {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
        'float-soft': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-8px)' },
        },
        blob: {
          '0%, 100%': { transform: 'translate(0, 0) scale(1)' },
          '33%': { transform: 'translate(28px, -34px) scale(1.12)' },
          '66%': { transform: 'translate(-22px, 18px) scale(0.94)' },
        },
        'border-flow': {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
      },

      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        // Keyframes for these live in index.css (so the raw `voice-bar` name used
        // by HeroCinematic's inline style also resolves).
        'aurora-drift': 'aurora-drift 22s ease-in-out infinite',
        'silhouette-float': 'silhouette-float 13s ease-in-out infinite',
        'laptop-float': 'laptop-float 16s ease-in-out infinite',
        'voice-bar': 'voice-bar 1.4s ease-in-out infinite',
        // ── Futuristic motion system (Wave 1 redesign) ───────────────────────
        'fade-up': 'fade-up 0.6s cubic-bezier(0.22, 1, 0.36, 1) both',
        'fade-in': 'fade-in 0.5s ease both',
        'scale-in': 'scale-in 0.4s cubic-bezier(0.22, 1, 0.36, 1) both',
        shimmer: 'shimmer 2.2s linear infinite',
        'glow-pulse': 'glow-pulse 3s ease-in-out infinite',
        'gradient-pan': 'gradient-pan 6s ease infinite',
        'float-soft': 'float-soft 6s ease-in-out infinite',
        'spin-slow': 'spin 16s linear infinite',
        blob: 'blob 20s ease-in-out infinite',
        'border-flow': 'border-flow 4s ease infinite',
      },
    },
  },
  plugins: [tailwindcssAnimate],
};
