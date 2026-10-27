import { ChevronsUpDown } from "lucide-react";
import { useRef, useState } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDuration } from "@/lib/format";
import { seek, type Chapter } from "@/stores/player";
import { cn } from "@/lib/utils";

/** The current chapter's title; opens the list of chapters to jump to one. */
export function ChapterMenu({
  chapters,
  current,
  className,
}: {
  chapters: Chapter[];
  current: Chapter | null;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const currentRef = useRef<HTMLButtonElement | null>(null);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="player-chapter"
          className={cn(
            "relative inline-flex min-w-0 items-center gap-0.5 rounded-sm outline-none after:absolute after:-inset-1 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50",
            className,
          )}
        >
          <span className="sr-only">Chapter: </span>
          <span className="truncate">{current?.title || "Chapters"}</span>
          <ChevronsUpDown className="size-3 shrink-0 opacity-60" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        className="w-72 p-1 motion-reduce:animate-none!"
        onOpenAutoFocus={(event) => {
          // Land on the chapter playing now, scrolled into view.
          if (!currentRef.current) return;
          event.preventDefault();
          currentRef.current.focus();
        }}
      >
        <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">Chapters</div>
        <ol className="max-h-72 overflow-y-auto" aria-label="Chapters">
          {chapters.map((chapter) => {
            const isCurrent = chapter === current;
            return (
              <li key={`${chapter.startTime}-${chapter.title}`}>
                <button
                  type="button"
                  ref={isCurrent ? currentRef : undefined}
                  aria-current={isCurrent ? "true" : undefined}
                  onClick={() => {
                    seek(chapter.startTime);
                    setOpen(false);
                  }}
                  className="flex w-full items-baseline gap-3 rounded-sm px-2 py-1.5 text-left text-sm outline-none hover:bg-accent focus-visible:bg-accent aria-[current=true]:font-medium aria-[current=true]:text-primary"
                >
                  <span className="w-14 shrink-0 text-xs text-muted-foreground tabular-nums">
                    {formatDuration(chapter.startTime)}
                  </span>
                  <span className="min-w-0 flex-1">{chapter.title || "Untitled"}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </PopoverContent>
    </Popover>
  );
}
