import { useState } from "react";

import { $api } from "@/api/client";
import { useConnectionStatus, type ConnectionStatus } from "@/api/events";
import type { ComponentStatus, StatusState } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { parseDate } from "@/lib/format";
import { HEALTH_DOT_CLASS, type HealthTone } from "@/lib/health";
import { cn } from "@/lib/utils";

export const STATUS_POLL_MS = 30_000;
/** While the popover is open the report is fetched every second. */
export const STATUS_OPEN_POLL_MS = 1_000;

const TONE: Record<StatusState, HealthTone> = {
  ok: "success",
  warn: "warning",
  error: "destructive",
  unknown: "muted",
};

const OVERALL: Record<StatusState, string> = {
  ok: "All systems normal",
  warn: "Something needs attention",
  error: "Something is broken",
  unknown: "Status unknown",
};

const LIVE: Record<ConnectionStatus, { state: StatusState; summary: string }> = {
  open: { state: "ok", summary: "Connected" },
  connecting: { state: "unknown", summary: "Connecting" },
  reconnecting: { state: "warn", summary: "Interrupted; polling every 10 s" },
};

const RANK: Record<StatusState, number> = { ok: 0, unknown: 0, warn: 1, error: 2 };

const checkedFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

function worst(states: StatusState[]): StatusState {
  return states.reduce<StatusState>((a, b) => (RANK[b] > RANK[a] ? b : a), "ok");
}

/**
 * The header's status dot: the worst of every component `get_status` reports and the live
 * updates connection. A click lists them, the crawlers one per service, refreshed every second.
 */
export function StatusIndicator() {
  const [open, setOpen] = useState(false);
  const status = $api.useQuery("get", "/api/status", undefined, {
    refetchInterval: open ? STATUS_OPEN_POLL_MS : STATUS_POLL_MS,
    retry: false,
  });
  const connection = useConnectionStatus();
  const live: ComponentStatus = { id: "live", name: "Live updates", ...LIVE[connection] };
  const components: ComponentStatus[] = status.isError
    ? [{ id: "api", name: "Copycast API", state: "error", summary: "Unreachable" }, live]
    : [...(status.data?.components ?? []), live];
  const overall: StatusState = status.isPending
    ? "unknown"
    : worst(components.map((component) => component.state));
  const label = OVERALL[overall];
  const checked = parseDate(status.data?.checked_at);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`System status: ${label}`}
          data-status={overall}
          title={label}
        >
          <span
            className={cn("size-2.5 rounded-full", HEALTH_DOT_CLASS[TONE[overall]])}
            aria-hidden
          />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-2rem))] p-0">
        <h2 className="border-b px-4 py-3 text-sm font-semibold tabular-nums">
          {checked ? (
            <time dateTime={status.data?.checked_at}>{checkedFormatter.format(checked)}</time>
          ) : (
            label
          )}
        </h2>
        <ul className="max-h-[min(32rem,70vh)] divide-y overflow-y-auto" aria-label="Components">
          {components.map((component) => (
            <ComponentRow key={component.id} component={component} />
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

function Dot({ state, className }: { state: StatusState; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        HEALTH_DOT_CLASS[TONE[state]],
        className,
      )}
      aria-hidden
    />
  );
}

/** One component; the crawlers list each service under it. */
function ComponentRow({ component }: { component: ComponentStatus }) {
  const crawlers = component.id === "crawlers" ? (component.facts ?? []) : [];
  return (
    <li className="px-4 py-2.5" data-testid={`status-${component.id}`} data-state={component.state}>
      <div className="flex gap-2.5">
        <Dot state={component.state} className="mt-1.5" />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{component.name}</span>
          <span className="block text-xs text-muted-foreground">{component.summary}</span>
        </span>
      </div>
      {crawlers.length > 0 ? (
        <ul className="mt-2 space-y-1.5 pl-4.5" aria-label="Crawlers">
          {crawlers.map((crawler) => (
            <li
              key={crawler.label}
              className="flex gap-2.5"
              data-testid={`crawler-${crawler.label}`}
              title={crawler.detail ?? undefined}
            >
              <Dot state={crawler.state ?? "unknown"} className="mt-1.5" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm">{crawler.label}</span>
                <span className="block text-xs text-muted-foreground">{crawler.value}</span>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
