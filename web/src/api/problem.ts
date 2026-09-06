/** RFC 9457 problem details as the API emits them (`urn:copycast:problem:<slug>` types). */
import type { Problem } from "./types";

export const PROBLEM_TYPE_PREFIX = "urn:copycast:problem:";

export type ProblemSlug =
  | "not-found"
  | "validation"
  | "feed-exists"
  | "candidates-ambiguous"
  | "source-unsupported"
  | "source-kind-change"
  | "invalid-selection"
  | "engine-option-rejected"
  | "conflict"
  | "engine-unavailable"
  | "internal"
  | "method-not-allowed"
  | "http-error"
  | "network"
  | "aborted"
  | (string & {});

export interface ValidationIssue {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
}

/** Extension members the API adds per slug. */
export interface ProblemExtensions {
  existing_feed_id?: string;
  candidates?: unknown[];
  unresolved?: string[];
  keys?: string[];
  scope?: string;
  errors?: ValidationIssue[];
}

export class ApiProblem extends Error {
  readonly slug: ProblemSlug;
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;
  readonly type: string;
  readonly instance: string | null;
  readonly extensions: ProblemExtensions;
  readonly method: string;
  readonly url: string;

  constructor(init: {
    slug: ProblemSlug;
    status: number;
    title: string;
    detail?: string | null;
    type?: string;
    instance?: string | null;
    extensions?: ProblemExtensions;
    method?: string;
    url?: string;
    cause?: unknown;
  }) {
    super(init.detail || init.title, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiProblem";
    this.slug = init.slug;
    this.status = init.status;
    this.title = init.title;
    this.detail = init.detail ?? null;
    this.type = init.type ?? `${PROBLEM_TYPE_PREFIX}${init.slug}`;
    this.instance = init.instance ?? null;
    this.extensions = init.extensions ?? {};
    this.method = init.method ?? "";
    this.url = init.url ?? "";
  }

  get existingFeedId(): string | null {
    return this.extensions.existing_feed_id ?? null;
  }

  /** Per-key wording for `engine-option-rejected`, keyed by option name. */
  get rejectedKeys(): string[] {
    return this.extensions.keys ?? [];
  }

  static is(value: unknown): value is ApiProblem {
    return value instanceof ApiProblem;
  }

  static slugOf(value: unknown): ProblemSlug | null {
    return ApiProblem.is(value) ? value.slug : null;
  }
}

export function slugFromType(type: string | undefined | null): ProblemSlug {
  if (!type) return "http-error";
  return type.startsWith(PROBLEM_TYPE_PREFIX) ? type.slice(PROBLEM_TYPE_PREFIX.length) : type;
}

const EXTENSION_KEYS: (keyof ProblemExtensions)[] = [
  "existing_feed_id",
  "candidates",
  "unresolved",
  "keys",
  "scope",
  "errors",
];

/** Build an ApiProblem from a response body (problem+json, FastAPI validation, or anything). */
export function problemFromBody(
  body: unknown,
  response: { status: number; statusText: string },
  request: { method: string; url: string },
): ApiProblem {
  const base = { status: response.status, method: request.method, url: request.url };
  if (body && typeof body === "object") {
    const raw = body as Partial<Problem> & Record<string, unknown>;
    if (typeof raw.type === "string" && typeof raw.title === "string") {
      const extensions: ProblemExtensions = {};
      for (const key of EXTENSION_KEYS) {
        if (key in raw) (extensions as Record<string, unknown>)[key] = raw[key];
      }
      return new ApiProblem({
        ...base,
        slug: slugFromType(raw.type),
        status: typeof raw.status === "number" ? raw.status : response.status,
        title: raw.title,
        detail: typeof raw.detail === "string" ? raw.detail : null,
        type: raw.type,
        instance: typeof raw.instance === "string" ? raw.instance : null,
        extensions,
      });
    }
    if (Array.isArray(raw.detail)) {
      return new ApiProblem({
        ...base,
        slug: "validation",
        title: "Validation failed",
        detail: (raw.detail as unknown[])
          .map((issue) => {
            if (!issue || typeof issue !== "object") return "";
            const { msg } = issue as { msg?: unknown };
            return typeof msg === "string" ? msg : "";
          })
          .filter(Boolean)
          .join("; "),
        extensions: { errors: raw.detail as ValidationIssue[] },
      });
    }
    if (typeof raw.detail === "string") {
      return new ApiProblem({ ...base, slug: "http-error", title: raw.detail, detail: raw.detail });
    }
  }
  const text = typeof body === "string" && body.trim() ? body.trim().slice(0, 200) : null;
  return new ApiProblem({
    ...base,
    slug:
      response.status === 404 ? "not-found" : response.status >= 500 ? "internal" : "http-error",
    title: response.statusText || `HTTP ${response.status}`,
    detail: text,
  });
}

/** Anything a query rejected with, as an Error for `throw` in a route component. */
export function asError(error: unknown): Error {
  if (error instanceof Error) return error;
  return new ApiProblem({
    slug: "internal",
    status: 0,
    title: "Something went wrong",
    detail: String(error),
  });
}

/** One-line wording for a toast, with the per-key list for rejected engine options. */
export function describeProblem(error: unknown): { title: string; description?: string } {
  if (ApiProblem.is(error)) {
    if (error.slug === "engine-option-rejected") {
      const keys = error.rejectedKeys;
      return {
        title: "Engine options rejected",
        description: keys.length
          ? keys
              .map(
                (key) =>
                  `${key}: not allowed${error.extensions.scope ? ` for a ${error.extensions.scope}` : ""}`,
              )
              .join("\n")
          : (error.detail ?? undefined),
      };
    }
    if (error.slug === "validation" && error.extensions.errors?.length) {
      return {
        title: error.title,
        description: error.extensions.errors
          .map((issue) =>
            `${(issue.loc ?? []).filter((p) => p !== "body").join(".")}: ${issue.msg ?? ""}`.replace(
              /^: /,
              "",
            ),
          )
          .join("\n"),
      };
    }
    if (error.slug === "network")
      return { title: "Cannot reach Copycast", description: error.detail ?? undefined };
    return { title: error.title, description: error.detail ?? undefined };
  }
  if (error instanceof Error) return { title: error.message };
  return { title: "Something went wrong" };
}
