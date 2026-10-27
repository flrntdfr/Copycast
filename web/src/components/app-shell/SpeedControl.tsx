import { Minus, Plus } from "lucide-react";
import { useEffect, useRef, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  RATE_MAX,
  RATE_MIN,
  RATE_PRESETS,
  RATE_STEP,
  formatRate,
  setRate,
  stepRate,
} from "@/stores/player";
import { cn } from "@/lib/utils";

/** Trackpads send many small deltas: one speed step per this many pixels. */
const WHEEL_STEP_PX = 40;

/**
 * Scrolling over the element changes the speed by one 0.05 step (up is faster). The
 * listener is not passive, so the page does not scroll underneath.
 */
function useWheelSpeed(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let pending = 0;
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      // Lines or pages (a mouse wheel in Firefox): one step per notch.
      if (event.deltaMode !== 0) {
        stepRate(event.deltaY < 0 ? RATE_STEP : -RATE_STEP);
        return;
      }
      pending += event.deltaY;
      if (Math.abs(pending) < WHEEL_STEP_PX) return;
      stepRate(pending < 0 ? RATE_STEP : -RATE_STEP);
      pending = 0;
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [ref]);
}

function SpeedSlider({ rate }: { rate: number }) {
  const ref = useRef<HTMLInputElement | null>(null);
  useWheelSpeed(ref);
  return (
    <input
      ref={ref}
      type="range"
      aria-label="Speed"
      aria-valuetext={`${formatRate(rate)} times`}
      min={RATE_MIN}
      max={RATE_MAX}
      step={RATE_STEP}
      value={rate}
      onChange={(event) => setRate(Number(event.target.value))}
      className="h-1.5 min-w-0 flex-1 cursor-pointer accent-primary"
    />
  );
}

/**
 * The speed chip: shows the current speed, scrolls through it, and opens a popover with a
 * 0.5–3× slider between −/+ and the usual presets.
 */
export function SpeedControl({
  rate,
  keyHint,
  className,
}: {
  rate: number;
  /** Name `[` and `]` in the tooltip, while the single-key shortcuts are on. */
  keyHint: boolean;
  className?: string;
}) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  useWheelSpeed(triggerRef);
  const label = formatRate(rate);
  const preset = RATE_PRESETS.find((value) => value === rate);

  return (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              ref={triggerRef}
              variant="ghost"
              size="sm"
              aria-label={`Playback speed, ${label} times`}
              className={cn("min-w-14 px-2 tabular-nums", className)}
            >
              {label}×
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>{keyHint ? "Speed: scroll, or [ and ]" : "Speed: scroll"}</TooltipContent>
      </Tooltip>
      <PopoverContent side="top" align="end" className="w-64 p-3 motion-reduce:animate-none!">
        <div className="flex items-baseline justify-between text-sm">
          <span className="font-medium">Speed</span>
          <span className="text-muted-foreground tabular-nums">{label}×</span>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Slower"
            onClick={() => stepRate(-RATE_STEP)}
          >
            <Minus />
          </Button>
          <SpeedSlider rate={rate} />
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Faster"
            onClick={() => stepRate(RATE_STEP)}
          >
            <Plus />
          </Button>
        </div>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          spacing={1}
          aria-label="Presets"
          value={preset === undefined ? "" : String(preset)}
          onValueChange={(value) => {
            if (value) setRate(Number(value));
          }}
          className="mt-3 grid w-full grid-cols-4"
        >
          {RATE_PRESETS.map((value) => (
            <ToggleGroupItem key={value} value={String(value)} className="px-0 tabular-nums">
              {formatRate(value)}×
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </PopoverContent>
    </Popover>
  );
}
