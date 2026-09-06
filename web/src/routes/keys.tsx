import { createFileRoute } from "@tanstack/react-router";

import { ApiKeysPage } from "@/features/keys/ApiKeysPage";

export const Route = createFileRoute("/keys")({ component: ApiKeysPage });
