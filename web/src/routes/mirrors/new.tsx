import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useCallback } from "react";
import { z } from "zod";

import { AddSourceWizard } from "@/features/add-source/AddSourceWizard";
import { WIZARD_STEPS } from "@/features/add-source/wizard";

const defaults = { url: "", step: "probe", query: "" } as const;

const searchSchema = z.object({
  url: z.string().default(defaults.url),
  step: z.enum(WIZARD_STEPS).default(defaults.step),
  query: z.string().default(defaults.query),
});

export const Route = createFileRoute("/mirrors/new")({
  validateSearch: searchSchema,
  search: { middlewares: [stripSearchParams(defaults)] },
  component: NewMirrorPage,
});

function NewMirrorPage() {
  const { url, step, query } = Route.useSearch();
  const navigate = Route.useNavigate();
  const onStepChange = useCallback(
    (next: (typeof WIZARD_STEPS)[number]) => {
      void navigate({ search: (prev) => ({ ...prev, step: next }), replace: next === "probe" });
    },
    [navigate],
  );
  const onUrlChange = useCallback(
    (next: string) => {
      void navigate({ search: { url: next, step: "probe", query: "" } });
    },
    [navigate],
  );
  return (
    <div className="mx-auto max-w-3xl">
      <AddSourceWizard
        url={url.trim()}
        step={step}
        query={query}
        onStepChange={onStepChange}
        onUrlChange={onUrlChange}
      />
    </div>
  );
}
