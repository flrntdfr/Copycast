import { z } from "zod";

import { catalogSearchDefaults, catalogSearchSchema } from "@/features/catalog/search";

export const feedSearchDefaults = { settings: false, url: "", ...catalogSearchDefaults } as const;

/** A feed page's search params: its Catalog's, plus two one-shot requests the page consumes. */
export const feedSearchSchema = catalogSearchSchema.extend({
  /** `?settings=true` opens a Mirror's Settings dialog once. */
  settings: z.boolean().default(feedSearchDefaults.settings),
  /** `?url=` prefills a Local feed's Add URL form (the Send-to-Local-feed flow). */
  url: z.string().default(feedSearchDefaults.url),
});

export type FeedSearch = z.infer<typeof feedSearchSchema>;
