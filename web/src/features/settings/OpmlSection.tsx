import { FileDown } from "lucide-react";

import { SectionHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";

/** The OPML export: every feed at once, for a podcast app's import. */
export function OpmlSection() {
  return (
    <section aria-labelledby="opml-heading">
      <SectionHeader
        id="opml-heading"
        title="Podcast apps"
        description="Every feed with its credentials, to subscribe to all of them at once in Overcast, Pocket Casts or AntennaPod."
      />
      <Button variant="outline" size="sm" asChild>
        <a href="/api/feeds.opml" download="copycast.opml">
          <FileDown /> Export OPML
        </a>
      </Button>
    </section>
  );
}
