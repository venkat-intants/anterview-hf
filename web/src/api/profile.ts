// Profile API — own profile update + viewing another user's profile.
// Talks to data_gateway (VITE_API_BASE_URL) via the central client.

import { apiGet, apiPatch } from './client';
import type { MeResponse, ProfileUpdate, PublicProfile } from '@/types/auth';

/** Update the signed-in user's profile. Empty string clears a field. */
export function updateProfile(body: ProfileUpdate): Promise<MeResponse> {
  return apiPatch<MeResponse>('/auth/me/profile', body);
}

/** View another user's public profile (HR / admin / super-admin only). */
export function getUserProfile(userId: string): Promise<PublicProfile> {
  return apiGet<PublicProfile>(`/users/${userId}/profile`);
}

/**
 * Read an image File and return a downscaled JPEG data URI (square-ish, max
 * `max` px on the long edge) — keeps avatars tiny so they fit in a TEXT column
 * with no object storage needed.
 */
export function imageFileToDataUrl(file: File, max = 256): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read the image file.'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('That file is not a valid image.'));
      img.onload = () => {
        const scale = Math.min(1, max / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          reject(new Error('Canvas not supported.'));
          return;
        }
        ctx.drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      img.src = reader.result as string;
    };
    reader.readAsDataURL(file);
  });
}
