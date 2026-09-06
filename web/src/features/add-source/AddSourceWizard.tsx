import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import { $api, ApiProblem, fetchClient } from "@/api/client";
import { OPS } from "@/api/ops";
import type { FeedRead, MirrorRead, ProbeCandidate, ProbeResult } from "@/api/types";
import { useSendToInbox } from "@/features/inboxes/useSendToInbox";
import { CandidateList } from "./CandidateList";
import { CreatedStep } from "./CreatedStep";
import { PolicyStep } from "./PolicyStep";
import { ProbeStep } from "./ProbeStep";
import { SearchStep } from "./SearchStep";
import { UrlStep } from "./UrlStep";
import { toMirrorCreate, type PolicyFormValues } from "./policy-form";
import {
  CREATE_SELECTION_TIMEOUT_MS,
  PROBE_TIMEOUT_MS,
  WIZARD_STEPS,
  findExistingMirror,
  initialWizardState,
  wizardReducer,
  type WizardStep,
} from "./wizard";

export interface AddSourceWizardProps {
  url: string;
  step: WizardStep;
  query?: string;
  onStepChange: (step: WizardStep) => void;
  onUrlChange: (url: string) => void;
}

const STEP_TITLES: Record<WizardStep, string> = {
  probe: "Looking at the Source",
  candidates: "Choose a Source",
  policy: "What to archive",
  created: "Mirror created",
};

export function AddSourceWizard({
  url,
  step,
  query,
  onStepChange,
  onUrlChange,
}: AddSourceWizardProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(wizardReducer, url, initialWizardState);
  const [creating, setCreating] = useState(false);
  const probeAbort = useRef<AbortController | null>(null);
  const createAbort = useRef<AbortController | null>(null);
  const sendToInbox = useSendToInbox();
  $api.useQuery("get", "/api/feeds", { params: { query: { kind: "mirror" } } });

  /** The known Mirrors, fetched if the list has not arrived yet; the server 409 stays the authority. */
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

  // URL changed from outside (new paste, palette): start over.
  useEffect(() => {
    if (state.url !== url) dispatch({ type: "restart", url });
  }, [url, state.url]);

  // Browser navigation moved `step` backwards: only a *changed* URL step counts, so the
  // moment between advancing the state and the URL catching up does not reset the wizard.
  const previousStep = useRef(step);
  useEffect(() => {
    const previous = previousStep.current;
    previousStep.current = step;
    if (previous !== step && WIZARD_STEPS.indexOf(step) < WIZARD_STEPS.indexOf(state.step)) {
      dispatch({ type: "goto", step });
    }
  }, [step, state.step]);

  // Keep the URL's `step` in sync with the state.
  useEffect(() => {
    if (state.step !== step) onStepChange(state.step);
  }, [state.step, step, onStepChange]);

  /** The probe runs as a mutation so its pending/error state needs no setState of our own. */
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
              const [only] = result.candidates;
              if (result.candidates.length === 1 && only) {
                const existing = findExistingMirror(await knownMirrors(), only.source_url);
                if (existing) {
                  toast.info(`Already mirrored as “${existing.title}”`);
                  await navigate({
                    to: "/mirrors/$mirrorId",
                    params: { mirrorId: existing.id },
                    replace: true,
                  });
                  return;
                }
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
    [runProbe, knownMirrors, navigate],
  );

  useEffect(() => {
    if (state.step !== "probe" || !state.url) return undefined;
    probe(state.url);
    return () => probeAbort.current?.abort();
    // Only re-probe when the URL or the step changes, not when the feeds list refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.step, state.url]);

  const cancelProbe = () => {
    probeAbort.current?.abort();
    void navigate({ to: "/mirrors" });
  };

  const create = async (candidate: ProbeCandidate, values: PolicyFormValues) => {
    createAbort.current?.abort();
    const controller = new AbortController();
    createAbort.current = controller;
    const timer = setTimeout(() => controller.abort(), CREATE_SELECTION_TIMEOUT_MS);
    setCreating(true);
    try {
      const { data } = await fetchClient.POST(OPS.create_mirror[1], {
        body: toMirrorCreate(candidate, values),
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
        await navigate({
          to: "/mirrors/$mirrorId",
          params: { mirrorId: error.existingFeedId },
          replace: true,
        });
        return;
      }
      if (ApiProblem.is(error) && error.slug === "candidates-ambiguous") {
        toast.error("Pick one of the Sources found");
        dispatch({ type: "goto", step: "candidates" });
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
  };

  const sendCandidateToInbox = (candidate: ProbeCandidate) =>
    void sendToInbox.send(candidate.source_url);

  if (!url && query) {
    return (
      <section aria-labelledby="wizard-title">
        <h1 id="wizard-title" className="mb-4 text-2xl font-semibold tracking-tight">
          Find a podcast
        </h1>
        <SearchStep query={query} onPick={(feedUrl) => onUrlChange(feedUrl)} />
      </section>
    );
  }
  if (!url) {
    return (
      <section aria-labelledby="wizard-title">
        <h1 id="wizard-title" className="mb-4 text-2xl font-semibold tracking-tight">
          Add a Source
        </h1>
        <UrlStep onSubmit={onUrlChange} />
      </section>
    );
  }

  return (
    <section aria-labelledby="wizard-title" className="space-y-6">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Step {WIZARD_STEPS.indexOf(state.step) + 1} of {WIZARD_STEPS.length}
        </p>
        <h1 id="wizard-title" className="text-2xl font-semibold tracking-tight">
          {STEP_TITLES[state.step]}
        </h1>
      </div>
      {state.step === "probe" ? (
        <ProbeStep
          url={state.url}
          error={probeMutation.isError ? probeMutation.error : null}
          onCancel={cancelProbe}
          onRetry={() => probe(state.url)}
          onSendToInbox={sendToInbox.target ? () => void sendToInbox.send(state.url) : undefined}
          sendingToInbox={sendToInbox.isPending}
        />
      ) : null}
      {state.step === "candidates" ? (
        <CandidateList
          probe={state.probe}
          onChoose={(candidate) => dispatch({ type: "choose", token: candidate.candidate_token })}
          onSendToInbox={sendToInbox.target ? sendCandidateToInbox : undefined}
          sendingToInbox={sendToInbox.isPending}
          onBack={() => void navigate({ to: "/mirrors" })}
        />
      ) : null}
      {state.step === "policy" ? (
        <PolicyStep
          candidate={state.candidate}
          submitting={creating}
          onSubmit={(values) => void create(state.candidate, values)}
          onCancelSubmit={() => createAbort.current?.abort()}
          onBack={() =>
            state.probe.candidates.length > 1
              ? dispatch({ type: "goto", step: "candidates" })
              : void navigate({ to: "/mirrors" })
          }
          onSendToInbox={
            sendToInbox.target ? () => sendCandidateToInbox(state.candidate) : undefined
          }
          sendingToInbox={sendToInbox.isPending}
        />
      ) : null}
      {state.step === "created" ? <CreatedStep mirror={state.mirror} /> : null}
    </section>
  );
}
