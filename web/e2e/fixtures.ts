/**
 * The `test` every spec imports: Playwright's own, plus an automatic check that the page met
 * no Content-Security-Policy violation. The api sends a strict policy on the UI shell and its
 * assets; a script, style, image, media or fetch it does not allow is refused by the browser
 * and the page breaks quietly, so any `securitypolicyviolation` event or CSP console error,
 * on any document the test visited, fails the test that caused it.
 */
import { expect, test as base } from "@playwright/test";

interface Violation {
  directive: string;
  blocked: string;
  source: string;
}

declare global {
  interface Window {
    __copycastCspViolation?: (violation: Violation) => Promise<void>;
  }
}

const CSP_CONSOLE = /Content[- ]Security[- ]Policy|Refused to (load|execute|apply|connect|frame)/i;

export const test = base.extend<{ cspGuard: void }>({
  cspGuard: [
    async ({ page }, use) => {
      const violations: Violation[] = [];
      const consoleErrors: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error" && CSP_CONSOLE.test(message.text())) {
          consoleErrors.push(message.text());
        }
      });
      // The binding survives navigations; init scripts are injected by the driver, not the
      // page, so the CSP never blocks the listener itself.
      await page.exposeFunction("__copycastCspViolation", (violation: Violation) => {
        violations.push(violation);
      });
      await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (event) => {
          void window.__copycastCspViolation?.({
            directive: event.effectiveDirective,
            blocked: event.blockedURI,
            source: `${event.sourceFile}:${event.lineNumber}`,
          });
        });
      });
      await use();
      expect(violations, "Content-Security-Policy violations").toEqual([]);
      expect(consoleErrors, "Content-Security-Policy console errors").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
