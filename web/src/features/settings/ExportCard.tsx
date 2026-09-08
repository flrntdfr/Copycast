import { FileDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** OPML of every feed, credentials included, to subscribe to all of them in one go. */
export function ExportCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileDown className="size-4" aria-hidden /> Export
        </CardTitle>
        <CardDescription>
          An OPML file listing every Mirror and Inbox feed with its credentials; import it in
          Overcast, Pocket Casts or AntennaPod to subscribe to everything at once.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" asChild>
          <a href="/api/feeds.opml" download="copycast.opml">
            <FileDown /> Export OPML
          </a>
        </Button>
      </CardContent>
    </Card>
  );
}
