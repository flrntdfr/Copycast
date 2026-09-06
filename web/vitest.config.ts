import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
      restoreMocks: true,
      coverage: {
        provider: "v8",
        include: ["src/features/**", "src/api/**", "src/lib/**", "src/stores/**"],
        exclude: ["src/api/schema.d.ts", "**/*.test.*"],
        reporter: ["text", "html"],
      },
    },
  }),
);
