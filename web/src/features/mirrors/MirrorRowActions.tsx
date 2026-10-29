import { Copy, Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { useRequestRefresh, useSetPaused } from "@/features/feeds/mutations";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copyWithToast } from "@/lib/copy";
import type { BucketRead, FeedRead, MirrorRead } from "@/api/types";

/** The icons other than Delete only show from `md` up: a phone's row keeps just Delete. */
const WIDE_ONLY = "hidden md:inline-flex";

function IconAction({
  label,
  onClick,
  disabled,
  className,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
          className={className}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * The row's Delete button and its confirmation. With `disabledReason` the button is disabled and
 * the reason is its tooltip: `aria-disabled` rather than `disabled`, so it keeps the pointer and
 * the focus that show the tooltip.
 */
function DeleteAction({
  feed,
  label,
  disabledReason,
}: {
  feed: FeedRead;
  label: string;
  disabledReason?: string;
}) {
  const [deleting, setDeleting] = useState(false);
  if (disabledReason) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${label}`}
            aria-disabled
            className="cursor-not-allowed opacity-50 hover:bg-transparent dark:hover:bg-transparent"
          >
            <Trash2 />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{disabledReason}</TooltipContent>
      </Tooltip>
    );
  }
  return (
    <>
      <IconAction label={`Delete ${label}`} onClick={() => setDeleting(true)}>
        <Trash2 />
      </IconAction>
      <DeleteFeedDialog
        feed={feed}
        open={deleting}
        onOpenChange={setDeleting}
        navigateAway={false}
      />
    </>
  );
}

/** A Mirror's row: Copy feed URL, Refresh now and Pause (from `md` up), then Delete. */
export function MirrorRowActions({ mirror }: { mirror: MirrorRead }) {
  const refresh = useRequestRefresh();
  const { setPaused, isPending: pausing } = useSetPaused();

  return (
    <div className="flex items-center justify-end gap-0.5">
      <IconAction
        label="Copy feed URL"
        onClick={() => void copyWithToast(mirror.feed_url, "Feed URL")}
        className={WIDE_ONLY}
      >
        <Copy />
      </IconAction>
      <IconAction
        label="Refresh now"
        onClick={() => refresh.mutate({ params: { path: { feed_id: mirror.id } } })}
        disabled={refresh.isPending}
        className={WIDE_ONLY}
      >
        <RefreshCw className={refresh.isPending ? "animate-spin" : undefined} />
      </IconAction>
      <IconAction
        label={mirror.paused ? "Resume" : "Pause"}
        onClick={() => void setPaused(mirror.id, !mirror.paused)}
        disabled={pausing}
        className={WIDE_ONLY}
      >
        {mirror.paused ? <Play /> : <Pause />}
      </IconAction>
      <DeleteAction feed={mirror} label={mirror.title} />
    </div>
  );
}

/**
 * A Local feed's row: Copy feed URL (from `md` up), then Delete. The default Local feed can never
 * be deleted, so its Delete stays disabled and says why.
 */
export function LocalFeedRowActions({ feed }: { feed: BucketRead }) {
  return (
    <div className="flex items-center justify-end gap-0.5">
      <IconAction
        label="Copy feed URL"
        onClick={() => void copyWithToast(feed.feed_url, "Feed URL")}
        className={WIDE_ONLY}
      >
        <Copy />
      </IconAction>
      <DeleteAction
        feed={feed}
        label={feed.name}
        disabledReason={feed.is_default ? "The default Local feed cannot be deleted" : undefined}
      />
    </div>
  );
}
