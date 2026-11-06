/**
 * The one HTTP client. Every error surfaces as an ApiProblem (problem+json parsed,
 * network failures wrapped); query keys are `[method, path, init]` from openapi-react-query.
 */
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import createFetchClient, { type Middleware } from "openapi-fetch";
import createQueryClient from "openapi-react-query";
import { toast } from "sonner";

import { ApiProblem, describeProblem, problemFromBody } from "./problem";
import type { paths } from "./schema";

export { ApiProblem, asError, describeProblem } from "./problem";

/**
 * Called once per 401 from `/api` (no session, or it expired): the app clears its caches,
 * stops the event stream and shows the sign-in page. `/api/me` is left to the route that asked
 * for it (the layout's `beforeLoad` redirects itself), so one sign-out never navigates twice.
 */
type UnauthorizedHandler = (request: Request) => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/** Install the app's 401 handler; returns a function that removes it. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): () => void {
  unauthorizedHandler = handler;
  return () => {
    if (unauthorizedHandler === handler) unauthorizedHandler = null;
  };
}

/** The path every session-only route lives under, and the one route that reports the session. */
const API_PREFIX = "/api/";
export const ME_PATH = "/api/me";

function signalUnauthorized(request: Request): void {
  const { pathname } = new URL(request.url, API_BASE_URL || "http://localhost");
  if (!pathname.startsWith(API_PREFIX) || pathname === ME_PATH) return;
  unauthorizedHandler?.(request);
}

const problemMiddleware: Middleware = {
  async onResponse({ request, response }) {
    if (response.ok) return undefined;
    if (response.status === 401) signalUnauthorized(request);
    let body: unknown = null;
    const text = await response.clone().text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw problemFromBody(body, response, request);
  },
  onError({ error, request }) {
    if (ApiProblem.is(error)) return error;
    if (error instanceof DOMException && error.name === "AbortError") {
      return new ApiProblem({
        slug: "aborted",
        status: 0,
        title: "Cancelled",
        method: request.method,
        url: request.url,
        cause: error,
      });
    }
    return new ApiProblem({
      slug: "network",
      status: 0,
      title: "Network error",
      detail: error instanceof Error ? error.message : String(error),
      method: request.method,
      url: request.url,
      cause: error,
    });
  },
};

/** Same origin in the browser; an absolute origin is what Node's fetch (tests) requires. */
export const API_BASE_URL =
  typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";

export const fetchClient = createFetchClient<paths>({
  baseUrl: API_BASE_URL,
  // Late-bound so request interceptors installed after this module loads (MSW in tests) apply.
  fetch: (request) => globalThis.fetch(request),
  headers: { Accept: "application/json, application/problem+json" },
});
fetchClient.use(problemMiddleware);

/** Typed hooks: `$api.useQuery("get", "/api/feeds", { params: { query: {...} } })`. */
export const $api = createQueryClient(fetchClient);

/** Errors the UI handles inline and must not toast globally. */
const SILENT_SLUGS = new Set([
  "aborted",
  "unauthorized",
  "feed-exists",
  "candidates-ambiguous",
  "invalid-selection",
]);

/** Background query failures that never toast: a cancel, and a sign-out (the app redirects). */
const SILENT_QUERY_SLUGS = new Set(["aborted", "unauthorized"]);

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: (failureCount, error) => {
          if (ApiProblem.is(error) && error.status > 0 && error.status < 500) return false;
          return failureCount < 2;
        },
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
    queryCache: new QueryCache({
      onError: (error, query) => {
        // Only surface background failures for data we already showed once.
        if (query.state.data === undefined) return;
        if (ApiProblem.is(error) && SILENT_QUERY_SLUGS.has(error.slug)) return;
        const { title, description } = describeProblem(error);
        toast.error(title, { description, id: `query:${title}` });
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silent === true) return;
        if (ApiProblem.is(error) && SILENT_SLUGS.has(error.slug)) return;
        const { title, description } = describeProblem(error);
        toast.error(title, { description });
      },
    }),
  });
}

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: ApiProblem;
    mutationMeta: { silent?: boolean };
  }
}
