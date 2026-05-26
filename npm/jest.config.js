// Jest config for clauditor-eval.
// Jest owns tests under __tests__/; vitest owns tests under test/ (see
// vitest.config.js). The two test runners are scoped to disjoint
// directories so running both does not double-collect the same files.
module.exports = {
  testEnvironment: "node",
  testMatch: ["<rootDir>/__tests__/**/*.test.js"],
};
