# Super Plan: #150 — Multi-harness: skill identity to prompt resolver (`EvalSpec.system_prompt`)

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/150
- **Parent epic:** https://github.com/wjduenow/clauditor/issues/143 (Multi-provider / multi-harness, Epic B)
- **Branch:** `feature/150-prompt-resolver`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/150-prompt-resolver`
- **Base branch:** `dev`
- **Phase:** `devolved`
- **Sessions:** 1
- **Last session:** 2026-04-29
- **Total decisions:** 13
- **PR URL:** https://github.com/wjduenow/clauditor/pull/158
- **Beads epic:** `clauditor-dnr`

---

## Discovery

### Ticket Summary

**What:** Decouple skill *identity* from skill *invocation strategy*. Today `SkillRunner.run` synthesizes `f"/{skill_name} {args}"` inline (`runner.py:280–282`) — that string is a slash command, which only Claude Code's CLI understands. Codex (under `exec`) and raw API harnesses have no slash-command discovery. Three changes:

1. Add `EvalSpec.system_prompt: str | None = None` field with load-time validation (mirrors existing `user_prompt` from #39).
2. Auto-derive `system_prompt` when unset by reading the resolved skill's `SKILL.md` body (frontmatter parsed off, body returned as-is).
3. Add `Harness.build_prompt(skill_name, args, system_prompt) → str` to the protocol so each harness owns its strategy:
   - `ClaudeCodeHarness.build_prompt` → existing `f"/{skill_name} {args}"` synthesis (ignores `system_prompt`).
   - Future `CodexHarness.build_prompt` (#149) → prepend `system_prompt` body, append `args` as user query.
4. `SkillSpec.run` resolves `system_prompt` once and threads it through to the harness.

**Why:** Prerequisite for #149 (CodexHarness). Today's slash-command synthesis is Claude-Code-specific and inlined at the seam where the harness boundary should be. Without this resolver, any non-slash-command harness has to either re-implement the synthesis or hack around it.

**Who benefits:** clauditor maintainers (unblocks #149), and downstream eval authors who want one `EvalSpec` to run across `{ClaudeCode, Codex, raw-API}` harness combinations.

**Done when (from ticket):**
1. A `SKILL.md` skill runs under Claude unchanged (back-compat).
2. The same skill, with `EvalSpec.system_prompt` auto-derived, runs under Codex producing comparable output.
3. An eval spec with explicit `system_prompt` overrides auto-derivation.
4. Tests cover both back-compat (Claude path) and the Codex path.

**Out of scope (explicit):**
- Generating an `AGENTS.md` from `SKILL.md` (Codex auto-loads `AGENTS.md` from cwd; the `system_prompt` path is the explicit alternative).
- LLM-assisted prompt rewriting per harness.
- Actual `CodexHarness` implementation lives in #149.
- New CLI flags (deferred to #151).

**Dependencies:** Blocked by #148 (`Harness` protocol — already MERGED via PR #157). Blocks #149 (Codex needs the prompt resolver).

### Codebase Findings

#### Current `Harness` protocol (post-#148)

`src/clauditor/_harnesses/__init__.py:1–79`

```python
@runtime_checkable
class Harness(Protocol):
    name: ClassVar[str]
    def invoke(self, prompt: str, *, cwd, env, timeout, model=None, subject=None) -> InvokeResult: ...
    def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]: ...
```

The protocol takes a fully-synthesized `prompt: str` and is transport-agnostic. `build_prompt` will be a new third method.

#### Inline slash-command synthesis (the seam to extract)

`src/clauditor/runner.py:280–282` (inside `SkillRunner.run`):

```python
prompt = f"/{skill_name}"
if args:
    prompt += f" {args}"
```

Then passed to `_invoke()` → `harness.invoke()` (line 393–398). Two internal callers:
- `SkillRunner.run` (above)
- `call_anthropic` at `_anthropic.py:881–882` via `asyncio.to_thread(_invoke_claude_cli, ...)` — Claude-only path; **does not need `build_prompt` plumbing** (it's a grader-side call, not skill-runner side).

#### `EvalSpec` definition

`src/clauditor/schemas.py:237–300` (fields) and `:301–775` (`from_dict` / `from_file` validation).

Existing `user_prompt: str | None = None` field (DEC-001 of #39's plan) is the **template we follow**:

```python
# field declaration (line ~252)
user_prompt: str | None = None

