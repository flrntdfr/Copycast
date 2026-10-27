import { Info } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * A small (i) that explains a section instead of a paragraph: it opens on hover with a mouse
 * and on tap on a phone, where a tooltip never shows. The hit area is larger than the icon.
 */
export function HelpTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pointer = useRef<string>("mouse");
  const hover = (next: boolean) => (event: React.PointerEvent) => {
    if (event.pointerType === "mouse") setOpen(next);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className="-my-2.5 inline-flex size-10 items-center justify-center rounded-full align-middle text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring md:-my-1 md:size-6"
          onPointerDown={(event) => (pointer.current = event.pointerType)}
          onPointerEnter={hover(true)}
          onPointerLeave={hover(false)}
          // A mouse already opened it on hover: a click must not close it again.
          onClick={(event) => (pointer.current === "mouse" && open ? event.preventDefault() : null)}
        >
          <Info className="size-4 md:size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        className="w-72 max-w-[calc(100vw-2rem)] text-xs leading-relaxed text-pretty"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}
