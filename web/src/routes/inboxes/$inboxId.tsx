import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { z } from "zod";

import { $api, ApiProblem, asError } from "@/api/client";
import { isInbox } from "@/api/types";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CatalogTable } from "@/features/catalog/CatalogTable";
import { catalogSearchDefaults, catalogSearchSchema } from "@/features/catalog/search";
import { InboxHeader } from "@/features/inboxes/InboxHeader";
import { InboxSettings } from "@/features/inboxes/InboxSettings";
import { RequestsTable } from "@/features/inboxes/RequestsTable";

const INBOX_TABS = ["episodes", "requests", "settings"] as const;
type InboxTab = (typeof INBOX_TABS)[number];

const defaults = { tab: "episodes", url: "", ...catalogSearchDefaults } as const;

export const Route = createFileRoute("/inboxes/$inboxId")({
  validateSearch: catalogSearchSchema.extend({
    tab: z.enum(INBOX_TABS).default(defaults.tab),
    url: z.string().default(defaults.url),
  }),
  search: { middlewares: [stripSearchParams(defaults)] },
  component: InboxPage,
});

function InboxPage() {
  const { inboxId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const feed = $api.useQuery("get", "/api/feeds/{feed_id}", {
    params: { path: { feed_id: inboxId } },
  });

  if (feed.isPending) return <PageSpinner />;
  if (feed.isError) {
    if (ApiProblem.is(feed.error) && feed.error.status === 404) return <NotFound />;
    throw asError(feed.error);
  }
  if (!isInbox(feed.data)) return <NotFound />;
  const inbox = feed.data;

  return (
    <>
      <InboxHeader inbox={inbox} initialUrl={search.url} />
      <Tabs
        value={search.tab}
        onValueChange={(tab) =>
          void navigate({ search: (prev) => ({ ...prev, tab: tab as InboxTab }) })
        }
      >
        <TabsList aria-label="Inbox sections">
          <TabsTrigger value="episodes">Episodes</TabsTrigger>
          <TabsTrigger value="requests">Requests</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="episodes" className="mt-4">
          <CatalogTable
            feed={inbox}
            search={search}
            onSearchChange={(patch) =>
              void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true })
            }
          />
        </TabsContent>
        <TabsContent value="requests" className="mt-4">
          <RequestsTable inboxId={inbox.id} />
        </TabsContent>
        <TabsContent value="settings" className="mt-4">
          <InboxSettings inbox={inbox} />
        </TabsContent>
      </Tabs>
    </>
  );
}
