// TypeScript declarations for clauditor-eval's public JS API (US-004).
//
// v1 is a subprocess bridge to the Python clauditor engine. The shapes
// below mirror the engine's `run --json` / `validate --json` stdout
// payloads (DEC-004 / DEC-008).

/**
 * Parsed `clauditor run <skill> --json` payload.
 *
 * NOTE: v1 does NOT include `entries` — per-field L2 extraction requires
 * an eval spec and is exposed via `validate()` / the `extract` command.
 */
export interface RunSkillResult {
  output: string;
  exit_code: number;
  duration_seconds: number;
  error: string | null;
  error_category: string | null;
  warnings: string[];
  input_tokens: number;
  output_tokens: number;
  harness: string;
  skill: string;
  args: string;
  // The `run --json` stdout is unversioned (DEC-009); additional engine
  // fields are surfaced as-is.
  [key: string]: unknown;
}

/** One per-assertion result inside a `validate --json` payload. */
export interface ValidateResultEntry {
  name: string;
  passed: boolean;
  [key: string]: unknown;
}

/** Parsed `clauditor validate <skillPath> --json` payload. */
export interface ValidateResult {
  skill: string;
  pass_rate: number;
  passed: boolean;
  results: ValidateResultEntry[];
  /**
   * Present on the skill-failed-to-run path (exit 1 is data): a rendered
   * error string and the engine's error category. Absent on the normal
   * pass/fail path.
   */
  error?: string;
  error_category?: string | null;
  [key: string]: unknown;
}

export interface RunSkillOptions {
  /** Forwarded as `--args <value>`. */
  args?: string;
  /** Forwarded as `--project-dir <value>`. */
  projectDir?: string;
  /** Engine timeout in SECONDS (also used as the exec timeout in ms). */
  timeout?: number;
}

export interface ValidateOptions {
  /** Forwarded as `--eval <path>`. */
  eval?: string;
  /** Engine timeout in SECONDS (also used as the exec timeout in ms). */
  timeout?: number;
}

/**
 * Run a skill via `clauditor run <skill> --json`.
 *
 * Resolves with the parsed SkillResult shape. Throws `ClauditorInputError`
 * (exit 2), `ClauditorApiError` (exit 3), or `ClauditorError` on other
 * failures / non-JSON output.
 */
export function runSkill(
  skill: string,
  opts?: RunSkillOptions
): Promise<RunSkillResult>;

/**
 * Validate a skill against its eval spec via
 * `clauditor validate <skillPath> --json`.
 *
 * A failing eval (exit 1) resolves with `{passed: false, ...}` — it is data,
 * not an error. Exit 2 throws `ClauditorInputError`; exit 3 throws
 * `ClauditorApiError`.
 */
export function validate(
  skillPath: string,
  opts?: ValidateOptions
): Promise<ValidateResult>;

/**
 * Discover and read a clauditor eval spec from disk (DEC-012).
 *
 * If `target` ends in `.json`, it is read directly. Otherwise `target` is a
 * skill file path and the sibling eval is discovered (`X.md` -> `X.eval.json`;
 * `.../SKILL.md` -> `eval.json` or `<dir>.eval.json`). Throws if no eval file
 * is found. Does NOT shell out to the Python engine and does NOT re-validate
 * the spec (a documented v1 limitation).
 */
export function loadSpec(target: string): Promise<Record<string, unknown>>;

/** Base error class for the clauditor-eval subprocess bridge. */
export class ClauditorError extends Error {
  constructor(message: string);
}

/** Engine binary could not be resolved (no exit code). */
export class ClauditorNotFoundError extends ClauditorError {
  constructor(message: string);
}

/** Python exit code 2 — input validation failure. */
export class ClauditorInputError extends ClauditorError {
  constructor(message: string);
}

/** Python exit code 3 — provider API failure. */
export class ClauditorApiError extends ClauditorError {
  constructor(message: string);
}
