import { useNavigate } from "@tanstack/react-router";
import {
  Info,
  PaintBucket,
  KeyRound,
  Keyboard,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Send,
  Settings,
} from "lucide-react";
import { useState } from "react";

import { $api } from "@/api/client";
import { openAddSource } from "@/stores/addSource";
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

/** ⌘K: feeds, pages, and a pasted URL or free text handed to the Add Source dialog. */
export function CommandPalette({
  open,
  onOpenChange,
  onShowShortcuts,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The shortcuts sheet, reachable here when `?` is switched off. */
  onShowShortcuts: () => void;
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
                  onSelect={() => go(() => openAddSource({ url: query.trim() }))}
                >
                  <Radio /> Mirror this URL
                </CommandItem>
                <CommandItem
                  value={`local feed ${query}`}
                  onSelect={() =>
                    go(() => void navigate({ to: "/", search: { url: query.trim() } }))
                  }
                >
                  <Send /> Send to Local feed…
                </CommandItem>
              </>
            ) : (
              <CommandItem
                value={`search ${query}`}
                onSelect={() => go(() => openAddSource({ query: query.trim() }))}
              >
                <Search /> Find a podcast named “{query.trim()}”
              </CommandItem>
            )}
          </CommandGroup>
        ) : null}
        <CommandGroup heading="Pages">
          <CommandItem onSelect={() => go(() => void navigate({ to: "/" }))}>
            <Radio /> Feeds
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/", hash: "buckets" }))}>
            <PaintBucket /> Buckets
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/jobs" }))}>
            <RefreshCw /> Jobs
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/keys" }))}>
            <KeyRound /> MCP
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/settings" }))}>
            <Settings /> Settings
          </CommandItem>
          <CommandItem onSelect={() => go(() => void navigate({ to: "/about" }))}>
            <Info /> About
          </CommandItem>
          <CommandItem onSelect={() => go(() => openAddSource({}))}>
            <Plus /> Add a Source
          </CommandItem>
          <CommandItem onSelect={() => go(onShowShortcuts)}>
            <Keyboard /> Keyboard shortcuts
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
                    go(() => void navigate({ to: "/feed/$feedId", params: { feedId: feed.id } }))
                  }
                >
                  {isMirror(feed) ? <Radio /> : <PaintBucket />} {feed.title}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        ) : null}
      </CommandList>
    </CommandDialog>
  );
}
