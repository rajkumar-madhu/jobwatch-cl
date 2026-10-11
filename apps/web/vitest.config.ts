import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
  test: {
    environment: "jsdom", include: ["tests/unit/**/*.test.ts"], globals: true,
    // Node 25+ ships its own `localStorage` global (undefined without --localstorage-file), which
    // shadows jsdom's. Turning it off keeps tests on jsdom's storage on any Node version.
    poolOptions: { forks: { execArgv: ["--no-experimental-webstorage"] } },
  },
});
