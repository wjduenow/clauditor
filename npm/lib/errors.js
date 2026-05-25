// Error classes for the clauditor-eval subprocess bridge.
//
// All errors extend a single base (`ClauditorError`) so callers can catch
// the whole family with one `catch (err) { if (err instanceof ClauditorError) }`
// check. Each subclass maps to a distinct failure category, mirroring the
// Python engine's exit-code taxonomy (.claude/rules/llm-cli-exit-code-taxonomy.md):
//
//   - ClauditorNotFoundError  — engine binary could not be resolved (no exit code).
//   - ClauditorInputError     — Python exit code 2 (input validation failure).
//   - ClauditorApiError       — Python exit code 3 (provider API failure).
//
// A failing eval (Python exit code 1) is DATA, not an exception: execJson
// returns the parsed JSON so the caller inspects `passed` itself.

class ClauditorError extends Error {
  constructor(message) {
    super(message);
    this.name = "ClauditorError";
  }
}

class ClauditorNotFoundError extends ClauditorError {
  constructor(message) {
    super(message);
    this.name = "ClauditorNotFoundError";
  }
}

class ClauditorInputError extends ClauditorError {
  constructor(message) {
    super(message);
    this.name = "ClauditorInputError";
  }
}

class ClauditorApiError extends ClauditorError {
  constructor(message) {
    super(message);
    this.name = "ClauditorApiError";
  }
}

module.exports = {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
};
