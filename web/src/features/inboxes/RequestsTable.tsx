import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, Send } from "lucide-react";
import { Fragment, useState } from "react";

import { $api } from "@/api/client";
import type { RequestRead } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SkeletonRows } from "@/components/common/SkeletonRows";
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
import { StateBadge } from "@/features/catalog/StateBadge";
import { JobProgress } from "@/features/jobs/JobProgress";
import { count, requestStatusLabel, requestedViaLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

export const REQUESTS_PAGE_SIZE = 100;

/**
 * Requests of one Inbox: URL, requested_via, status, item_count and the produced Episodes.
 * `request` events invalidate `list_requests`, so the table is live without polling.
 */
export function RequestsTable({ inboxId }: { inboxId: string }) {
  const requests = $api.useQuery("get", "/api/inboxes/{inbox_id}/requests", {
    params: { path: { inbox_id: inboxId }, query: { limit: REQUESTS_PAGE_SIZE } },
  });
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const rows = requests.data?.requests ?? [];

  if (requests.isSuccess && rows.length === 0) {
    return (
      <EmptyState
        icon={<Send />}
        title="No Requests yet"
        description="Paste a video, playlist or page URL above; every Episode it yields lands in this Inbox."
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">
              <span className="sr-only">Details</span>
            </TableHead>
            <TableHead>URL</TableHead>
            <TableHead className="hidden sm:table-cell">Via</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Episodes</TableHead>
            <TableHead className="hidden md:table-cell">Added</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {requests.isPending ? <SkeletonRows columns={6} rows={3} /> : null}
          {requests.isError ? (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-destructive">
                Requests could not be loaded.
              </TableCell>
            </TableRow>
          ) : null}
          {rows.map((request) => {
            const open = expanded[request.id] ?? false;
            const items = request.items ?? [];
            const producible = items.length > 0;
            return (
              <Fragment key={request.id}>
                <TableRow data-testid="request-row" data-request-status={request.status}>
                  <TableCell>
                    {producible ? (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-expanded={open}
                        aria-label={open ? "Hide produced Episodes" : "Show produced Episodes"}
                        onClick={() =>
                          setExpanded((current) => ({ ...current, [request.id]: !open }))
                        }
                      >
                        {open ? <ChevronDown /> : <ChevronRight />}
                      </Button>
                    ) : null}
                  </TableCell>
                  <TableCell className="max-w-[16rem] md:max-w-md">
                    <a
                      href={request.url}
                      target="_blank"
                      rel="noreferrer"
                      className="block truncate font-mono text-xs hover:underline"
                      title={request.url}
                    >
                      {request.url}
                    </a>
                    <span className="text-xs text-muted-foreground sm:hidden">
                      {requestedViaLabel(request.requested_via)}
                    </span>
                  </TableCell>
                  <TableCell className="hidden sm:table-cell">
                    <Badge variant="outline">{requestedViaLabel(request.requested_via)}</Badge>
                  </TableCell>
                  <TableCell>
                    <RequestStatus request={request} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{request.item_count}</TableCell>
                  <TableCell className="hidden whitespace-nowrap md:table-cell">
                    <RelativeTime value={request.created_at} />
                  </TableCell>
                </TableRow>
                {open ? (
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={6} className="py-2">
                      <ul className="divide-y text-sm" aria-label="Produced Episodes">
                        {items.map((item) => (
                          <li key={item.id} className="flex items-center gap-3 py-1.5">
                            <Link
                              to="/inboxes/$inboxId"
                              params={{ inboxId }}
                              search={{ tab: "episodes", q: item.title }}
                              className={cn("min-w-0 flex-1 truncate hover:underline")}
                              title={item.title}
                            >
                              {item.title || <span className="italic">Untitled</span>}
                            </Link>
                            <StateBadge item={item} />
                          </li>
                        ))}
                      </ul>
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function RequestStatus({ request }: { request: RequestRead }) {
  if (request.job && (request.job.status === "running" || request.job.status === "queued")) {
    return <JobProgress job={request.job} compact />;
  }
  return (
    <span className="flex flex-col">
      <span
        className={cn(
          request.status === "failed" && "text-destructive",
          request.status === "expanded" && "text-foreground",
        )}
      >
        {requestStatusLabel(request.status)}
        {request.status === "expanded" ? ` · ${count(request.item_count, "Episode")}` : ""}
      </span>
      {request.error ? (
        <span className="max-w-xs truncate text-xs text-destructive" title={request.error}>
          {request.error}
        </span>
      ) : null}
    </span>
  );
}
