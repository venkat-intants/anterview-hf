// Consent API — data_gateway POST /consent and GET /consent/status
// Both routes are JWT-protected and live under VITE_API_BASE_URL (port 8002).
// Switches between mock and real backend via VITE_USE_MOCK env var.

import type { ConsentStatus, ConsentResult, ConsentRequest, ConsentType } from '../types/consent';
import { simulateDelay } from './mock';
import { apiGet, apiPost } from './client';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

/**
 * GET /consent/status — returns current consent state for the authenticated user.
 * consentType defaults to the voice type (backward compatible); pass
 * 'video_capture' to check the candidate-webcam consent.
 * The `_token` parameter is accepted for backwards-compatibility but ignored.
 */
export async function getConsentStatus(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
  consentType: ConsentType = 'interview_voice_recording',
): Promise<ConsentStatus> {
  if (USE_MOCK) {
    await simulateDelay(200);
    // Default mock: user has not yet consented
    return { consented: false, consent_id: null, granted_at: null };
  }
  const qs = consentType === 'interview_voice_recording'
    ? ''
    : `?consent_type=${encodeURIComponent(consentType)}`;
  return apiGet<ConsentStatus>(`/consent/status${qs}`);
}

/**
 * POST /consent — records consent for purpose="interview", version=1.
 * consentType defaults to the voice type (backward compatible); pass
 * 'video_capture' to record the candidate-webcam consent.
 * Returns 201 (first time) or 200 (idempotent). Both are handled as success.
 * The `_token` parameter is accepted for backwards-compatibility but ignored.
 */
export async function postConsent(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
  consentType: ConsentType = 'interview_voice_recording',
): Promise<ConsentResult> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return {
      consented: true,
      consent_id: 'mock-consent-' + Math.random().toString(36).slice(2, 10),
      granted_at: new Date().toISOString(),
    };
  }
  const body: ConsentRequest = { purpose: 'interview', version: 1, consent_type: consentType };
  return apiPost<ConsentResult>('/consent', body);
}
