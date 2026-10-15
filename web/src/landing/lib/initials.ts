/** "Gloria (Warm)" -> "GW", "Anna" -> "A". Letters only, at most two. */
export function initialsOf(name: string): string {
  const words = name.split(/[^\p{L}]+/u).filter(Boolean)
  return words
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join('')
}
