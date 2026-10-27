import { Moon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { SLEEP_MINUTES, setSleepTimer, type SleepChoice, type SleepTimer } from "@/stores/player";
import { cn } from "@/lib/utils";

/** "12m" on the chip; seconds in the last minute. */
function shortLeft(leftMs: number): string {
  return leftMs >= 60_000 ? `${Math.ceil(leftMs / 60_000)}m` : `${Math.ceil(leftMs / 1000)}s`;
}

function describe(sleep: SleepTimer | null): string {
  if (!sleep) return "Sleep timer";
  if (sleep.kind === "chapter") return "Sleep timer, end of chapter";
  if (sleep.kind === "episode") return "Sleep timer, end of Episode";
  const minutes = Math.ceil(sleep.leftMs / 60_000);
  return `Sleep timer, ${minutes} minute${minutes === 1 ? "" : "s"} left`;
}

function choiceOf(sleep: SleepTimer | null): SleepChoice {
  if (!sleep) return null;
  return sleep.kind === "minutes" ? (sleep.minutes as SleepChoice) : sleep.kind;
}

/** The moon: pause after some minutes, at the end of the chapter or of the Episode. */
export function SleepTimerControl({
  sleep,
  hasChapters,
  className,
}: {
  sleep: SleepTimer | null;
  hasChapters: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const current = choiceOf(sleep);
  const options: { choice: SleepChoice; label: string; wide?: boolean }[] = [
    { choice: null, label: "Off" },
    ...SLEEP_MINUTES.map((minutes) => ({ choice: minutes, label: `${minutes} min` })),
    ...(hasChapters ? [{ choice: "chapter" as const, label: "End of chapter", wide: true }] : []),
    { choice: "episode", label: "End of Episode", wide: true },
  ];
  const choose = (choice: SleepChoice) => {
    setSleepTimer(choice);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-label={describe(sleep)}
              className={cn(
                "min-w-10 px-2 tabular-nums sm:min-w-9",
                sleep && "text-primary",
                className,
              )}
            >
              <Moon className={cn(sleep && "fill-current")} />
              {sleep?.kind === "minutes" ? <span>{shortLeft(sleep.leftMs)}</span> : null}
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Sleep timer</TooltipContent>
      </Tooltip>
      <PopoverContent side="top" align="start" className="w-56 p-3 motion-reduce:animate-none!">
        <div className="text-sm font-medium">Sleep timer</div>
        <div className="mt-2 grid grid-cols-3 gap-1" role="group" aria-label="Pause playback">
          {options.map(({ choice, label, wide }) => (
            <Button
              key={label}
              variant={current === choice ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={current === choice}
              onClick={() => choose(choice)}
              className={cn(
                "justify-center",
                wide && "col-span-3",
                current === choice && "font-semibold",
              )}
            >
              {label}
            </Button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
