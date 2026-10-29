import { Link } from "@tanstack/react-router";
import { CheckCircle2 } from "lucide-react";

import type { MirrorRead } from "@/api/types";
import { FeedUrlField } from "@/components/common/FeedUrlField";
import { Button } from "@/components/ui/button";
import { openAddSource } from "@/stores/addSource";

/** The last step: the new Mirror's feed URL and where to go next. */
export function CreatedStep({ mirror, onOpen }: { mirror: MirrorRead; onOpen?: () => void }) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm" role="status">
        <CheckCircle2 className="size-5 text-success" aria-hidden />
        <span>
          <span className="font-medium">{mirror.title}</span> is now a Mirror.
        </span>
      </div>
      <FeedUrlField url={mirror.feed_url} label="Mirror Feed URL" />
      <div className="flex gap-2">
        <Button asChild>
          <Link to="/feed/$feedId" params={{ feedId: mirror.id }} onClick={onOpen}>
            Open feed
          </Link>
        </Button>
        <Button variant="outline" onClick={() => openAddSource({})}>
          Add another
        </Button>
      </div>
    </div>
  );
}
