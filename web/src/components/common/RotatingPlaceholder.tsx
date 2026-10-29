import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/** How long each hint stays before the next one slides in. */
export const ROTATING_PLACEHOLDER_INTERVAL_MS = 2_800;

/**
 * The index of the hint to show, advancing every `intervalMs` while `active`.
 *
 * Browsers cannot animate an input's `placeholder` attribute, so callers render the hint
 * themselves (see `RotatingPlaceholder`). The timer stops while the tab is hidden, while
 * `active` is false and when the component unmounts, and resumes where it stopped.
 */
export function useRotatingPlaceholder(
  count: number,
  { active = true, intervalMs = ROTATING_PLACEHOLDER_INTERVAL_MS } = {},
): number {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!active || count < 2) return undefined;
    let timer: ReturnType<typeof setInterval> | null = null;
    const stop = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };
    const start = () => {
      stop();
      timer = setInterval(() => setIndex((current) => (current + 1) % count), intervalMs);
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active, count, intervalMs]);

  return count > 0 ? index % count : 0;
}

/**
 * A placeholder that cycles through `hints`, drawn over an empty input.
 *
 * Render it inside a `relative` wrapper next to an `Input` whose own `placeholder` is empty;
 * it is decorative (`aria-hidden`, `pointer-events-none`), so keep the input labelled and
 * give it a static `aria-describedby` hint for assistive tech. Each change slides the new
 * hint in (`animate-placeholder-in`); under reduced motion it is a plain swap.
 */
export function RotatingPlaceholder({
  hints,
  active = true,
  intervalMs = ROTATING_PLACEHOLDER_INTERVAL_MS,
  className,
}: {
  hints: readonly string[];
  /** False while the input holds text: the hint is not rendered and the timer stops. */
  active?: boolean;
  intervalMs?: number;
  className?: string;
}) {
  const index = useRotatingPlaceholder(hints.length, { active, intervalMs });
  if (!active || hints.length === 0) return null;
  return (
    <span
      aria-hidden
      data-testid="rotating-placeholder"
      className={cn(
        "pointer-events-none absolute inset-y-0 right-3 left-3 flex items-center overflow-hidden text-base text-muted-foreground md:text-sm",
        className,
      )}
    >
      {/* Keyed on the index so the slide-in keyframes replay for every new hint. */}
      <span key={index} className="animate-placeholder-in truncate motion-reduce:animate-none">
        {hints[index]}
      </span>
    </span>
  );
}
