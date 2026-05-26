// bin/clauditor.js pure-helper mirror (vitest runner). Only the
// arg-assembly helper is exercised here — the spawn/exit propagation
// tests live jest-only in __tests__/bin.test.js to avoid duplicating
// fake-child event plumbing across runners.
import { describe, test, expect } from "vitest";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const { buildChildArgs } = require("../bin/clauditor.js");

describe("buildChildArgs (vitest mirror)", () => {
  test("prepends empty prefix verbatim", () => {
    expect(
      buildChildArgs({ command: "clauditor", argsPrefix: [] }, ["validate", "foo.md", "--json"]),
    ).toEqual(["validate", "foo.md", "--json"]);
  });

  test("prepends python module fallback prefix", () => {
    expect(
      buildChildArgs(
        { command: "python3", argsPrefix: ["-m", "clauditor"] },
        ["run", "foo.md"],
      ),
    ).toEqual(["-m", "clauditor", "run", "foo.md"]);
  });

  test("preserves args with spaces verbatim", () => {
    expect(
      buildChildArgs({ command: "clauditor", argsPrefix: [] }, ["run", "a b c"]),
    ).toEqual(["run", "a b c"]);
  });
});
