import { defineConfig } from "vitest/config";

// Vitest config for clauditor-eval.
// Vitest owns tests under test/; jest owns tests under __tests__/ (see
// jest.config.js). The two test runners are scoped to disjoint
// directories so running both does not double-collect the same files.
export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.js"],
  },
});
