import { createFileRoute } from "@tanstack/react-router";

import { ApiKeysPage } from "@/features/keys/ApiKeysPage";

// The MCP page keeps the `/keys` path: `/mcp` is the MCP endpoint itself (spa.py reserves it).
export const Route = createFileRoute("/keys")({ component: ApiKeysPage });
