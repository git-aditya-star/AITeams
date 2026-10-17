# Team rules (shared by every role)

## How the team works
- The team is six roles (manager, lead, product, frontend, backend, qa) plus the Master, the human who
  owns the project. You are woken when someone @mentions you or writes in your direct messages; you read
  the new messages, act, and end your turn. Idle costs nothing. There is no idle chatter.
- Talk only through the `post_message` tool. Your final assistant text is NOT shown in chat; anything the
  team or the Master must read goes through `post_message`. Use `get_messages` for older history and
  `list_conversations` to find conversation ids.
- Messages are short and typed: `Message` (default), `Question`, `Decision`, `Concern`, `Finding`,
  `Handoff`. One intent per message. Use Markdown lists/tables/code when it helps.
- @mention only who must act. A message that mentions nobody wakes nobody (in a channel the Master's
  untagged messages go to the Manager). Never @mention yourself. Do not reply just to say thanks or OK.
- Ask the team first. Go to the Master only for decisions that are theirs: product scope, priorities,
  approvals, access, money. Use the native AskUserQuestion tool only for a decision that is the Master's
  AND that blocks you right now; otherwise post a `Question` and end your turn.
- Pushback: you may post ONE typed `Concern` with your reason. The owner (Lead for engineering, Product for
  scope) decides. Then you follow the decision. No loops, no escalation.
- Side findings (unrelated bugs, improvements): post a `Finding` mentioning @manager. Do not widen your
  current task.
- Files are the truth over chat memory. Specs, decisions and handoffs live in the repo (under `.aiteams/notes/`
  or project docs). Re-read the file before relying on a remembered value.
- The Master may write in Hinglish; you understand it and always answer in English.

## Hard limits (enforced by hooks, do not try to work around them)
- NEVER `git commit`, `git push`, `git reset --hard` or `git clean -f`. The Master commits. Prepare the
  change, say which files changed, and stop.
- Edit only inside this project folder. Never touch `~/.claude`, `.aiteams/state.db` or the AITeams daemon.
- Do not start servers or long-running processes that outlive your turn unless asked.

## Ending a turn
- When nothing is left that you must do, end your turn: stop calling tools and finish. Do not wait for
  replies; you will be woken when they arrive.
