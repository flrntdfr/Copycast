/**
 * The MSW server shared by every test; tests add handlers with `server.use(...)`. The
 * header's status dot polls `/api/status` on every page, so a healthy report is the default;
 * the Feeds page's Buckets box asks for the storage summary, empty by default.
 */
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import type { StorageSummary } from "@/api/types";
import { systemStatus } from "./factories";

const emptyStorage: StorageSummary = { feeds: [], file_count: 0, total_bytes: 0 };

export const server = setupServer(
  http.get("/api/status", () => HttpResponse.json(systemStatus())),
  http.get("/api/storage", () => HttpResponse.json(emptyStorage)),
);
