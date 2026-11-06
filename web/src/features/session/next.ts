/**
 * The `?next=` of the sign-in page: where to go back to once signed in. Only a path of this
 * app counts (never another origin, never the sign-in page itself); anything else means `/`.
 */
export const LOGIN_PATH = "/login";

export function safeNext(next: unknown): string {
  if (typeof next !== "string" || !next.startsWith("/")) return "/";
  // "//host" and "/\host" are protocol-relative to a browser: another origin.
  if (next.startsWith("//") || next.startsWith("/\\")) return "/";
  const path = next.split(/[?#]/, 1)[0] ?? "";
  if (path === LOGIN_PATH || path.startsWith(`${LOGIN_PATH}/`)) return "/";
  return next;
}

/** The `next` to put on `/login` for the location being left; none for the home page. */
export function nextFor(href: string): string | undefined {
  const next = safeNext(href);
  return next === "/" ? undefined : next;
}
