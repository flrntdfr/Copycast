import { createFileRoute, stripSearchParams, useLocation } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { z } from "zod";

import { $api } from "@/api/client";
import { SectionHeader } from "@/components/common/PageHeader";
import { InboxesSection } from "@/features/inboxes/InboxesSection";
import { AddSourceBar } from "@/features/mirrors/AddSourceBar";
import { MirrorsTable } from "@/features/mirrors/MirrorsTable";

const defaults = { sort: "title", order: "asc", url: "" } as const;

const searchSchema = z.object({
  sort: z.enum(["title", "created_at", "updated_at"]).default(defaults.sort),
  order: z.enum(["asc", "desc"]).default(defaults.order),
  // A URL to send to an Inbox (the command palette's "Send to Inbox…"): pick one below.
  url: z.string().default(defaults.url),
});

export const Route = createFileRoute("/")({
  validateSearch: searchSchema,
  search: { middlewares: [stripSearchParams(defaults)] },
  component: FeedsPage,
});

/** Every Feed on one page: the Mirrors first, the Inboxes and captured playlists below. */
function FeedsPage() {
  const { sort, order, url } = Route.useSearch();
  const hash = useLocation({ select: (location) => location.hash });
  const navigate = Route.useNavigate();
  const inboxes = useRef<HTMLElement>(null);
  const scrolledFor = useRef<string | null>(null);
  // Both lists share these queries with the table and the section; once they have rendered,
  // the Inboxes sit where they will stay, so a URL to send or `#inboxes` scrolls there once.
  const mirrors = $api.useQuery("get", "/api/feeds", {
    params: { query: { kind: "mirror", sort, order } },
  });
  const feeds = $api.useQuery("get", "/api/feeds");
  const ready = mirrors.isSuccess && feeds.isSuccess;
  const target = url || hash === "inboxes" ? `${url}#${hash}` : null;
  useEffect(() => {
    if (!target || !ready || scrolledFor.current === target) return;
    scrolledFor.current = target;
    inboxes.current?.scrollIntoView({ block: "start" });
  }, [target, ready]);

  return (
    <>
      <h1 className="sr-only">Feeds</h1>
      <section aria-labelledby="mirrors-heading" className="mb-10">
        <SectionHeader id="mirrors-heading" title="Mirrors" />
        <div className="mb-4">
          <AddSourceBar />
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
      <InboxesSection ref={inboxes} url={url} />
    </>
  );
}
