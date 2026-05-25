#!/usr/bin/env node
// clauditor-eval CLI launcher (npx clauditor / clauditor-eval bin).
//
// v1 is a SUBPROCESS BRIDGE: US-005 fills this stub to forward argv to the
// Python `clauditor` CLI (via the binary resolver from US-003) and proxy
// its exit code.
//
// Skeleton story (US-002): keep it parseable and a no-op success so the
// package installs and the bin entry is wired. Not yet implemented.
"use strict";

process.stderr.write(
  "clauditor-eval: CLI launcher not yet implemented (skeleton). " +
    "See https://github.com/wjduenow/clauditor\n",
);
process.exit(0);
