# Motion and agent-UX rules for the ISHTI app

Research sources: NN/g "[Executing UX Animations: Duration](https://www.nngroup.com/articles/animation-duration/)", "[The Role of Animation and Motion in UX](https://www.nngroup.com/articles/animation-purpose-ux/)"; ShapeofAI "[Stream of Thought](https://www.shapeof.ai/patterns/stream-of-thought)"; "[Agent UX patterns that work](https://inference.sh/blog/ux-agent-ux-patterns)".

## Rules the app follows
1. **Every animation has a job:** feedback (I heard you), state change (idle → thinking → acting → done), or orientation (where a result came from). Nothing moves just to decorate.
2. **Timing**
   - Feedback starts within 100 ms of the action.
   - Small elements animate for 120–200 ms; panels and drawers for 250–350 ms (ease-out in, ease-in out).
   - Anything that plays on every message is subtle and short. Rare events (a plan, a folder being organized) may be richer.
3. **Show the agent working, in layers**
   - Always visible: status (thinking / using a skill / waiting for you / done), via the matrix orb and the step track.
   - One click away: the reasoning, meaning why this skill (rule match or Laya scores), the plan, and each step with its result. This is the "Thinking" panel.
   - On demand: the live activity graph (request → router → skills → tools → results), with nodes lighting up in real time. Collapsed after the run.
4. **Stream, don't wait.** Tokens stream as they are generated; steps and tool calls appear as they start. A completed step gets a check and the active one pulses.
5. **Don't stream noise.** Raw page snapshots, JSON schemas and prompt text are never shown; each tool call gets a short human label.
6. **Confirmations happen in place** (delete-button pattern) instead of modal dialogs, and a consequential action always shows what will happen first.
7. **`prefers-reduced-motion`**: springs become instant fades, and the graph stops its flowing edges.

## Components (Rare UI, https://rareui.com, MIT + Commons Clause + Attribution)
matrix-orb (assistant state) · bounce-sidebar (navigation) · step-player (plan progress) · folder-component (folder skill) · task-list (reminders) · delete-button (in-place confirm) · notification-bell (fired reminders) · animated-counter (stats) · code-block (code answers) · grid-reveal (loading) · github-activity (usage heatmap).
