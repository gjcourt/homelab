---
status: Proposed
last_modified: 2026-11-08
summary: "Agent sessions prototype B: the SANDBOX-8 session as Matrix rooms in Element X (plan step 1)"
---

# Agent sessions, shape B: Matrix + Element X

Round 1 of the low-fidelity prototype for build step 1 of the
[agent-sessions plan](../../plans/2026-11-08-agent-sessions-plan.md). The plan asks for two
clickable mocks so George can pick the session shape by trying both (its open question 1):

- **A:** `docs/ui/sessions/prototype-a-r1.html` in gjcourt/plane-mobile, a Sessions pane in
  plane-mobile, with the full task analysis in its README.
- **B, this one:** [`prototype-b-r1.html`](prototype-b-r1.html), the same session as it would look
  in Element X.

Both replay the same real session, so the only difference between them is the shape. This is an
Element X **look-alike**, not the real app. How Element X really renders edited, pinned and
threaded messages is checked on the phone at plan step 3B.2.

## Task analysis (short; the full one is in A's README)

George, on his phone and mostly away from a desk, brings these questions, most frequent first:

| # | Question | What answers it in B |
| --- | --- | --- |
| 1 | Is anything waiting on me? | The room list: the session room is named `❓ SANDBOX-8 · …` and carries an `@` mention badge; the push |
| 2 | What is it doing? | The pinned status message (banner and message), edited at most every 30 s; the activity thread under it |
| 3 | Did my message land? | A ✅ reaction from `desk` naming when the agent got it (a prototype suggestion, not in the plan) |
| 4 | Is the PR green? | A line in the status message; the PR link in the agent's message |
| 5 | Can I start one from here? | The `desk` DM: free text, a proposal, then `go` |

