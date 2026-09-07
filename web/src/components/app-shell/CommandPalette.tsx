import { useNavigate } from "@tanstack/react-router";
import {
  Info,
  Inbox,
  KeyRound,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Send,
  Settings,
} from "lucide-react";
import { useState } from "react";

import { $api } from "@/api/client";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { isMirror } from "@/api/types";

const URL_RE = /^(https?:\/\/|www\.)\S+$/i;

/** ⌘K: feeds, pages, and a pasted URL or free text handed to the Add Source wizard. */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const { data } = $api.useQuery("get", "/api/feeds", undefined, { enabled: open });
  const feeds = data?.feeds ?? [];
  const looksLikeUrl = URL_RE.test(query.trim());

  const handleOpenChange = (next: boolean) => {
    if (!next) setQuery("");
    onOpenChange(next);
  };
  const go = (action: () => void) => {
    handleOpenChange(false);
    action();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Command palette"
      description="Jump to a feed or an action"
    >
      <CommandInput
        placeholder="Search feeds, paste a URL, or type a podcast name…"
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        {query.trim() ? (
          <CommandGroup heading="Actions">
            {looksLikeUrl ? (
              <>
                <CommandItem
                  value={`mirror ${query}`}
                  onSelect={() =>
                    go(
                      () =>
                        void navigate({
                          to: "/mirrors/new",
                          search: { url: query.trim(), step: "probe" },
                        }),
                    )
                  }
                >
                  <Radio /> Mirror this URL
                </CommandItem>
                <CommandItem
                  value={`inbox ${query}`}
                  onSelect={() =>
                    go(() => void navigate({ to: "/inboxes", search: { url: query.trim() } }))
                  }
                >
                  <Send /> Send to Inbox…
                </CommandItem>
              </>
            ) : (
              <CommandItem
                value={`search ${query}`}
                onSelect={() =>
                  go(
                    () =>
                      void navigate({
                        to: "/mirrors/new",
                        search: { query: query.trim(), step: "probe" },
                      }),
                  )
                }
              >
                <Search /> Find a podcast named “{query.trim()}”
              </CommandItem>
            )}
          </CommandGroup>
        ) : null}
        <CommandGroup heading="Pages">
          <CommandItem onSelect={() => go(() => void navigate({ to: "/mirrors" }))}>
            <Radio /> Mirrors
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/inboxes" }))}>
            <Inbox /> Inboxes
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/jobs" }))}>
            <RefreshCw /> Jobs
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/keys" }))}>
            <KeyRound /> API keys
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/settings" }))}>
            <Settings /> Settings
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/about" }))}>
            <Info /> About
          </CommandItem>
          <CommandItem
            onSelect={() =>
              go(() => void navigate({ to: "/mirrors/new", search: { step: "probe" } }))
            }
          >
            <Plus /> Add a Source
          </CommandItem>
        </CommandGroup>
        {feeds.length ? (
          <>
            <CommandSeparator />
            <CommandGroup heading="Feeds">
              {feeds.map((feed) => (
                <CommandItem
                  key={feed.id}
                  value={`${feed.title} ${feed.id}`}
                  onSelect={() =>
                    go(
                      () =>
                        void (isMirror(feed)
                          ? navigate({ to: "/mirrors/$mirrorId", params: { mirrorId: feed.id } })
                          : navigate({ to: "/inboxes/$inboxId", params: { inboxId: feed.id } })),
                    )
                  }
                >
                  {isMirror(feed) ? <Radio /> : <Inbox />} {feed.title}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        ) : null}
      </CommandList>
    </CommandDialog>
  );
}
