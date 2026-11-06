/**
 * The signed-in User (`GET /api/me`) and what the screens derive from it. The `_app` layout
 * loads it before rendering anything, so inside the shell `useMe()` always has data; the
 * sign-in page reads it to know whether authentication is on at all.
 */
import type { QueryClient } from "@tanstack/react-query";

import { $api } from "./client";
import type { FeedRead, MeRead } from "./types";

/** Re-read now and then: a demotion or a new quota applies without a reload. */
export const ME_STALE_MS = 60_000;

export function meQueryOptions() {
  return $api.queryOptions("get", "/api/me", undefined, { staleTime: ME_STALE_MS, retry: false });
}

export const ME_QUERY_KEY = meQueryOptions().queryKey;

/** The signed-in User; undefined only outside the `_app` layout (the sign-in page, tests). */
export function useMe(): MeRead | undefined {
  return $api.useQuery("get", "/api/me", undefined, { staleTime: ME_STALE_MS, retry: false }).data;
}

export function useIsAdmin(): boolean {
  return useMe()?.admin === true;
}

/** Forget the cached User, so the next `ensureQueryData` asks the server again. */
export function forgetMe(queryClient: QueryClient): void {
  queryClient.removeQueries({ queryKey: ME_QUERY_KEY, exact: true });
}

/** What an owner label needs from a Feed. */
export type Owned = Pick<FeedRead, "owner"> & { owner_removed?: boolean | null };

/**
 * The owner to label a Feed with: only an Admin sees labels, and only on another User's Feed
 * (their own, and every Feed in open mode, carry none). Null when nothing is shown.
 */
export function ownerLabel(me: MeRead | undefined, feed: Owned | null | undefined): string | null {
  if (!me?.admin || !feed?.owner || feed.owner === me.username) return null;
  return feed.owner_removed ? `${feed.owner} (owner removed)` : feed.owner;
}
