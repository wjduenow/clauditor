// Jest skeleton test (US-002): assert the package requires cleanly.
const pkg = require("../index.js");

describe("clauditor-eval skeleton (jest)", () => {
  test("index.js requires cleanly and exports an object", () => {
    expect(typeof pkg).toBe("object");
    expect(pkg).not.toBeNull();
  });
});
