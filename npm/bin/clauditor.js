#!/usr/bin/env node
// clauditor-eval CLI launcher (npx clauditor-eval / `clauditor` bin).
//
// v1 is a SUBPROCESS BRIDGE (US-005): resolve the Python `clauditor`
// engine via lib/resolve-binary.js, forward every CLI arg through to it
// with inherited stdio, and propagate the child's exit code.
//
// The core logic lives in a testable ``run(argv, deps)`` function with
// injectable dependencies (spawn / resolve / stderr / exit) so tests can
// drive it with a fake child without launching a real process. The
// ``require.main === module`` guard at the bottom wires it to the real
// dependencies when executed as a script (per pure-compute-vs-io-split.md:
// arg-assembly is separated from the spawn I/O).
"use strict";

const childProcess = require("child_process");

const { resolveBinary } = require("../lib/resolve-binary");
const { ClauditorNotFoundError } = require("../lib/errors");

// Exit code for engine-missing / input-category failures, mirroring the
// Python exit-code taxonomy (DEC-008): 2 == input/pre-call category.
const ENGINE_MISSING_EXIT = 2;

// Pure: assemble the argv array the child engine should receive.
// ``resolution.argsPrefix`` makes the ``python3 -m clauditor`` fallback
// work — the user's args are appended verbatim after the prefix.
function buildChildArgs(resolution, userArgs) {
  return [...resolution.argsPrefix, ...userArgs];
}

// Core launcher logic with injectable deps for testability.
//
//   deps.spawn   — child_process.spawn-compatible (command, args, opts)
//   deps.resolve — resolveBinary-compatible (() => {command, argsPrefix})
//   deps.stderr  — writable stream for the install hint
//   deps.exit    — process.exit-compatible (code) => never returns
function run(argv, deps = {}) {
  const spawn = deps.spawn || childProcess.spawn;
  const resolve = deps.resolve || resolveBinary;
  const stderr = deps.stderr || process.stderr;
  const exit = deps.exit || process.exit;

  let resolution;
  try {
    resolution = resolve();
  } catch (err) {
    if (err instanceof ClauditorNotFoundError) {
      // Surface the actionable install hint to stderr, exit 2.
      stderr.write(`${err.message}\n`);
      return exit(ENGINE_MISSING_EXIT);
    }
    throw err;
  }

  const args = buildChildArgs(resolution, argv);
  // INHERIT stdio for interactive passthrough; inherit env so API keys
  // flow through. Argument ARRAY only (no shell: true) to prevent
  // command injection (DEC-007).
  const child = spawn(resolution.command, args, {
    stdio: "inherit",
    env: process.env,
  });

  // Guard against double-dispatch: a spawn failure can emit both "error"
  // and "exit"; only the first should drive the process exit code.
  let settled = false;
  const settle = (codeOrNull, signal) => {
    if (settled) {
      return undefined;
    }
    settled = true;
    if (codeOrNull != null) {
      return exit(codeOrNull);
    }
    // Signal-killed (code === null): propagate as a nonzero exit.
    stderr.write(`clauditor-eval: engine terminated by signal ${signal}\n`);
    return exit(1);
  };

  child.on("error", (err) => {
    if (settled) {
      return undefined;
    }
    stderr.write(`clauditor-eval: failed to launch engine: ${err.message}\n`);
    return settle(1);
  });

  child.on("exit", (code, signal) => settle(code, signal));

  return child;
}

module.exports = { run, buildChildArgs, ENGINE_MISSING_EXIT };

if (require.main === module) {
  run(process.argv.slice(2));
}
