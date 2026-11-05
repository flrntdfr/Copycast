/**
 * Zod without its JIT. Zod 4 compiles object schemas with `new Function` after probing
 * whether eval is allowed; the api's Content-Security-Policy forbids eval, so the probe
 * alone would report a violation on every page. Imported first by `main.tsx`; parsing
 * stays the same, only slower on very large objects.
 */
import { z } from "zod";

z.config({ jitless: true });
