/** `OPS` must name every consumed capability exactly as `x-capability` in openapi.json. */
import { describe, expect, it } from "vitest";

import openapi from "../../openapi.json";
import { EXTRA_OPS, OPS } from "./ops";

interface Operation {
  operationId?: string;
  "x-capability"?: string;
}
interface Document {
  paths: Record<string, Record<string, Operation>>;
}

const document = openapi as unknown as Document;

function capabilityRoutes(): Map<string, [string, string]> {
  const routes = new Map<string, [string, string]>();
  for (const [path, methods] of Object.entries(document.paths)) {
    for (const [method, operation] of Object.entries(methods)) {
      const capability = operation["x-capability"];
      if (!capability) continue;
      expect(routes.has(capability), `capability ${capability} has two routes`).toBe(false);
      routes.set(capability, [method, path]);
    }
  }
  return routes;
}

describe("ops drift", () => {
  const routes = capabilityRoutes();

  it("maps every OPS entry to the route carrying that x-capability", () => {
    for (const [capability, [method, path]] of Object.entries(OPS)) {
      const route = routes.get(capability);
      expect(route, `capability ${capability} is not in openapi.json`).toBeDefined();
      expect([method, path]).toEqual(route);
      expect(document.paths[path]?.[method]?.operationId).toBe(capability);
    }
  });

  it("consumes every routed capability the API exposes", () => {
    const missing = [...routes.keys()].filter((capability) => !(capability in OPS));
    expect(missing).toEqual([]);
  });

  it("only lists real capability-less routes in EXTRA_OPS", () => {
    for (const [name, [method, path]] of Object.entries(EXTRA_OPS)) {
      const operation = document.paths[path]?.[method];
      expect(operation, `${name} -> ${method} ${path} is not a route`).toBeDefined();
      expect(operation?.["x-capability"]).toBeUndefined();
    }
  });
});
