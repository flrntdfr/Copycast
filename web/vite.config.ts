import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { fileURLToPath, URL } from "node:url";

const backend = process.env.COPYCAST_DEV_BACKEND ?? "http://127.0.0.1:8080";

/** Every backend prefix is proxied unbuffered so SSE and media ranges pass through. */
const proxied = ["/api", "/feeds", "/mcp", "/healthz"] as const;

export default defineConfig({
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
      routesDirectory: "./src/routes",
      generatedRouteTree: "./src/routeTree.gen.ts",
      routeFileIgnorePrefix: "-",
      quoteStyle: "double",
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      proxied.map((prefix) => [
        prefix,
        {
          target: backend,
          changeOrigin: false,
          ws: false,
          // Disable response buffering so text/event-stream frames arrive as sent.
          configure: (proxy) => {
            proxy.on("proxyRes", (proxyRes) => {
              proxyRes.headers["x-accel-buffering"] = "no";
            });
          },
        },
      ]),
    ),
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
    rollupOptions: {
      output: {
        // Vendor libraries change less often than the app; keep them cacheable on their own.
        manualChunks: {
          react: ["react", "react-dom"],
          tanstack: ["@tanstack/react-query", "@tanstack/react-router", "@tanstack/react-table"],
          radix: ["radix-ui"],
        },
      },
    },
  },
});
