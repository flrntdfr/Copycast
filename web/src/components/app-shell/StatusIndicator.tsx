import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { $api } from "@/api/client";
import { useConnectionStatus, type ConnectionStatus } from "@/api/events";
import type { ComponentStatus, StatusFact, StatusState } from "@/api/types";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { HEALTH_DOT_CLASS, type HealthTone } from "@/lib/health";
import { cn } from "@/lib/utils";

export const STATUS_POLL_MS = 30_000;

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

function worst(states: StatusState[]): StatusState {
  return states.reduce<StatusState>((a, b) => (RANK[b] > RANK[a] ? b : a), "ok");
}

/**
 * The header's status dot: the worst of every component `get_status` reports and the live
 * updates connection. A click opens one row per component; rows that need attention open.
 */
export function StatusIndicator() {
  const status = $api.useQuery("get", "/api/status", undefined, {
    refetchInterval: STATUS_POLL_MS,
    retry: false,
  });
  const connection = useConnectionStatus();
  const live: ComponentStatus = { id: "live", name: "Live updates", ...LIVE[connection] };
  const components: ComponentStatus[] = status.isError
    ? [
        {
          id: "api",
          name: "Copycast API",
          state: "error",
          summary: "Unreachable",
        },
        live,
      ]
    : [...(status.data?.components ?? []), live];
  const overall: StatusState = status.isPending
    ? "unknown"
    : worst(components.map((component) => component.state));
  const label = OVERALL[overall];

  return (
    <Popover onOpenChange={(open) => (open ? void status.refetch() : undefined)}>
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
        <div className="flex items-baseline justify-between gap-2 border-b px-4 py-3">
          <h2 className="text-sm font-semibold">{label}</h2>
          {status.data ? (
            <span className="text-xs text-muted-foreground">
              Checked <RelativeTime value={status.data.checked_at} />
            </span>
          ) : null}
        </div>
        <ul className="max-h-[min(32rem,70vh)] divide-y overflow-y-auto" aria-label="Components">
          {components.map((component) => (
            <ComponentRow key={component.id} component={component} />
          ))}
        </ul>
        <div className="border-t px-4 py-2 text-xs">
          <Link to="/about" className="text-muted-foreground hover:text-foreground">
            Versions and storage on the About page
          </Link>
        </div>
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

function ComponentRow({ component }: { component: ComponentStatus }) {
  const facts = component.facts ?? [];
  const head = (
    <>
      <Dot state={component.state} className="mt-1.5" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{component.name}</span>
        <span className="block text-xs text-muted-foreground">{component.summary}</span>
      </span>
    </>
  );
  if (facts.length === 0) {
    return (
      <li
        className="flex gap-2.5 px-4 py-2.5"
        data-testid={`status-${component.id}`}
        data-state={component.state}
      >
        {head}
      </li>
    );
  }
  return (
    <li data-testid={`status-${component.id}`} data-state={component.state}>
      <details className="group" open={component.state === "warn" || component.state === "error"}>
        <summary className="flex cursor-pointer list-none gap-2.5 px-4 py-2.5 outline-none hover:bg-accent focus-visible:bg-accent [&::-webkit-details-marker]:hidden">
          {head}
          <ChevronRight
            className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90"
            aria-hidden
          />
        </summary>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 px-4 pb-3 pl-8.5 text-xs">
          {facts.map((fact) => (
            <Fact key={fact.label} fact={fact} />
          ))}
        </dl>
      </details>
    </li>
  );
}

function Fact({ fact }: { fact: StatusFact }) {
  return (
    <>
      <dt className="flex items-center gap-1.5 text-muted-foreground">
        {fact.state ? <Dot state={fact.state} className="size-1.5" /> : null}
        {fact.label}
      </dt>
      <dd className="min-w-0">
        <span className="break-words">
          {fact.value}
          {fact.value && fact.at ? " · " : null}
          {fact.at ? <RelativeTime value={fact.at} /> : null}
        </span>
        {fact.detail ? (
          <span className="mt-0.5 line-clamp-2 block text-muted-foreground" title={fact.detail}>
            {fact.detail}
          </span>
        ) : null}
      </dd>
    </>
  );
}
