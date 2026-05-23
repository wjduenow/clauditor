---
name: background-task-fanout
description: TEST-ONLY known-bad fixture skill that deliberately launches background sub-agents and exits without polling them, to exercise clauditor's background-task non-completion detector (#97). NOT a user-facing skill.
disable-model-invocation: true
allowed-tools: Task
---

# /background-task-fanout — known-bad background-task fan-out (TEST FIXTURE)

This skill is a **deliberate anti-pattern**. It exists only to make
clauditor's background-task non-completion heuristic
(`_detect_background_task_noncompletion`, GitHub #97) fire end-to-end in
a live test. It must NEVER be packaged into `src/clauditor/skills/`,
installed by `clauditor setup`, or shown in any user-facing skill list.

The bad pattern it reproduces: a skill launches several
`Task(run_in_background=true)` sub-agents and then immediately summarizes
and exits **without polling** any of them. Under `claude -p`, background
tasks are not polled, so their output is silently truncated — the run
ends with a valid `result` message and no error, but the work the
sub-agents were doing never completes.

## Workflow

Do exactly the following, in order. Do NOT poll, wait for, or read the
results of any background task. Do NOT use the BashOutput tool. Exit as
soon as you have launched the tasks and printed the summary.

1. Launch **three** background sub-agents using the `Task` tool with
   `run_in_background: true`. Give each one a trivial, bounded task that
   needs no network access or file edits, for example:
   - Task 1: "Count from 1 to 5 and report the final number."
   - Task 2: "Name three primary colors."
   - Task 3: "State whether 7 is a prime number."

   Each `Task` call MUST set `run_in_background: true`. Launch all three
   before doing anything else.

2. Immediately after launching the three tasks — without polling, waiting
   on, or reading any of their results — print a one-line summary such as:

   "Launched 3 background agents; continuing in the background."

3. Stop. Do not poll the tasks. Do not call BashOutput. Do not wait for
   the sub-agents to finish. End the run here.
