/** "Waiting 3 days" — how long a request has been sitting in the queue. */
export function waitingText(iso: string | null): string {
  if (!iso) return '';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (Number.isNaN(days)) return '';
  if (days <= 0) return 'Submitted today';
  return days === 1 ? 'Waiting 1 day' : `Waiting ${days} days`;
}
