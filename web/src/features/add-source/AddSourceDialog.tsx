import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import { $api, ApiProblem, fetchClient } from "@/api/client";
import { OPS } from "@/api/ops";
import type { FeedRead, MirrorCreate, MirrorRead, ProbeCandidate, ProbeResult } from "@/api/types";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useSendToInbox } from "@/features/inboxes/useSendToInbox";
import { CandidateList } from "./CandidateList";
import { CreatedStep } from "./CreatedStep";
import { ProbeStep } from "./ProbeStep";
import { SearchStep } from "./SearchStep";
import {
  CREATE_TIMEOUT_MS,
  PROBE_TIMEOUT_MS,
  autoCandidate,
  findExistingMirror,
  initialWizardState,
  wizardReducer,
  type WizardStep,
} from "./wizard";
import { closeAddSource, openAddSource, useAddSource } from "@/stores/addSource";

const STEP_TITLES: Record<WizardStep, string> = {
  probe: "Looking at the Source",
  candidates: "Choose a Source",
  created: "Mirror created",
};

/** The Add Source dialog: probe, pick when several Sources are found, create, done. */
export function AddSourceDialog() {
  const request = useAddSource();
  return (
    <Dialog open={request.open} onOpenChange={(open) => !open && closeAddSource()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        {request.open ? (
          <AddSourceFlow
            key={request.nonce}
            url={request.url ?? ""}
            query={request.query ?? ""}
            sync={request.sync ?? false}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function AddSourceFlow({ url, query, sync }: { url: string; query: string; sync: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(wizardReducer, url, initialWizardState);
  const [creating, setCreating] = useState(false);
  const probeAbort = useRef<AbortController | null>(null);
  const createAbort = useRef<AbortController | null>(null);
  const sendToInbox = useSendToInbox();
  $api.useQuery("get", "/api/feeds", { params: { query: { kind: "mirror" } } });

  const knownMirrors = useCallback(async (): Promise<FeedRead[]> => {
    try {
      const list = await queryClient.ensureQueryData(
        $api.queryOptions("get", "/api/feeds", { params: { query: { kind: "mirror" } } }),
      );
      return list.feeds ?? [];
    } catch {
      return [];
    }
  }, [queryClient]);

  const openMirror = useCallback(
    async (mirrorId: string) => {
      closeAddSource();
      await navigate({ to: "/mirrors/$mirrorId", params: { mirrorId } });
    },
    [navigate],
  );

  const create = useCallback(
    async (candidate: ProbeCandidate) => {
      createAbort.current?.abort();
      const controller = new AbortController();
      createAbort.current = controller;
      const timer = setTimeout(() => controller.abort(), CREATE_TIMEOUT_MS);
      setCreating(true);
      try {
        // No policy here: the operator's default applies; the Mirror's Settings tab changes it.
        const body: MirrorCreate = {
          source_url: candidate.source_url,
          candidate_token: candidate.candidate_token,
          ...(sync ? { sync_deletions: true } : {}),
        };
        const { data } = await fetchClient.POST(OPS.create_mirror[1], {
          body,
          signal: controller.signal,
        });
        if (!data) return;
        const mirror: MirrorRead = data;
        void queryClient.invalidateQueries({ queryKey: [OPS.list_feeds[0], OPS.list_feeds[1]] });
        toast.success(`Mirror “${mirror.title}” created`);
        dispatch({ type: "created", mirror });
      } catch (error) {
        if (controller.signal.aborted) {
          toast.info("Creation cancelled");
          return;
        }
        if (ApiProblem.is(error) && error.slug === "feed-exists" && error.existingFeedId) {
          toast.info("This Source is already mirrored");
          await openMirror(error.existingFeedId);
          return;
        }
        const wording = ApiProblem.is(error)
          ? (error.detail ?? error.title)
          : "Could not create the Mirror";
        toast.error("Could not create the Mirror", { description: wording });
      } finally {
        clearTimeout(timer);
        setCreating(false);
        if (createAbort.current === controller) createAbort.current = null;
      }
    },
    [openMirror, queryClient, sync],
  );

  const probeMutation = $api.useMutation("post", "/api/probe", { meta: { silent: true } });
  const { mutate: runProbe } = probeMutation;
  const probe = useCallback(
    (target: string) => {
      probeAbort.current?.abort();
      const controller = new AbortController();
      probeAbort.current = controller;
      const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
      runProbe(
        { body: { url: target }, signal: controller.signal },
        {
          onSuccess: (result: ProbeResult) => {
            if (controller.signal.aborted) return;
            void (async () => {
              const auto = autoCandidate(result);
              if (auto) {
                const existing = findExistingMirror(await knownMirrors(), auto.source_url);
                if (existing) {
                  toast.info(`Already mirrored as “${existing.title}”`);
                  await openMirror(existing.id);
                  return;
                }
                await create(auto);
                return;
              }
              dispatch({ type: "probed", probe: result });
            })();
          },
          onSettled: () => {
            clearTimeout(timer);
            if (probeAbort.current === controller) probeAbort.current = null;
          },
        },
      );
    },
    [runProbe, knownMirrors, openMirror, create],
  );

  useEffect(() => {
    if (state.step !== "probe" || !state.url) return undefined;
    probe(state.url);
    return () => probeAbort.current?.abort();
    // Only re-probe when the URL or the step changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.step, state.url]);

  if (!url) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>Find a podcast</DialogTitle>
          <DialogDescription>
            Apple’s directory and YouTube; pick a result to mirror it, or paste a link.
          </DialogDescription>
        </DialogHeader>
        <SearchStep query={query} onPick={(feedUrl) => openAddSource({ url: feedUrl })} />
      </>
    );
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{creating ? "Creating the Mirror" : STEP_TITLES[state.step]}</DialogTitle>
        <DialogDescription className="font-mono text-xs break-all">{state.url}</DialogDescription>
      </DialogHeader>
      {state.step === "probe" ? (
        <ProbeStep
          url={state.url}
          error={probeMutation.isError ? probeMutation.error : null}
          creating={creating}
          onCancel={closeAddSource}
          onRetry={() => probe(state.url)}
          onSendToInbox={sendToInbox.target ? () => void sendToInbox.send(state.url) : undefined}
          sendingToInbox={sendToInbox.isPending}
        />
      ) : null}
      {state.step === "candidates" ? (
        <CandidateList
          probe={state.probe}
          onChoose={(candidate) => void create(candidate)}
          choosing={creating}
          onSendToInbox={
            sendToInbox.target
              ? (candidate) => void sendToInbox.send(candidate.source_url)
              : undefined
          }
          sendingToInbox={sendToInbox.isPending}
          onBack={closeAddSource}
        />
      ) : null}
      {state.step === "created" ? (
        <CreatedStep mirror={state.mirror} onOpen={() => void openMirror(state.mirror.id)} />
      ) : null}
    </>
  );
}
