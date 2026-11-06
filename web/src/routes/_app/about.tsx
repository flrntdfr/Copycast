import { createFileRoute } from "@tanstack/react-router";

import { AboutPage } from "@/features/about/AboutPage";

export const Route = createFileRoute("/_app/about")({ component: AboutPage });
