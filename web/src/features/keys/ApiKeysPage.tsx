import { AlertTriangle, KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";

import { $api, asError } from "@/api/client";
import type { ApiKeyRead } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/common/PageHeader";
import { PageSpinner } from "@/components/common/PageSpinner";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { NewApiKeyDialog } from "./NewApiKeyDialog";
import { useRevokeApiKey } from "./mutations";
import { keyScopeHint, keyScopeLabel } from "@/lib/labels";

/** The MCP API keys: name, scope, prefix, created, last used; mint and revoke. */
export function ApiKeysPage() {
  const about = $api.useQuery("get", "/api/about");
  const keys = $api.useQuery("get", "/api/keys");
  const revoke = useRevokeApiKey();
  const [revoking, setRevoking] = useState<ApiKeyRead | null>(null);

  if (about.isPending) return <PageSpinner />;
  if (about.isError) throw asError(about.error);
  const mcpUrl = `${about.data.base_url}/mcp`;
  const rows = keys.data?.keys ?? [];

  return (
    <>
      <PageHeader
        title="API keys"
        description="Bearer tokens for MCP clients such as Claude Code; each key's scope caps what the agent may do."
        actions={<NewApiKeyDialog mcpUrl={mcpUrl} />}
      />
      {!about.data.auth_enabled ? (
        <Alert className="mb-6">
          <AlertTriangle />
          <AlertTitle>Authentication is off</AlertTitle>
          <AlertDescription>
            Keys are only checked once an operator password is set (COPYCAST__AUTH__PASSWORD). Until
            then the MCP endpoint at {mcpUrl} accepts every client on the network.
          </AlertDescription>
        </Alert>
      ) : null}
      {keys.isSuccess && rows.length === 0 ? (
        <EmptyState
          icon={<KeyRound />}
          title="No API keys yet"
          description="Create one per agent or machine so you can revoke each on its own."
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead className="hidden sm:table-cell">Key</TableHead>
                <TableHead className="hidden md:table-cell">Created</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead className="w-12">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {keys.isPending ? <SkeletonRows columns={6} rows={2} /> : null}
              {keys.isError ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-destructive">
                    The keys could not be loaded.
                  </TableCell>
                </TableRow>
              ) : null}
              {rows.map((key) => (
                <TableRow key={key.id} data-testid="key-row">
                  <TableCell className="max-w-xs truncate font-medium">{key.name}</TableCell>
                  <TableCell>
                    <Badge variant="outline" title={keyScopeHint[key.scope]}>
                      {keyScopeLabel(key.scope)}
                    </Badge>
                  </TableCell>
                  <TableCell className="hidden font-mono text-xs sm:table-cell">
                    {key.prefix}…
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <RelativeTime value={key.created_at} />
                  </TableCell>
                  <TableCell>
                    <RelativeTime value={key.last_used_at} fallback="never" />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Revoke ${key.name}`}
                      onClick={() => setRevoking(key)}
                    >
                      <Trash2 />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <AlertDialog open={revoking !== null} onOpenChange={(open) => !open && setRevoking(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke “{revoking?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              The next MCP call with this key is refused. Clients configured with it must be given a
              new key.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revoke.isPending}>Keep it</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={revoke.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (!revoking) return;
                revoke.mutate(
                  { params: { path: { key_id: revoking.id } } },
                  { onSuccess: () => setRevoking(null) },
                );
              }}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
