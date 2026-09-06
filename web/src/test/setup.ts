/** Vitest setup: jest-dom matchers, jsdom polyfills Radix needs, and the MSW server. */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "./server";

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
