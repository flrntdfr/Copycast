import { Check, CircleAlert, Loader2, Upload } from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";
import { toast } from "sonner";

import { describeProblem, fetchClient } from "@/api/client";
import type { BucketRead } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { useInvalidateCatalog } from "@/features/catalog/mutations";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { formatBytes } from "@/lib/format";
import { count } from "@/lib/labels";
import { naturalCompare } from "@/lib/natural";

/** What `create_upload` takes: mp3, m4a and the MPEG-4 audio containers served as m4a. */
export const UPLOAD_ACCEPT = ".mp3,.m4a,.m4b,.mp4,audio/mpeg,audio/mp4,audio/x-m4a,audio/x-m4b";

type FileState =
  | { status: "waiting" }
  | { status: "uploading"; percent: number }
  | { status: "received"; uploadId: string }
  | { status: "failed"; error: string };

interface Entry {
  file: File;
  state: FileState;
}

/** PUT the bytes to the one-time upload URL, reporting progress; same origin, path only. */
function putFile(uploadUrl: string, file: File, onProgress: (percent: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", new URL(uploadUrl, window.location.origin).pathname);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(problemTitle(xhr.responseText) ?? `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Network error"));
    xhr.send(file);
  });
}

function problemTitle(body: string): string | null {
  try {
    const problem = JSON.parse(body) as { detail?: string; title?: string };
    return problem.detail ?? problem.title ?? null;
  } catch {
    return null;
  }
}

function errorText(error: unknown): string {
  const { title, description } = describeProblem(error);
  return description ?? title;
}

/**
 * "Upload files…": pick audio files, upload them one by one in natural file-name order and
 * add them to the Bucket as Episodes, dated by arrival (`create_upload`, `add_uploads`).
 */
export function UploadFilesButton({ bucket }: { bucket: BucketRead }) {
  const input = useRef<HTMLInputElement>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const invalidateFeed = useInvalidateFeed();
  const invalidateCatalog = useInvalidateCatalog(bucket.id);

  const update = (index: number, state: FileState) =>
    setEntries((current) => current.map((entry, i) => (i === index ? { ...entry, state } : entry)));

  const run = async (files: File[]) => {
    setBusy(true);
    const ids: string[] = [];
    for (const [index, file] of files.entries()) {
      update(index, { status: "uploading", percent: 0 });
      try {
        const { data } = await fetchClient.POST("/api/uploads", { body: { filename: file.name } });
        if (!data?.upload_url) throw new Error("No upload URL");
        await putFile(data.upload_url, file, (percent) =>
          update(index, { status: "uploading", percent }),
        );
        ids.push(data.id);
        update(index, { status: "received", uploadId: data.id });
      } catch (error) {
        update(index, { status: "failed", error: errorText(error) });
      }
    }
    if (ids.length > 0) {
      try {
        await fetchClient.POST("/api/buckets/{bucket_id}/uploads", {
          params: { path: { bucket_id: bucket.id } },
          body: { upload_ids: ids },
        });
        toast.success(`Added ${count(ids.length, "Episode")} to ${bucket.name}`);
      } catch (error) {
        toast.error("The uploads could not be added", { description: errorText(error) });
      }
      invalidateFeed(bucket.id);
      invalidateCatalog();
    }
    setBusy(false);
  };

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const files = [...(event.target.files ?? [])].sort((a, b) => naturalCompare(a.name, b.name));
    event.target.value = "";
    if (files.length === 0) return;
    setEntries(files.map((file) => ({ file, state: { status: "waiting" } })));
    void run(files);
  };

  const failed = entries.filter((entry) => entry.state.status === "failed").length;
  const done = entries.length - entries.filter((e) => e.state.status === "waiting").length;
  return (
    <>
      <input
        ref={input}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="hidden"
        aria-label="Audio files to upload"
        onChange={pick}
      />
      <Button variant="outline" size="sm" onClick={() => input.current?.click()} disabled={busy}>
        <Upload /> Upload files…
      </Button>
      <Dialog
        open={entries.length > 0}
        onOpenChange={(open) => {
          if (!open && !busy) setEntries([]);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upload to {bucket.name}</DialogTitle>
            <DialogDescription>
              In file-name order, so Part 2 comes before Part 10; each file becomes an Episode dated
              by its arrival.
            </DialogDescription>
          </DialogHeader>
          <ol className="max-h-80 space-y-3 overflow-y-auto text-sm">
            {entries.map(({ file, state }) => (
              <li key={`${file.name}-${file.size}`} className="space-y-1">
                <div className="flex items-center gap-2">
                  {state.status === "received" ? (
                    <Check className="size-4 shrink-0 text-green-600" aria-label="Uploaded" />
                  ) : state.status === "failed" ? (
                    <CircleAlert className="size-4 shrink-0 text-destructive" aria-label="Failed" />
                  ) : state.status === "uploading" ? (
                    <Loader2 className="size-4 shrink-0 animate-spin" aria-label="Uploading" />
                  ) : (
                    <span className="size-4 shrink-0" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="shrink-0 text-muted-foreground tabular-nums">
                    {formatBytes(file.size)}
                  </span>
                </div>
                {state.status === "uploading" ? (
                  <Progress value={state.percent} aria-label={`${file.name} upload progress`} />
                ) : null}
                {state.status === "failed" ? (
                  <p className="pl-6 text-destructive">{state.error}</p>
                ) : null}
              </li>
            ))}
          </ol>
          <DialogFooter>
            <p className="mr-auto self-center text-sm text-muted-foreground tabular-nums">
              {busy
                ? `${done} of ${entries.length}`
                : failed > 0
                  ? `${count(failed, "file")} failed`
                  : "Done"}
            </p>
            <Button variant="outline" disabled={busy} onClick={() => setEntries([])}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
