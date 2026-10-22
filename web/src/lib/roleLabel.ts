// roleLabel - the one English name for a person's role, for staff screens that
// show someone else's account (ProfileView). Every staff role is named, most
// senior first (audit AD-F28): an interviewer or a platform owner used to fall
// through to "Candidate".

export type RoleTone = 'electric' | 'forest' | 'lavender';

export function roleLabel(roles: readonly string[]): { label: string; tone: RoleTone } {
  if (roles.includes('platform_owner')) return { label: 'Platform owner', tone: 'lavender' };
  if (roles.includes('super_admin')) return { label: 'Super admin', tone: 'lavender' };
  if (roles.includes('admin')) return { label: 'Platform admin', tone: 'lavender' };
  if (roles.includes('hr_manager')) return { label: 'HR manager', tone: 'forest' };
  if (roles.includes('interviewer')) return { label: 'Interviewer', tone: 'forest' };
  return { label: 'Candidate', tone: 'electric' };
}