**Tasks for the evaluation** (plan step 1's exit). George does each one unprompted, and where he
hesitates is written down:

1. Find what's waiting on you.
2. Answer a question an agent asked you.
3. Start a new session.

## What the prototype shows

- **Chats (the room list) is the session list.**
  - One room per session. The bot keeps the state in the room's name: ⏳ working, ❓ waiting on
    you, 👀 in review, ✓ finished. The plan names ⏳ and ✓; ❓ and 👀 are this prototype's.
  - Unread and mention badges, and the `desk` DM, are in the same list.
- **The session room:**
  - **A pinned status message**, shown as Element X's pinned banner and as the message itself. It
    is edited in place (`m.replace`) at most every 30 s, and shows:
    - the state and elapsed time;
    - the current action and the last three;
    - the task count and the next task;
    - the PR and its CI;
    - the cost.
  - **The activity thread** under the status message: one reply per batch of tool calls, at most
    every 10 s.
  - **The chat:** agent-desk's Started line, George's messages, and the agent's messages. The
    question mentions George, so it is highlighted and pushes.
- **Starting a session**, in the `desk` DM:
  1. George writes what he wants.
  2. The bot proposes `PROJECT · playbook · title`. George can change it with `project …`,
     `playbook …` or `title …`, or by writing the task again.
  3. Exactly `go` starts it. The bot creates the ticket and invites George to the new room, which
     he accepts.
- **Notifications:** simulated Element X pushes for the question and for In review.

### Defaults taken from the plan's recommendations

These follow the plan's recommendations. They are **not decisions**, and the prototype labels them
where they show:

- question 4: push through Element's gateway with `event_id_only`;
- question 6: one bot user, `desk`;
- question 7: free text, a proposal, and `go`;
- question 8: every tool call, in the thread;
- question 10: a session ends at Done or Cancelled, and a message to a finished room wakes it.

## What building B found

Questions for the plan, found while building B:

1. **Notices, not messages, for progress.** George and the bot are the room's only two members.
   Element's default push rules treat a two-person room like a DM and push every message, so a
   thread reply every 10 s would buzz the phone. Status and activity should go as `m.notice`,
   which the default rule `.m.rule.suppress_notices` keeps silent. The question stays an `m.text`
   that mentions George, so it still pushes. The plan doesn't say which message type the bot uses.
2. **The status message lags.** Edited at most every 30 s, it can still say "Working" right
   after the room name and the question say the agent is waiting. In the replay, the question
   lands 23 s after the Job starts, before the first edit is due. One option: an edit on a state
   change doesn't count against the 30 s.
3. **The new room is an invite.** Matrix clients don't join rooms by themselves, so a session
   started from the DM costs an extra Accept tap.
4. **"Did my message land?" has no place in the plan for B.** The prototype uses a reaction from
   `desk` when the Job receives a message. The plan could adopt it.

## Data: the SANDBOX-8 replay

SANDBOX-8 ("Improve the docs") is replayed from its real run on 2026-10-24, the phase-1 step-5b
test the plan names. Source: the run record `ad-sandbox-8-20261024-0d545a` on hestia
(`meta.json`, `result.json`, the transcript) and
[agent-sandbox#3](https://github.com/gjcourt/agent-sandbox/pull/3).

- Real: every tool call and its time, the comments, the state changes, the PR, the
  `lint-and-test` result at 23:17:07 UTC, the run time and the $0.69 cost over 36 turns.
- Each action is the tool's name plus the plan's fixed-form summary (the path, or a Bash command's
  first line cut to 120 characters), with no tool output. Nothing in these commands is secret.
- Not in the run, and labelled:
  - the task list: the agent wrote none, so the docs-fix playbook's steps stand in;
  - the live cost: the run record has only the total.
- **The replay waits for George** at `bench-ticket wait`, and resumes 7 s after his answer, as the
  real run did. Its later steps are the real run's whatever he writes, and the prototype says so.
- SANDBOX-6 and SANDBOX-1 are real sessions shown without their activity.

The replay data is identical to A's, so the two can be compared step for step.

## Using it

Open `prototype-b-r1.html` on the phone. The striped strip at the top is a prototype aid, not part
of the design:

- ▶ / ❚❚ plays and pauses; 1× / 4× / 16× sets the speed;
- **Tasks** lists the three tasks and ticks them as you do them;
- **⋯** jumps to the question, to the PR, or to the end, answers as George did, starts over, and
  switches the theme.

## UI.md review checklist

Some items are limited by Element X's design, which we don't control.

- [x] **The main task works for a first-time user without explanation.** Each task is checked by
  walking it in a 390 × 844 headless Chromium, light and dark.
  - Waiting shows in the room name, the `@` badge and the push.
  - An answer is an ordinary reply in the room.
  - Starting needs knowing that the `desk` DM exists and that `go` confirms. The DM's first message
    says both.
- [x] **The default view answers the most common question.** The room list shows ❓ and `@` on the
  waiting room. It's sorted by recency, though, not by need, which is Element X's choice.
- [x] **Every action gives visible feedback, and slow ones show progress.**
  - A sent message appears at once, then gets the ✅ reaction with the time the agent received it.
  - The status message and thread show progress. The status can lag by up to 30 s, and says so.
- [x] **Nothing destructive happens without undo or a clear preview.** The proposal shows the
  project, repo, playbook, model and cap before `go`. There's no undo after `go`, since that's
  B's confirm.
- [x] **Missing, estimated and uncertain values are labelled, never shown as 0 or blank.**
  - "PR: none yet".
  - "Cost: shown when the run ends".
  - The stand-in task list and the keyword-guess proposal say what they are.
- [x] **Names, units and colours are consistent across every screen.** The state emoji and names
  are the same in the room names, the status message and the pushes.
- [x] **Each screen has one clear purpose and a visible hierarchy.** That hierarchy is mostly
  Element X's:
  - the room list answers "what needs me?";
  - the room answers "what is it doing?" (pinned status) and holds the chat;
  - the thread holds the detail.
- [x] **Phone width, dark mode, keyboard, screen reader basics.**
  - Built at 390 px, light and dark.
  - Tap targets are at least 44 px.
  - Rooms and controls are buttons; badges have labels.
  - Sheets close with Escape.
- [ ] **George has tried it on a real task.** Not yet. This is the prototype for that evaluation.
