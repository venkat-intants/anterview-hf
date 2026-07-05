// Notifications API — data_gateway (VITE_API_BASE_URL).
// Backs the AppShell header bell. Endpoints:
//   GET  /notifications        → { items, unread_count }
//   POST /notifications/{id}/read
//   POST /notifications/read-all

import { apiGet, apiPost } from './client';

export interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  read: boolean;
  created_at: string;
}

export interface NotificationList {
  items: NotificationItem[];
  unread_count: number;
}

export function listNotifications(limit = 30): Promise<NotificationList> {
  return apiGet<NotificationList>(`/notifications?limit=${limit}`);
}

export function markNotificationRead(id: string): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>(`/notifications/${id}/read`, {});
}

export function markAllNotificationsRead(): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>(`/notifications/read-all`, {});
}
