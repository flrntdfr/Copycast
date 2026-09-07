import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { z } from "zod";

import { FileDown } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { AddSourceBar } from "@/features/mirrors/AddSourceBar";
import { MirrorsTable } from "@/features/mirrors/MirrorsTable";

const defaults = { sort: "title", order: "asc" } as const;

const searchSchema = z.object({
  sort: z.enum(["title", "created_at", "updated_at", "storage_bytes"]).default(defaults.sort),
  order: z.enum(["asc", "desc"]).default(defaults.order),
});

export const Route = createFileRoute("/mirrors/")({
  validateSearch: searchSchema,
  search: { middlewares: [stripSearchParams(defaults)] },
  component: MirrorsPage,
});

function MirrorsPage() {
  const { sort, order } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <>
      <PageHeader
        title="Mirrors"
        description="Every Source you track, each published as its own podcast feed."
        actions={
          <Button variant="outline" size="sm" asChild>
            <a href="/api/feeds.opml" download="copycast.opml">
              <FileDown /> Export OPML
            </a>
          </Button>
        }
      />
      <div className="mb-6">
        <AddSourceBar />
      </div>
      <MirrorsTable
        sort={sort}
        order={order}
        onSort={(nextSort, nextOrder) =>
          void navigate({ search: { sort: nextSort, order: nextOrder }, replace: true })
        }
      />
    </>
  );
}
