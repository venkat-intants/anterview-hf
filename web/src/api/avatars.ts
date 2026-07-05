// Avatars API — fetches the list of available interview avatars from interview_core.
// Endpoint: GET /api/avatars (VITE_INTERVIEW_API_URL, Bearer JWT).
// Stable order from backend: lucas, anna, gloria.

import type { Avatar, AvatarsResponse } from '../types/interview';
import { simulateDelay } from './mock';
import { clientFetch } from './client';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const INTERVIEW_BASE: string = import.meta.env.VITE_INTERVIEW_API_URL;
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

// ---------------------------------------------------------------------------
// Mock data — mirrors the stable backend ordering
// ---------------------------------------------------------------------------

const MOCK_AVATARS: Avatar[] = [
  {
    id: 'lucas',
    name: 'Lucas',
    gender: 'male',
    thumbnail_url: 'https://placehold.co/280x360/4f46e5/ffffff?text=Lucas',
  },
  {
    id: 'anna',
    name: 'Anna',
    gender: 'female',
    thumbnail_url: 'https://placehold.co/280x360/db2777/ffffff?text=Anna',
  },
  {
    id: 'gloria',
    name: 'Gloria',
    gender: 'female',
    thumbnail_url: 'https://placehold.co/280x360/7c3aed/ffffff?text=Gloria',
  },
];

// ---------------------------------------------------------------------------
// getAvatars
// ---------------------------------------------------------------------------

/**
 * Fetch the list of available interview avatars from interview_core.
 * Returns avatars in stable backend order: lucas, anna, gloria.
 * On any network/parse error the caller should fall back to the default avatar.
 */
export async function getAvatars(): Promise<Avatar[]> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return MOCK_AVATARS;
  }

  const data = await clientFetch<AvatarsResponse>(`${INTERVIEW_BASE}/api/avatars`);
  return data.avatars;
}
