/** Vitest setup: jest-dom matchers, jsdom polyfills Radix needs, and the MSW server. */
import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "./server";
import { closeAddSource } from "@/stores/addSource";

// `findBy*` / `waitFor` default to 1 s, which a whole-app render (router, query client, MSW)
// exceeds on a loaded CI runner; the tests still fail fast on a genuine miss.
configure({ asyncUtilTimeout: 4000 });

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function installDomPolyfills(): void {
  if (typeof window === "undefined") return;
  if (!window.matchMedia) {
    window.matchMedia = (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList;
  }
  if (!window.ResizeObserver) window.ResizeObserver = ResizeObserverStub;
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
  if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false;
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => undefined;
  if (!Element.prototype.releasePointerCapture)
    Element.prototype.releasePointerCapture = () => undefined;
  if (!window.HTMLElement.prototype.scrollTo)
    window.HTMLElement.prototype.scrollTo = () => undefined;
  if (!("EventSource" in window)) {
    (window as unknown as { EventSource: unknown }).EventSource = class {
      onopen = null;
      onerror = null;
      addEventListener(): void {}
      removeEventListener(): void {}
      close(): void {}
    };
  }
}

installDomPolyfills();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  closeAddSource();
  cleanup();
  server.resetHandlers();
  vi.useRealTimers();
  try {
    window.localStorage.clear();
  } catch {
    // ignore
  }
});
afterAll(() => server.close());
