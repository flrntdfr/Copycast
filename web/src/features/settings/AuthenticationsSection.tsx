import { CircleCheck, ClipboardPaste, Loader2, Lock, Trash2, Upload } from "lucide-react";
import { useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { $api } from "@/api/client";
import type { CookieServiceRead, EngineCookieFileRead } from "@/api/types";
import { SectionHeader } from "@/components/common/PageHeader";
import { RelativeTime } from "@/components/common/RelativeTime";
import { ServiceLogo } from "@/components/common/ServiceLogo";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { HelpTip } from "./HelpTip";
import { useDeleteCookies, useSetCookies } from "./mutations";

/**
 * The sites that need a login: one tile per service, unlocked (green check) once a stored
 * cookie file covers it, and the table of stored cookie files with Add, Paste and Remove.
 */
export function AuthenticationsSection() {
  const cookies = $api.useQuery("get", "/api/engine/cookies");
  const save = useSetCookies();
  const fileInput = useRef<HTMLInputElement>(null);
  const [pasting, setPasting] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const services = cookies.data?.services ?? [];
  const files = cookies.data?.files ?? [];

  const pickFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    void file.text().then((content) => save.mutate({ body: { content, name: file.name } }));
  };

  return (
    <section aria-labelledby="auth-heading">
      <SectionHeader
        id="auth-heading"
        title={
          <span className="inline-flex items-center gap-1.5">
            Authentications
            <HelpTip label="How authentication works">
              YouTube answers a server’s address with “Sign in to confirm you’re not a bot”. In a
              private browser window, sign in to the site, export its cookies with a “Get
              cookies.txt LOCALLY” extension, then close the window without signing out. Add the
              file here: the engine sends it along with every fetch, and it is never shown again.
            </HelpTip>
          </span>
        }
        actions={
          <>
            <input
              ref={fileInput}
              type="file"
              accept=".txt,text/plain"
              className="sr-only"
              aria-label="Pick a cookies.txt file"
              onChange={pickFile}
            />
            <Button
              size="sm"
              className="h-10 md:h-8"
              onClick={() => fileInput.current?.click()}
              disabled={save.isPending}
            >
              {save.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              Add cookies.txt…
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-10 md:h-8"
              onClick={() => setPasting(true)}
            >
              <ClipboardPaste /> Paste…
            </Button>
          </>
        }
      />
      <ul className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Services">
        {cookies.isPending
          ? null
          : services.map((service) => (
              <ServiceTile
                key={service.id}
                service={service}
                onClick={() => fileInput.current?.click()}
              />
            ))}
      </ul>
      {cookies.isError ? (
        <p className="text-sm text-destructive">The cookie files could not be loaded.</p>
      ) : (
        <CookieFilesTable files={files} onRemove={setRemoving} />
      )}
      <PasteDialog open={pasting} onOpenChange={setPasting} />
      <RemoveDialog name={removing} onClose={() => setRemoving(null)} />
    </section>
  );
}

/** A service's tile; a click picks a cookies.txt to unlock (or renew) it. */
function ServiceTile({ service, onClick }: { service: CookieServiceRead; onClick: () => void }) {
  const on = service.authenticated;
  return (
    <li>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onClick}
            aria-label={`${service.name}: ${on ? "unlocked" : "locked"}`}
            data-testid={`service-${service.id}`}
            data-authenticated={on}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
              on ? "border-success/40 bg-success/5" : "bg-muted/30",
            )}
          >
            <ServiceLogo service={service.id} active={on} className="size-6" />
            <span
              className={cn("min-w-0 flex-1 truncate font-medium", !on && "text-muted-foreground")}
            >
              {service.name}
            </span>
            {on ? (
              <CircleCheck className="size-4 shrink-0 text-success" aria-label="Unlocked" />
            ) : (
              <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-label="Locked" />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent>
          {on
            ? `Unlocked by ${service.files?.join(", ")}`
            : `Add a cookies.txt holding ${service.domains.join(" or ")} cookies`}
        </TooltipContent>
      </Tooltip>
    </li>
  );
}

function CookieFilesTable({
  files,
  onRemove,
}: {
  files: EngineCookieFileRead[];
  onRemove: (name: string) => void;
}) {
  return (
    <div className="rounded-lg border">
      <Table aria-label="Cookie files">
        <TableHeader>
          <TableRow>
            <TableHead>Cookie file</TableHead>
            <TableHead>Unlocks</TableHead>
            <TableHead className="text-right">Cookies</TableHead>
            <TableHead className="hidden sm:table-cell">Updated</TableHead>
            <TableHead className="w-10">
              <span className="sr-only">Remove</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {files.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-muted-foreground">
                No cookie file: every fetch is anonymous.
              </TableCell>
            </TableRow>
          ) : (
            files.map((file) => (
              <TableRow key={file.name} data-testid={`cookie-file-${file.name}`}>
                <TableCell className="max-w-40 truncate font-mono text-xs">
                  {file.name}.txt
                </TableCell>
                <TableCell>
                  <span className="flex items-center gap-1.5">
                    {(file.services ?? []).map((service) => (
                      <ServiceLogo key={service} service={service} className="size-4" />
                    ))}
                    {(file.services ?? []).length === 0 ? (
                      <span className="truncate text-xs text-muted-foreground">
                        {(file.domains ?? []).slice(0, 3).join(", ") || "—"}
                      </span>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {(file.cookie_count ?? 0).toLocaleString()}
                </TableCell>
                <TableCell className="hidden text-muted-foreground sm:table-cell">
                  <RelativeTime value={file.updated_at} fallback="unknown" />
                </TableCell>
                <TableCell>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-10 text-muted-foreground hover:text-destructive md:size-8"
                    aria-label={`Remove ${file.name}.txt`}
                    onClick={() => onRemove(file.name)}
                  >
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

function PasteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const save = useSetCookies();
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!content.trim()) return;
    save.mutate(
      { body: { content, name: name.trim() || "cookies" } },
      {
        onSuccess: () => {
          setContent("");
          setName("");
          onOpenChange(false);
        },
      },
    );
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <form onSubmit={submit} aria-label="Paste a cookie file" className="space-y-4">
          <DialogHeader>
            <DialogTitle>Paste a cookie file</DialogTitle>
            <DialogDescription>
              The Netscape cookies.txt text a browser extension exported. A name already in the
              table replaces that file.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="cookie-name">Name</Label>
            <Input
              id="cookie-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="youtube"
              maxLength={64}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cookie-content">Cookie file (Netscape cookies.txt)</Label>
            <Textarea
              id="cookie-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={8}
              spellCheck={false}
              placeholder={
                "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\t…"
              }
              className="font-mono text-xs"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!content.trim() || save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              Store cookies
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RemoveDialog({ name, onClose }: { name: string | null; onClose: () => void }) {
  const remove = useDeleteCookies();
  return (
    <AlertDialog open={name !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {name}.txt?</AlertDialogTitle>
          <AlertDialogDescription>
            The services it unlocks go back to anonymous fetches unless another file covers them;
            sites that need a login will fail until a new file is added.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>Keep it</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-white hover:bg-destructive/90"
            disabled={remove.isPending}
            onClick={(event) => {
              event.preventDefault();
              if (!name) return;
              remove.mutate({ params: { query: { name } } }, { onSuccess: onClose });
            }}
          >
            Remove
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
