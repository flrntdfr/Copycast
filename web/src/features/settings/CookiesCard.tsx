import { Cookie, Loader2, Trash2, Upload } from "lucide-react";
import { useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { $api } from "@/api/client";
import { RelativeTime } from "@/components/common/RelativeTime";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDeleteCookies, useSetCookies } from "./mutations";
import { formatBytes } from "@/lib/format";

/**
 * The engine's cookie file (Netscape cookies.txt): what is stored, never its values,
 * a paste box or file picker to replace it, and Remove.
 */
export function CookiesCard() {
  const cookies = $api.useQuery("get", "/api/engine/cookies");
  const save = useSetCookies();
  const remove = useDeleteCookies();
  const [content, setContent] = useState("");
  const [removing, setRemoving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const stored = cookies.data;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!content.trim()) return;
    save.mutate({ body: { content } }, { onSuccess: () => setContent("") });
  };
  const pickFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    void file.text().then(setContent);
    event.target.value = "";
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cookie className="size-4" aria-hidden /> YouTube and other sites that need a login
        </CardTitle>
        <CardDescription>
          YouTube answers a server’s address with “Sign in to confirm you’re not a bot”. Give the
          engine the cookies of a logged-in browser session and it downloads as that session; the
          file is stored under the data directory, readable by Copycast only, and never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div data-testid="cookies-status" className="text-sm">
          {cookies.isPending ? (
            "…"
          ) : cookies.isError ? (
            <span className="text-destructive">The cookie status could not be loaded.</span>
          ) : stored?.present ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-medium">
                {stored.cookie_count.toLocaleString()} cookies stored
              </span>
              <span className="text-muted-foreground">
                {formatBytes(stored.size_bytes)} · updated{" "}
                <RelativeTime value={stored.updated_at} fallback="unknown" />
              </span>
              {(stored.domains ?? []).map((domain) => (
                <Badge key={domain} variant={domain === "youtube.com" ? "default" : "outline"}>
                  {domain}
                </Badge>
              ))}
              {!stored.youtube ? (
                <span className="text-warning-foreground">No youtube.com cookie in the file.</span>
              ) : null}
            </div>
          ) : (
            <span className="text-muted-foreground">
              No cookies stored: every fetch is anonymous.
            </span>
          )}
        </div>
        <form onSubmit={submit} className="space-y-3" aria-label="Store cookies">
          <div className="space-y-1.5">
            <Label htmlFor="cookies-content">Cookie file (Netscape cookies.txt)</Label>
            <Textarea
              id="cookies-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={7}
              spellCheck={false}
              placeholder={
                "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\t…"
              }
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              In a browser, open a private window, sign in to youtube.com, export its cookies with a
              “Get cookies.txt LOCALLY” extension, then close that window without signing out so the
              session stays valid. Paste the file here or pick it.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!content.trim() || save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              {stored?.present ? "Replace cookies" : "Store cookies"}
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept=".txt,text/plain"
              className="sr-only"
              aria-label="Pick a cookies.txt file"
              onChange={pickFile}
            />
            <Button type="button" variant="outline" onClick={() => fileInput.current?.click()}>
              Pick a file…
            </Button>
            {stored?.present ? (
              <Button
                type="button"
                variant="outline"
                className="text-destructive"
                onClick={() => setRemoving(true)}
              >
                <Trash2 /> Remove…
              </Button>
            ) : null}
          </div>
        </form>
      </CardContent>
      <AlertDialog open={removing} onOpenChange={setRemoving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove the stored cookies?</AlertDialogTitle>
            <AlertDialogDescription>
              Fetches become anonymous again; sites that need a login will fail until a new file is
              stored.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Keep them</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault();
                remove.mutate({}, { onSuccess: () => setRemoving(false) });
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
