import { useState } from "react";

import { $api, ApiProblem } from "@/api/client";
import { useConnectionStatus, type ConnectionStatus } from "@/api/events";
import type { ComponentStatus, StatusFact, StatusMeter, StatusState } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import { formatSize, parseDate } from "@/lib/format";
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

/** The bar's fill for a state: amber from 90 %, red from 100 % (the api decides the state). */
const BAR_CLASS: Record<StatusState, string> = {
  ok: "*:bg-primary",
  warn: "*:bg-warning",
  error: "*:bg-destructive",
  unknown: "*:bg-muted-foreground/50",
};

/** A meter in words: "3.2 GB of 10 GB" and its percentage, or "3.2 GB used (unlimited)". */
export function describeMeter(meter: StatusMeter): { text: string; percent: number | null } {
  const used = formatSize(meter.used_bytes) || "0 B";
  if (meter.quota_bytes == null) return { text: `${used} used (unlimited)`, percent: null };
  const ratio = meter.ratio ?? (meter.quota_bytes > 0 ? meter.used_bytes / meter.quota_bytes : 1);
  return {
    text: `${used} of ${formatSize(meter.quota_bytes) || "0 B"}`,
    percent: Math.round(ratio * 100),
  };
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
  // A 401 is a session that ended, not an outage: the app is on its way to the sign-in page.
  const failure: unknown = status.error;
  const signedOut = ApiProblem.is(failure) && failure.status === 401;
  const components: ComponentStatus[] = signedOut
    ? [{ id: "api", name: "Copycast API", state: "unknown", summary: "Signed out" }, live]
    : status.isError
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

/** A usage bar under a line, with its words: "3.2 GB of 10 GB · 32 %". */
function Meter({ meter, state, label }: { meter: StatusMeter; state: StatusState; label: string }) {
  const { text, percent } = describeMeter(meter);
  return (
    <>
      {percent !== null ? (
        <Progress
          value={Math.min(100, Math.max(0, percent))}
          aria-label={label}
          aria-valuetext={`${text} (${percent} %)`}
          className={cn("mt-1.5 h-1.5", BAR_CLASS[state])}
          data-testid="status-meter"
        />
      ) : null}
      <span className="mt-1 block text-xs text-muted-foreground tabular-nums">
        {percent !== null ? `${text} · ${percent} %` : text}
      </span>
    </>
  );
}

/** What the list under a component holds. */
const LINES_LABEL: Partial<Record<string, string>> = {
  crawlers: "Crawlers",
  quota: "Storage by User",
};

/** A line under a component that has a state or a meter of its own: a crawler, a User. */
function hasOwnLine(fact: StatusFact): boolean {
  return fact.state != null || fact.meter != null;
}

/**
 * One component. A meter (the Storage row) shows as a bar with its words; the lines that carry
 * a state or a meter of their own (each crawler, each User's storage for an Admin) are listed
 * under it.
 */
function ComponentRow({ component }: { component: ComponentStatus }) {
  const lines = (component.facts ?? []).filter(hasOwnLine);
  const crawlers = component.id === "crawlers";
  return (
    <li className="px-4 py-2.5" data-testid={`status-${component.id}`} data-state={component.state}>
      <div className="flex gap-2.5">
        <Dot state={component.state} className="mt-1.5" />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{component.name}</span>
          {component.meter ? (
            <Meter meter={component.meter} state={component.state} label={component.name} />
          ) : (
            <span className="block text-xs text-muted-foreground">{component.summary}</span>
          )}
        </span>
      </div>
      {lines.length > 0 ? (
        <ul
          className="mt-2 space-y-1.5 pl-4.5"
          aria-label={LINES_LABEL[component.id] ?? component.name}
        >
          {lines.map((line) => (
            <li
              key={line.label}
              className="flex gap-2.5"
              data-testid={crawlers ? `crawler-${line.label}` : `${component.id}-${line.label}`}
              data-state={line.state ?? "unknown"}
              title={line.detail ?? undefined}
            >
              <Dot state={line.state ?? "unknown"} className="mt-1.5" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm">{line.label}</span>
                {line.meter ? (
                  <Meter meter={line.meter} state={line.state ?? "unknown"} label={line.label} />
                ) : (
                  <span className="block text-xs text-muted-foreground">{line.value}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
