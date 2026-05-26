// ESLint flat config for clauditor-eval. Minimal, Node env, sane defaults.
const js = require("@eslint/js");
const globals = require("globals");

module.exports = [
  {
    ignores: ["node_modules/**", "coverage/**"],
  },
  // CommonJS sources: index.js, bin/, lib/, jest config, jest tests.
  {
    files: [
      "index.js",
      "bin/**/*.js",
      "lib/**/*.js",
      "jest-helper.js",
      "jest.config.js",
      "__tests__/**/*.js",
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: {
        ...globals.node,
        ...globals.jest,
      },
    },
    rules: {
      ...js.configs.recommended.rules,
    },
  },
  // ESM sources: vitest config + vitest tests.
  {
    files: ["vitest.config.js", "test/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
      },
    },
    rules: {
      ...js.configs.recommended.rules,
    },
  },
];
