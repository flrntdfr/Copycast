import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { Command, Menu, Plus } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { CommandPalette } from "./CommandPalette";
import { ConnectionDot } from "./ConnectionDot";
import { JobsIndicator } from "./JobsIndicator";
import { PlayerBar } from "./PlayerBar";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { ThemeToggle } from "./ThemeToggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { usePlayer } from "@/stores/player";
import { cn } from "@/lib/utils";

const NAV: {
  to: "/mirrors" | "/inboxes" | "/jobs" | "/keys" | "/settings" | "/about";
  label: string;
  extra?: ReactNode;
}[] = [
  { to: "/mirrors", label: "Mirrors" },
  { to: "/inboxes", label: "Inboxes" },
  { to: "/jobs", label: "Jobs", extra: <JobsIndicator /> },
  { to: "/keys", label: "API keys" },
  { to: "/settings", label: "Settings" },
  { to: "/about", label: "About" },
];

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/** Top nav + content column (`max-w-6xl`) + player; nav collapses into a Sheet under `md`. */
export function AppShell() {
  const navigate = useNavigate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const { track } = usePlayer();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (isEditable(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
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
    NAV.map((item) => (
      <Link
        key={item.to}
        to={item.to}
        onClick={onNavigate}
        className="inline-flex items-center rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground data-[status=active]:font-semibold data-[status=active]:text-foreground"
        activeOptions={{ includeSearch: false }}
      >
        {item.label}
        {item.extra}
      </Link>
    ));

  return (
    <div className={cn("min-h-dvh", track && "pb-16")}>
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
            to="/mirrors"
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
            <Button
              size="sm"
              onClick={() => void navigate({ to: "/mirrors/new", search: { step: "probe" } })}
            >
              <Plus /> <span className="hidden sm:inline">Add Source</span>
            </Button>
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
            <ConnectionDot />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl px-4 py-6" tabIndex={-1}>
        <Outlet />
      </main>
      <PlayerBar />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
