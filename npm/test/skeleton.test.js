// Vitest skeleton test (US-002): assert the package requires cleanly.
import { describe, it, expect } from "vitest";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const pkg = require("../index.js");

describe("clauditor-eval skeleton (vitest)", () => {
  it("index.js requires cleanly and exports an object", () => {
    expect(typeof pkg).toBe("object");
    expect(pkg).not.toBeNull();
  });
});
