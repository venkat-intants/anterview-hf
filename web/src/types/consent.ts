// Consent types — mirror data_gateway Pydantic models for DPDP Act 2023 compliance
// POST /consent and GET /consent/status live under data_gateway (port 8002)

export interface ConsentStatus {
  consented: boolean;
  consent_id: string | null;
  granted_at: string | null;
}

export interface ConsentResult {
  consented: true;
  consent_id: string;
  granted_at: string;
}

export type ConsentType = 'interview_voice_recording' | 'video_capture';

export interface ConsentRequest {
  purpose: 'interview';
  version: 1;
  // Optional — backend defaults to 'interview_voice_recording'.
  consent_type?: ConsentType;
}