# from_dict validation (lines 636–643)
user_prompt = data.get("user_prompt")
if user_prompt is not None:
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        raise ValueError(
            f"EvalSpec(skill_name={skill_name!r}): user_prompt "
            f"must be a non-empty, non-whitespace string, "
            f"got {user_prompt!r}"
        )
```

#### Frontmatter parser

`src/clauditor/_frontmatter.py:1–182`

```python
def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return ``(parsed_frontmatter_dict, body_text)``."""
```

Returns the post-frontmatter body verbatim as the second tuple element. Auto-derive == call `parse_frontmatter(skill_md_text)` and use `body`. **No changes needed to `_frontmatter.py`.**

#### `SkillSpec.run` flow

`src/clauditor/spec.py:124–229`

The auto-derive insertion point is between the existing resolution steps (~line 162, after `allow_hang_heuristic` is computed) and the call to `self.runner.run(...)` at line 193. The runner's `run()` will gain a `system_prompt: str | None = None` kwarg.

#### No existing `ResolvedSkill` dataclass

Frontmatter is parsed inline at call sites (e.g., `paths.py:125–140`). Re-reading SKILL.md once per run is acceptable for a first pass; caching can land later if profiling demands it.

#### Test landscape

- `tests/test_runner.py` (155 KB) — runner tests.
- `tests/test_spec.py` (41 KB) — `SkillSpec` and `EvalSpec` tests.
- `tests/conftest.py` — `make_fake_skill_stream` factory (lines 77–130), `_eval_spec_factory` (lines 398–427), `build_eval_spec(**overrides)` (lines 563–580). The `user_prompt` test patterns there give us the template for `system_prompt` tests.

### Convention Constraints (from `.claude/rules/`)

These rules apply to #150's design. Each will become a validation criterion at the Phase 4 rules-compliance gate.

- **harness-protocol-shape** — `build_prompt` belongs on the `Harness` protocol; harnesses own their strategy. Don't inline harness-specific logic outside the protocol.
- **skill-identity-from-frontmatter** — frontmatter is the identity source; body is content. The auto-derive treats body as the prompt content; frontmatter stays untouched.
- **eval-spec-stable-ids** — `system_prompt` is a top-level scalar; if any sub-field structure is added later, each sub-entry must carry stable `id`s. Out of scope here.
- **pre-llm-contract-hard-validate** — validate `system_prompt` (non-empty, non-whitespace) at `EvalSpec.from_dict` time, not at runtime. Mirror `user_prompt`.
- **permissive-parser-strict-validator** — accept absent/None permissively; validate strictly when present.
- **path-validation** — does not currently apply (we are not accepting `@file:` references). If we later allow `system_prompt: "@file:./prompt.md"`, this rule kicks in.
- **non-mutating-scrub** — any normalization (trim trailing whitespace) must return a new string; never mutate the spec in place.
- **pure-compute-vs-io-split** — `build_prompt` is pure (string in, string out). File I/O for auto-derive lives outside the protocol method, in `SkillSpec.run`.
- **centralized-sdk-call** — N/A here; `build_prompt` does not call any SDK.
- **dual-version-external-schema-embed** — if `EvalSpec` ever exports to an external registry, `system_prompt` participates in schema versioning. Out of scope here.
- **json-schema-version** — if sidecars (e.g., `extraction.json`, `grading.json`) ever record the resolved `system_prompt`, bump `schema_version`. Out of scope unless a story explicitly adds it.

### Project Documentation Touch Points

- **`docs/eval-spec-reference.md`** — needs a new "System Prompt" section (auto-derive + override precedence).
- **`docs/architecture.md`** — needs the `Harness.build_prompt` protocol addition documented.
- **`README.md`** — light review only.

### Proposed Scope (initial)

1. New `EvalSpec.system_prompt: str | None = None` field + non-empty-string validation in `from_dict`.
2. `Harness.build_prompt(skill_name, args, system_prompt) → str` added to the protocol.
3. `ClaudeCodeHarness.build_prompt` returns `f"/{skill_name} {args}"` (ignores `system_prompt`).
4. `SkillRunner.run` accepts `system_prompt` kwarg, calls `self.harness.build_prompt(...)` instead of inline synthesis at lines 280–282.
5. `SkillSpec.run` resolves `effective_system_prompt`: explicit > auto-derived from SKILL.md body. Threads to `runner.run`.
6. A test-only `_StringPromptHarness` (or extend `MockHarness`) that exercises the `system_prompt` path so we don't ship a dormant code path waiting for #149.
7. Tests: back-compat (Claude path unchanged), explicit override path, auto-derive path, validation failure path.
8. Doc updates: `docs/eval-spec-reference.md` + `docs/architecture.md` snippets.

---

## Scoping Questions (resolved)

- **DEC-001 — `build_prompt` call site (Q1=A):** Called inside `SkillRunner.run`. Replace inline synthesis at `runner.py:280–282` with `prompt = self.harness.build_prompt(skill_name, args, system_prompt)`. Public `run()` gains `system_prompt: str | None = None` kwarg. **Rationale:** keeps the seam at the boundary the protocol was designed for; `SkillSpec.run` only owns *resolution*, not synthesis.
- **DEC-002 — Auto-derive caching (Q2=A):** Read + parse `SKILL.md` inside `SkillSpec.run` every invocation. **Rationale:** one extra read per run is negligible (skills are small markdown files); avoids new state on `SkillSpec` and avoids a `ResolvedSkill` refactor that would touch many sites.
- **DEC-003 — Frontmatter override (Q3=A):** Two-level precedence only. `EvalSpec.system_prompt` (explicit) wins; otherwise auto-derive from `SKILL.md` body. No frontmatter `system_prompt:` key. **Rationale:** keep the surface minimal; if frontmatter override is wanted later, it's a localized addition to the resolver.
- **DEC-004 — ClaudeCodeHarness with explicit `system_prompt` (Q4=A):** Silent ignore. `ClaudeCodeHarness.build_prompt` returns `f"/{skill_name} {args}"` regardless of the `system_prompt` arg. **Rationale:** matches ticket text; cross-harness specs should run without per-harness friction.
- **DEC-005 — Future-harness test coverage (Q5=A):** Add a test-only `_StringPromptHarness` (or extend the existing test fake) that uses `system_prompt` as the prompt body, so the protocol shape is exercised end-to-end before #149 ships. **Rationale:** prevents shipping a dormant code path; gives #149 a known-good reference.

---

## Architecture Review

| Area | Rating | Summary |
|---|---|---|
| Security | **pass** | Inputs are trusted developer files (SKILL.md, eval.json). No new traversal surface. Existing `SKILL_NAME_RE` guard at `paths.py` is defense-in-depth. Optional: length cap on `system_prompt` to prevent accidental memory bloat. |
| Performance | **pass** | Measured: ~0.013 ms read+parse for a 6.5 KB SKILL.md. Per-run overhead ~0.001% relative to subprocess startup. Variance worst case (5 sequential runs) totals ~0.065 ms. DEC-002 (re-read per run) is fine. |
| API + Data Model | **concern** | (1) `build_prompt` should be keyword-only on `system_prompt` to match the codebase style for optional kwargs. (2) `MockHarness` already exists at `_harnesses/_mock.py` and **must** gain `build_prompt` — otherwise it stops satisfying the runtime-checkable protocol. (3) Kwarg order in `runner.run` for `system_prompt` is up for review. (4) Whether `SkillResult` should record the resolved `system_prompt` for audit/replay is open. (5) `EvalSpec` has no `schema_version`; sidecars do not embed `EvalSpec`. No version bumps. |
| Observability | **concern** | (1) When `SkillSpec.run` auto-derives, missing/unreadable `SKILL.md` raises an unwrapped `FileNotFoundError`/`ValueError`; should be wrapped with a friendly stderr message. (2) Should we emit a one-line stderr "system_prompt source = explicit-eval-spec | auto-derived-from-body | not-set" tag (label only, not content)? Useful for cross-harness debugging once #149 lands. |
| Testing | **pass** | Mature infrastructure exists: `test_schemas.py:1418–1756` has the `user_prompt` validation suite that we mirror; `tmp_skill_file` fixture in `conftest.py:432–485` writes modern-layout `.claude/skills/<n>/SKILL.md`; `MockHarness` at `_harnesses/_mock.py` is the harness fake. `TestHarnessProtocol` (`test_runner.py:165–226`) is a structural-conformance drift-guard we'll need to update for `build_prompt`. ~100 LoC of new tests across 4 files. Coverage gate (`--cov-fail-under=80`) will pass. |

### Key concerns (carry into refinement)

- **C-API-1 — Keyword-only `system_prompt` on `build_prompt`?** Ticket text shows positional ordering. Codebase style for optional kwargs is keyword-only (e.g., `invoke`'s `model`, `subject`).
- **C-API-2 — `MockHarness` is a real implementation, not just a test toy.** It lives at `src/clauditor/_harnesses/_mock.py` and is referenced from production code (per the API+Data Model report). Adding `build_prompt` to the protocol requires implementing it on `MockHarness` *in this PR* — non-negotiable.
- **C-API-3 — `SkillResult` audit field.** Should the resolved `system_prompt` be recorded on `SkillResult` so an audit log can reconstruct the exact request? Ticket is silent.
- **C-API-4 — Kwarg position of `system_prompt` in `runner.run`.** Place it last (after `allow_hang_heuristic`) or grouped with prompt-related fields? Existing call sites all use kwargs, so no positional breakage.
- **C-OBS-1 — Wrap auto-derive errors.** When SKILL.md is missing or has malformed frontmatter, surface a user-friendly error that names the skill, instead of a raw stack trace.
- **C-OBS-2 — Source-label stderr log.** Emit `"clauditor: system_prompt source = <label>"` to stderr, label-only, no content. Helps debug cross-harness specs.
- **C-SEC-1 — Length cap on `system_prompt`.** Optional defensive measure (e.g., reject > 100 KB at `from_dict` time). Ticket doesn't require it; cheap to add.

No blockers. All concerns are decisions to make before story breakdown.

---

## Refinement Log

- **DEC-006 — `build_prompt` parameter style (Q6=A):** Keyword-only on `system_prompt`. Final shape: `def build_prompt(self, skill_name: str, args: str, *, system_prompt: str | None) -> str`. **Why:** matches codebase convention for optional kwargs (e.g., `invoke`'s `model`, `subject`); call sites become self-documenting.
- **DEC-007 — `SkillResult` audit field (Q7=A):** Do not add. Recording the resolved `system_prompt` on `SkillResult` is deferred to #154 (per-iteration harness context sidecar). **Why:** keeps #150 focused on resolution; sidecar/audit shape belongs to its own ticket.
- **DEC-008 — `runner.run` kwarg position (Q8=A):** `system_prompt` lands last, after `allow_hang_heuristic`. **Why:** stable anchor for existing test mocks; "new optional kwargs go at the end" is consistent with prior changes here.
- **DEC-009 — Auto-derive error handling (Q9=A):** Wrap and re-raise. When `SkillSpec.run` auto-derives and the read or `parse_frontmatter` fails, raise `RuntimeError(f"clauditor.spec: failed to auto-derive system_prompt for skill {skill_name!r} from {skill_path}: {orig}")` with the original exception chained via `from`. **Why:** raw `FileNotFoundError`/`ValueError` deep in `_frontmatter` doesn't tell the user which skill/path is broken.
- **DEC-010 — Stderr source-label log (Q10=B):** No log emitted in #150. **Why:** silent today; cheap to add later if cross-harness debugging (#149) shows it would help.
- **DEC-011 — `system_prompt` length cap (Q11=B):** No cap. **Why:** ticket doesn't require it; trust developer input; can add defensively later.
- **DEC-012 — `MockHarness` must implement `build_prompt`:** Non-negotiable. `MockHarness` lives in `src/clauditor/_harnesses/_mock.py` and is referenced from production code, so the protocol addition forces an implementation here. The `MockHarness.build_prompt` records the inputs (so tests can assert what was passed) and returns a deterministic concatenation that includes `system_prompt` when present (so the e2e tests for the future Codex path can verify threading).
- **DEC-013 — `TestHarnessProtocol` drift-guard updated:** `tests/test_runner.py:165–226` enforces structural conformance to the protocol via `inspect.signature`. The drift-guard must be updated to expect the new `build_prompt` member, including a signature check for `(skill_name: str, args: str, *, system_prompt: str | None) -> str`.

---

## Detailed Breakdown

### Validation command (used as AC suffix on every implementation story)

```bash
uv run ruff check . && uv run pytest --cov-fail-under=80
```

---

### US-001 — Add `build_prompt` to `Harness` protocol; implement on `ClaudeCodeHarness` and `MockHarness`

**Description:** Extend the `Harness` protocol with a pure prompt-builder method that lets each harness own its identity-to-prompt strategy. Implement it on the two existing harnesses (`ClaudeCodeHarness` keeps slash-command synthesis; `MockHarness` records inputs and returns a deterministic concatenation). Update the drift-guard test to expect the new member.

**Traces to:** DEC-001, DEC-004, DEC-006, DEC-012, DEC-013.

**Files:**
- `src/clauditor/_harnesses/__init__.py` — add `build_prompt(self, skill_name: str, args: str, *, system_prompt: str | None) -> str` to the `Harness` Protocol.
- `src/clauditor/_harnesses/_claude_code.py` — implement `build_prompt`: returns `f"/{skill_name}"` when `args == ""`, else `f"/{skill_name} {args}"`. Ignores `system_prompt`.
- `src/clauditor/_harnesses/_mock.py` — implement `build_prompt`: record `(skill_name, args, system_prompt)` on a list (`build_prompt_calls`) and return a deterministic string that includes `system_prompt` when present (e.g., `f"[mock]{system_prompt or ''}|/{skill_name} {args}".rstrip()`). The recorded calls are what tests assert against.
- `tests/test_runner.py` — update `TestHarnessProtocol` (lines 165–226) to require `build_prompt` and check its signature via `inspect.signature`.

**TDD:**
- `test_claude_code_harness_build_prompt_with_args_and_no_system_prompt` — `("foo", "bar baz", system_prompt=None)` → `"/foo bar baz"`.
- `test_claude_code_harness_build_prompt_no_args` — `("foo", "", system_prompt=None)` → `"/foo"`.
- `test_claude_code_harness_build_prompt_ignores_system_prompt` — `("foo", "", system_prompt="anything")` → `"/foo"`.
- `test_mock_harness_build_prompt_records_call` — `MockHarness.build_prompt("foo", "bar", system_prompt="hello")` appends to `build_prompt_calls` and the recorded entry contains all three values.
- `test_harness_protocol_includes_build_prompt` — drift-guard asserts the method exists and has the keyword-only `system_prompt` parameter typed `str | None`, return type `str`.

**Acceptance criteria:**
- All five TDD tests pass.
- Existing `TestHarnessProtocol` continues to pass (with the updated expectation).
- `uv run ruff check . && uv run pytest --cov-fail-under=80` is green.

**Done when:** Protocol exposes `build_prompt`; both shipping harnesses implement it; drift-guard is updated; tests pass.

**Depends on:** none.

---

### US-002 — Add `EvalSpec.system_prompt` field with load-time validation

**Description:** Add an optional `system_prompt: str | None = None` field to `EvalSpec`, mirroring the existing `user_prompt` field's shape and validation. Place it adjacent to `user_prompt` for grouping. Mirror the existing `user_prompt` test suite for the validation paths.

**Traces to:** DEC-003 (no frontmatter override path on `EvalSpec`), DEC-011 (no length cap).

**Files:**
- `src/clauditor/schemas.py` — add `system_prompt: str | None = None` to the `EvalSpec` dataclass field list immediately after `user_prompt` (~line 252). Add validation in `from_dict` immediately after the `user_prompt` validation block (~line 644): same `isinstance(str) and strip()` check, same error-message style. Add a corresponding entry in `to_dict` (omit when `None`, emit when set), parallel to how `user_prompt` is serialized.
- `tests/test_schemas.py` — mirror the `user_prompt` validation suite (lines 1418–1756) for `system_prompt`.

**TDD:**
- `test_from_file_loads_system_prompt` — eval.json with `"system_prompt": "you are helpful"` round-trips into the dataclass.
- `test_from_file_system_prompt_absent_defaults_to_none` — eval.json without the key produces `system_prompt is None`.
- `test_from_file_system_prompt_empty_string_rejected` — `"system_prompt": ""` raises `ValueError` with skill name in message.
- `test_from_file_system_prompt_whitespace_only_rejected` — `"system_prompt": "   "` raises.
- `test_from_file_system_prompt_non_string_rejected` — `"system_prompt": 42` raises.
- `test_to_dict_omits_system_prompt_when_unset` — round-trip an `EvalSpec` with `system_prompt=None`; the output dict has no `system_prompt` key.
- `test_to_dict_emits_system_prompt_when_set` — round-trip with `system_prompt="hi"`; output dict has the field.
- Extend `make_eval_spec` / `build_eval_spec` factories in `conftest.py` to accept a `system_prompt` kwarg.

**Acceptance criteria:**
- All seven TDD tests pass.
- The factories accept `system_prompt`.
- `uv run ruff check . && uv run pytest --cov-fail-under=80` is green.

**Done when:** `EvalSpec` carries `system_prompt`; loader validates it; serializer round-trips it; tests cover absent/valid/empty/whitespace/non-string.

**Depends on:** none.

---

### US-003 — Wire `system_prompt` through `SkillRunner.run` via `harness.build_prompt`

**Description:** Replace the inline `f"/{skill_name} {args}"` synthesis at `runner.py:280–282` with a call to `self.harness.build_prompt(...)`. Add `system_prompt: str | None = None` as the last keyword argument on `SkillRunner.run` (after `allow_hang_heuristic`). Thread it to `_invoke` only as far as needed to call `build_prompt` — it does not need to land on `InvokeResult`.

**Traces to:** DEC-001, DEC-007, DEC-008.

**Files:**
- `src/clauditor/runner.py` — `SkillRunner.run` (line 247): add `system_prompt: str | None = None` as last kwarg. Replace lines 280–282 with `prompt = self.harness.build_prompt(skill_name, args, system_prompt=system_prompt)`. Audit `_invoke` to ensure it still passes `prompt` through unchanged.
- `tests/test_runner.py` — add tests that assert `build_prompt` is called with the right args and that `system_prompt` propagates from `runner.run` kwargs.

**TDD:**
- `test_runner_run_calls_harness_build_prompt_with_args` — `MockHarness` records the call when `runner.run("foo", "bar")` is invoked; assert `("foo", "bar", system_prompt=None)`.
- `test_runner_run_threads_system_prompt_kwarg_to_build_prompt` — `runner.run("foo", "bar", system_prompt="hello")` records `("foo", "bar", system_prompt="hello")`.
- `test_runner_run_passes_built_prompt_to_invoke` — assert the prompt string returned by `MockHarness.build_prompt` is what reaches `MockHarness.invoke`.
- `test_runner_run_back_compat_no_system_prompt_kwarg` — call `runner.run("foo", "bar")` with no `system_prompt`; ClaudeCodeHarness path produces `"/foo bar"` exactly (back-compat).

**Acceptance criteria:**
- Four TDD tests pass.
- All existing `runner.run` tests pass without modification (back-compat).
- `uv run ruff check . && uv run pytest --cov-fail-under=80` is green.

**Done when:** Runner uses `build_prompt`; the `system_prompt` kwarg threads cleanly; back-compat is proven.

**Depends on:** US-001 (needs `build_prompt` on the protocol and `MockHarness`).

---

### US-004 — Resolve and thread `system_prompt` in `SkillSpec.run` (auto-derive + explicit override + friendly errors)

**Description:** In `SkillSpec.run`, compute `effective_system_prompt`: explicit (`self.eval_spec.system_prompt`) wins; otherwise read `self.skill_path` and use the body returned by `_frontmatter.parse_frontmatter` as the auto-derived value. Pass `system_prompt=effective_system_prompt` to `self.runner.run(...)`. Wrap read/parse failures with a `RuntimeError` that names the skill and path, chained from the original.

**Traces to:** DEC-002, DEC-003, DEC-009.

**Files:**
- `src/clauditor/spec.py` — in `SkillSpec.run` (~line 162, after `allow_hang_heuristic` resolution and before the `self.runner.run(...)` call at ~line 193): add the resolution block. Wrap the file read + `parse_frontmatter` call in a `try/except (FileNotFoundError, OSError, ValueError)` and re-raise a `RuntimeError(f"clauditor.spec: failed to auto-derive system_prompt for skill {self.skill_name!r} from {self.skill_path}: {exc}") from exc`.
- `tests/test_spec.py` — e2e tests using `tmp_skill_file` fixture (`conftest.py:432–485`) and `MockHarness`.

**TDD:**
- `test_skill_spec_run_auto_derives_system_prompt_from_body` — write a `SKILL.md` with frontmatter and a body; construct `SkillSpec` with no explicit `system_prompt`; call `run("args")`; assert `MockHarness.build_prompt_calls[-1].system_prompt` equals the body string.
- `test_skill_spec_run_explicit_eval_spec_system_prompt_wins` — `SKILL.md` has body "BODY"; `EvalSpec.system_prompt = "EXPLICIT"`; assert recorded call uses `"EXPLICIT"`.
- `test_skill_spec_run_no_system_prompt_when_eval_spec_explicitly_empty_body` — `SKILL.md` body is empty (frontmatter only); auto-derive yields empty string; the empty-body case threads through as `""` (allowed; falsy values do not trigger fallback). *(Documents the edge case rather than masking it.)*
- `test_skill_spec_run_missing_skill_md_raises_friendly_error` — `skill_path` points at a non-existent file; assert `RuntimeError` is raised with `skill_name` and the path in the message; assert `__cause__` is a `FileNotFoundError`.
- `test_skill_spec_run_malformed_frontmatter_raises_friendly_error` — write a `SKILL.md` with broken frontmatter (`---` opening with no closing); assert `RuntimeError` with skill name and path; `__cause__` is a `ValueError`.

**Acceptance criteria:**
- Five TDD tests pass.
- Existing `SkillSpec.run` integration tests continue to pass (back-compat under `ClaudeCodeHarness` is preserved because the resolved `system_prompt` is ignored on that path).
- `uv run ruff check . && uv run pytest --cov-fail-under=80` is green.

**Done when:** `SkillSpec.run` resolves `system_prompt` correctly under all three branches (explicit, auto-derive, error); friendly error wrapping is in place.

**Depends on:** US-002 (needs the field), US-003 (needs the runner kwarg).

---

### US-005 — Documentation updates

**Description:** Document the new `system_prompt` field, its precedence, and the auto-derive behavior in user-facing docs. Document the protocol addition in architecture docs.

**Traces to:** DEC-001, DEC-002, DEC-003, DEC-004.

**Files:**
- `docs/eval-spec-reference.md` — add a "System Prompt" subsection covering: (1) what it is, (2) the two-level precedence (explicit > auto-derived from `SKILL.md` body), (3) the validation rules (non-empty, non-whitespace string when set), (4) which harnesses use it (Codex via #149) vs ignore it (ClaudeCode).
- `docs/architecture.md` — add a paragraph documenting `Harness.build_prompt` as the third protocol member, with a one-line description of each shipping implementation's strategy.
- `CHANGELOG.md` — under `[Unreleased]`, add a bullet under "Added" for `EvalSpec.system_prompt` and `Harness.build_prompt`.

**Acceptance criteria:**
- All three docs updated.
- `uv run ruff check . && uv run pytest --cov-fail-under=80` is green.

**Done when:** Reference docs explain the field; architecture doc explains the protocol; changelog records the addition.

**Depends on:** US-001, US-002, US-003, US-004.

---

### US-006 — Quality Gate (code review × 4 + CodeRabbit)

**Description:** Run an in-process code reviewer four times across the full diff for #150 (everything since branch base `dev`). Fix every real bug surfaced on each pass. Run CodeRabbit if available. Validation must pass after all fixes.

**Acceptance criteria:**
- Four code-review passes complete.
- All real bugs flagged on each pass are fixed (with rationale recorded for any flag rejected as a false positive).
- CodeRabbit pass complete (or noted as unavailable).
- `uv run ruff check . && uv run pytest --cov-fail-under=80` is green after all fixes.

**Done when:** Four passes done, fixes committed, validation green.

**Depends on:** US-001, US-002, US-003, US-004, US-005.

---

### US-007 — Patterns & Memory (priority 99)

**Description:** Distill any reusable patterns from #150 into `.claude/rules/` and update relevant memory. Likely candidates if patterns surface during implementation:
- A rule on "prompt-resolver lives in `SkillSpec`, harness owns the synthesis" — refines `harness-protocol-shape`.
- A note on `MockHarness` being production-grade (not just a test fake) — informs future protocol additions.

If no new patterns emerge, this story explicitly records "no new rules" and verifies existing rules still apply.

**Acceptance criteria:**
- Walk through `.claude/rules/` and update `harness-protocol-shape` if needed.
- Update memory if any user/feedback-level patterns emerged.
- Document the empty case explicitly if nothing new.

**Done when:** Rules audit done, decisions recorded.

**Depends on:** US-006.

---

### Story dependency graph

```
US-001 ─┬─> US-003 ──┐
US-002 ─┴─> US-004 ──┴─> US-005 ──> US-006 ──> US-007
```

### Rules-compliance gate (validation)

- ✅ `harness-protocol-shape` — `build_prompt` is added on the protocol; harnesses own their strategy. (US-001)
- ✅ `pure-compute-vs-io-split` — `build_prompt` is pure; file I/O for auto-derive lives in `SkillSpec.run`, not in the protocol method. (US-001, US-004)
- ✅ `pre-llm-contract-hard-validate` — `system_prompt` validated at `EvalSpec.from_dict` time. (US-002)
- ✅ `permissive-parser-strict-validator` — `None` accepted permissively; non-empty-string strict when present. (US-002)
- ✅ `non-mutating-scrub` — no normalization mutates the spec; `system_prompt` is stored as-given. (US-002)
- ✅ `centralized-sdk-call` — `build_prompt` does not call any SDK. (US-001)
- ✅ `skill-identity-from-frontmatter` — frontmatter is identity; body is content; auto-derive uses body, not identity. (US-004)

---

## Beads Manifest

- **Epic:** `clauditor-dnr` — #150: skill identity to prompt resolver (EvalSpec.system_prompt)
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/150-prompt-resolver`
- **Branch:** `feature/150-prompt-resolver`
- **PR:** https://github.com/wjduenow/clauditor/pull/158

### Tasks

| ID | Story | Priority | Blocked by |
|---|---|---|---|
| `clauditor-dnr.1` | US-001 — Add `build_prompt` to `Harness` protocol; implement on `ClaudeCodeHarness` and `MockHarness` | P1 | — |
| `clauditor-dnr.2` | US-002 — Add `EvalSpec.system_prompt` field + validation | P1 | — |
| `clauditor-dnr.3` | US-003 — Wire `system_prompt` through `SkillRunner.run` via `harness.build_prompt` | P1 | `.1` |
| `clauditor-dnr.4` | US-004 — Resolve and thread `system_prompt` in `SkillSpec.run` (auto-derive + override + friendly errors) | P1 | `.2`, `.3` |
| `clauditor-dnr.5` | US-005 — Documentation updates | P2 | `.1`, `.2`, `.3`, `.4` |
| `clauditor-dnr.6` | US-006 — Quality Gate (code review × 4 + CodeRabbit) | P2 | `.1`, `.2`, `.3`, `.4`, `.5` |
| `clauditor-dnr.7` | US-007 — Patterns & Memory | P3 | `.6` |

### Next steps

1. Run Ralph: `/ralph-run`
2. Monitor: `bd list --status=in_progress` (or `bd ready` to see what's claimable)
3. When done: `/closeout`
