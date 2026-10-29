import { createFileRoute, stripSearchParams, useLocation } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { z } from "zod";

import { $api } from "@/api/client";
import { BucketsSection } from "@/features/buckets/BucketsSection";
import { CapturePlaylistMenu } from "@/features/buckets/CapturePlaylistMenu";
import { AddSourceBar } from "@/features/mirrors/AddSourceBar";
import { MirrorsTable } from "@/features/mirrors/MirrorsTable";

const defaults = { sort: "title", order: "asc", url: "" } as const;

const searchSchema = z.object({
  sort: z.enum(["title", "created_at", "updated_at"]).default(defaults.sort),
  order: z.enum(["asc", "desc"]).default(defaults.order),
  // A URL to send to a Local feed (the command palette's "Send to Local feed…"): pick one below.
  url: z.string().default(defaults.url),
});

export const Route = createFileRoute("/")({
  validateSearch: searchSchema,
  search: { middlewares: [stripSearchParams(defaults)] },
  component: FeedsPage,
});

/**
 * Every Feed on one page: the bar to mirror a Source or capture a YouTube playlist, the table of
 * every Feed (Mirrors, captured playlists, Local feeds), and their buckets in a box below.
 */
function FeedsPage() {
  const { sort, order, url } = Route.useSearch();
  const hash = useLocation({ select: (location) => location.hash });
  const navigate = Route.useNavigate();
  const buckets = useRef<HTMLElement>(null);
  const scrolledFor = useRef<string | null>(null);
  // The table and the Buckets box share this query; once it has rendered, the box sits where it
  // will stay, so a URL to send or `#buckets` scrolls there once.
  const feeds = $api.useQuery("get", "/api/feeds", { params: { query: { sort, order } } });
  const ready = feeds.isSuccess;
  const target = url || hash === "buckets" ? `${url}#${hash}` : null;
  useEffect(() => {
    if (!target || !ready || scrolledFor.current === target) return;
    scrolledFor.current = target;
    buckets.current?.scrollIntoView({ block: "start" });
  }, [target, ready]);

  return (
    <>
      <section aria-labelledby="feeds-heading" className="mb-10">
        <h1 id="feeds-heading" className="sr-only">
          Feeds
        </h1>
        <div className="mb-4">
          <AddSourceBar>
            <CapturePlaylistMenu />
          </AddSourceBar>
        </div>
        <MirrorsTable
          sort={sort}
          order={order}
          onSort={(nextSort, nextOrder) =>
            void navigate({
              search: (prev) => ({ ...prev, sort: nextSort, order: nextOrder }),
              replace: true,
            })
          }
        />
      </section>
      <BucketsSection ref={buckets} sort={sort} order={order} url={url} />
    </>
  );
}
