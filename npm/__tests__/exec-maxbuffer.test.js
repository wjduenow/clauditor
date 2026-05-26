// execJson maxBuffer-overflow branch (jest only). This case is NOT
// mirrored under vitest: it relies on mocking the CommonJS
// `require("child_process")` that exec.js loads, which `vi.mock` does not
// intercept when the module is pulled in via `createRequire`. jest's
// `jest.mock` patches the CJS require cache and covers the branch here.
//
// On stdout/stderr overflow, Node's execFile rejects with a STRING
// err.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" (not a numeric exit
// code). exec.js must surface that as a clear ClauditorError naming the
// buffer size, not the misleading "killed (timeout?)" message.

// Mock child_process BEFORE requiring exec.js so promisify(execFile) wraps
// our fake. The fake invokes the callback with the maxBuffer error.
jest.mock("child_process", () => ({
  execFile: (_file, _args, _options, callback) => {
    const err = new Error("stdout maxBuffer length exceeded");
    err.code = "ERR_CHILD_PROCESS_STDIO_MAXBUFFER";
    callback(err);
  },
}));

const { execJson } = require("../lib/exec");
const { ClauditorError } = require("../lib/errors");

describe("execJson maxBuffer overflow", () => {
  const ORIG_BIN = process.env.CLAUDITOR_BIN;

  beforeEach(() => {
    // Pin CLAUDITOR_BIN so resolveBinary() short-circuits without a PATH scan.
    process.env.CLAUDITOR_BIN = "/usr/bin/clauditor";
  });

  afterEach(() => {
    if (ORIG_BIN === undefined) {
      delete process.env.CLAUDITOR_BIN;
    } else {
      process.env.CLAUDITOR_BIN = ORIG_BIN;
    }
  });

  test("maps ERR_CHILD_PROCESS_STDIO_MAXBUFFER to a clear ClauditorError", async () => {
    await expect(execJson(["run", "x", "--json"])).rejects.toThrow(
      ClauditorError,
    );
    await expect(execJson(["run", "x", "--json"])).rejects.toThrow(/exceeded/);
    await expect(execJson(["run", "x", "--json"])).rejects.toThrow(/64 MB/);
  });
});
