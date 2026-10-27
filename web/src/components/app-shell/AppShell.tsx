import { Link, Outlet, useLocation } from "@tanstack/react-router";
import { Command, Menu } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { CommandPalette } from "./CommandPalette";
import { AddSourceDialog } from "@/features/add-source/AddSourceDialog";
import { JobsIndicator } from "./JobsIndicator";
import { PlayerBar } from "./PlayerBar";
import { StatusIndicator } from "./StatusIndicator";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { ThemeToggle } from "./ThemeToggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ownsKey } from "@/lib/keyboard";
import { getPlayerState } from "@/stores/player";
import { cn } from "@/lib/utils";

const NAV: {
  to: "/" | "/jobs" | "/keys" | "/settings" | "/about";
  label: string;
  extra?: ReactNode;
}[] = [
  { to: "/", label: "Feeds" },
  { to: "/jobs", label: "Jobs", extra: <JobsIndicator /> },
  { to: "/keys", label: "API keys" },
  { to: "/settings", label: "Settings" },
  { to: "/about", label: "About" },
];

/** Feeds covers the page of every Feed and each Mirror's and Bucket's own page. */
function isCurrent(to: (typeof NAV)[number]["to"], pathname: string): boolean {
  if (to !== "/") return pathname === to || pathname.startsWith(`${to}/`);
  return pathname === "/" || /^\/(mirrors|buckets)(\/|$)/.test(pathname);
}

/**
 * Top nav + content column (`max-w-6xl`) + player; nav collapses into a Sheet under `md`.
 * The page keeps clear of the player through `--player-h`, so the shell never re-renders
 * with playback.
 */
export function AppShell() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const pathname = useLocation({ select: (location) => location.pathname });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (ownsKey(event.target, event.key) || event.metaKey || event.ctrlKey || event.altKey)
        return;
      // `/` and `?` are single-key shortcuts too: the switch in the shortcuts sheet turns them off.
      if (!getPlayerState().shortcuts) return;
      if (event.key === "/") {
        const filter = document.querySelector<HTMLInputElement>("[data-filter-input]");
        if (filter) {
          event.preventDefault();
          filter.focus();
        }
      } else if (event.key === "?") {
        event.preventDefault();
        setShortcutsOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const links = (onNavigate?: () => void) =>
    NAV.map((item) => {
      const current = isCurrent(item.to, pathname);
      return (
        <Link
          key={item.to}
          to={item.to}
          onClick={onNavigate}
          className={cn(
            "inline-flex items-center rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground",
            current && "font-semibold text-foreground",
          )}
          aria-current={current ? "page" : undefined}
          activeOptions={{ exact: item.to === "/", includeSearch: false }}
        >
          {item.label}
          {item.extra}
        </Link>
      );
    });

  return (
    <div className="min-h-dvh pb-[var(--player-h,0px)]">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-2 px-4">
          <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                aria-label="Open navigation"
              >
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64">
              <SheetHeader>
                <SheetTitle>Copycast</SheetTitle>
              </SheetHeader>
              <nav className="flex flex-col gap-1 px-2" aria-label="Main">
                {links(() => setMenuOpen(false))}
              </nav>
            </SheetContent>
          </Sheet>
          <Link
            to="/"
            className="mr-2 flex items-center gap-2 text-base font-semibold tracking-tight"
          >
            <span
              className="inline-flex size-6 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground"
              aria-hidden
            >
              C
            </span>
            Copycast
          </Link>
          <nav className="hidden items-center gap-1 md:flex" aria-label="Main">
            {links()}
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Open command palette"
                  onClick={() => setPaletteOpen(true)}
                >
                  <Command />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Command palette (⌘K)</TooltipContent>
            </Tooltip>
            <StatusIndicator />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl px-4 py-6" tabIndex={-1}>
        <Outlet />
      </main>
      <PlayerBar />
      <AddSourceDialog />
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onShowShortcuts={() => setShortcutsOpen(true)}
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
