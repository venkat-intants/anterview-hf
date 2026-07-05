/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_INTERVIEW_API_URL: string;
  readonly VITE_FEEDBACK_API_URL: string;
  readonly VITE_ADMIN_API_URL: string;
  readonly VITE_WEBSOCKET_URL: string;
  readonly VITE_APP_NAME: string;
  readonly VITE_APP_ENV: string;
  readonly VITE_USE_MOCK: string;
  readonly VITE_AVATAR_PROVIDER: string;
  readonly VITE_RPM_SUBDOMAIN: string;
  readonly VITE_HEYGEN_AVATAR_ID: string;
  readonly VITE_SENTRY_DSN: string;
  readonly VITE_FEATURE_AVATAR: string;
  readonly VITE_FEATURE_VOICE_INTERRUPTION: string;
  readonly VITE_FEATURE_MULTILINGUAL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
