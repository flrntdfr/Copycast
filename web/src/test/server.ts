/**
 * The MSW server shared by every test; tests add handlers with `server.use(...)`. The
 * header's status dot polls `/api/status` on every page, so a healthy report is the default.
 */
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { systemStatus } from "./factories";

export const server = setupServer(http.get("/api/status", () => HttpResponse.json(systemStatus())));
