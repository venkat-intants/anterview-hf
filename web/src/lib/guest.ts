/** True for a session minted from an interview invitation link rather than a
 *  sign-in: the token holds `guest_candidate` and nothing else.
 *
 *  A guest may take the one interview they were invited to and read its
 *  scorecard. Every other account page refuses the token on the server, so a
 *  screen a guest can reach must not offer links to them.
 *
 *  Mirrors the server's own test (`get_non_guest_user` in interview_core): a
 *  token carrying a real role alongside `guest_candidate` is not a guest. */
export function isGuestOnly(roles: readonly string[] | null | undefined): boolean {
  if (!roles || roles.length === 0) return false;
  return roles.every((r) => r === 'guest_candidate');
}
