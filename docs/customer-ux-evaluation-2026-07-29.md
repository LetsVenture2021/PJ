# Customer UX evaluation (July 29, 2026)

## Executive summary

PJ presents a capable, reassuring workspace once a session is running. Its three
interaction modes, durable history, visible capability state, upload controls,
structured-output option, and explicit approval UI make the product feel more
like a governed work assistant than a generic chat box. The strongest part of
the experience is that advanced functionality is available in one place without
hiding consequential actions.

The largest customer problem occurs before that value is reached. The default
experience leads with **Fast Voice**, a server URL, and a required **Start
Session** action while the message composer is disabled. This makes a familiar
chat task feel like infrastructure setup. The empty conversation does not
explain what PJ can do or offer a first prompt, and the difference between Fast
Voice, Full Power Voice, and Full Power Text requires the customer to interpret
product-specific terminology. A new customer can reasonably wonder whether
they selected the right mode, why typing is unavailable, and whether they are
expected to understand the capability catalog.

**Overall assessment:** strong power-user utility and safety signaling, but a
high-friction first-run experience. The first product iteration should make
text immediately usable, move connection details out of the primary journey,
and teach the mode trade-offs at the point of choice.

## Evaluation scope and method

I approached PJ as a first-time customer trying to start a conversation, learn
what the assistant can do, continue earlier work, and understand failure states.
On the local Flask application I:

1. Opened the live root route and reviewed the initial workspace.
2. Checked the live health and capability experiences.
3. Started a Full Power conversation and verified that it appeared in history.
4. Reviewed the upload, structured-output, transcript, and approval journeys in
   the shipped client.
5. Tried the documented one-shot terminal journey without credentials, as a
   customer who had missed or not completed setup.

The environment intentionally had no usable provider credentials, browser
automation, microphone, or audio devices. Therefore this evaluation does not
score speech recognition, audio latency, model answer quality, responsive
visual rendering, or a complete provider-backed turn. Findings about controls
and customer-facing states are based on the live locally served client; code
review was used only to inspect states that could not be reached without those
dependencies.

## Customer journey observations

### 1. Arrival and orientation

The two-column workspace is easy to parse: controls and system state are on the
left, while the conversation and composer occupy the main area. The product
name, idle state, mode selector, and primary session action are visible without
navigation.

However, the customer arrives at an empty conversation with Fast Voice selected
and the composer disabled. There are no example tasks, welcome explanation, or
empty-state call to action. The most prominent concepts are “Server URL,”
“Start Session,” and “Refresh Capabilities,” which describe runtime mechanics
rather than the customer's goal.

### 2. Choosing how to work

Offering voice and text in the same workspace is valuable, and the note below
the controls changes with the selected mode. “Full Power Text” communicates
that the mode has broader tools, citations, and history.

The choices nevertheless impose terminology before value. “Fast” and “Full
Power” are not a complete decision model: they do not state relative latency,
tool availability, cost, persistence, or privacy in a scannable form. Fast
Voice being the default also brings microphone permission into the first-run
path even for customers who only intend to type.

### 3. Starting and continuing a conversation

Starting a Full Power session produced a durable conversation that appeared in
history. Search, refresh, and resume controls make continuity discoverable, and
history entries expose title, channel, and message count. These are meaningful
advantages for ongoing work.

Requiring a session to be started before the composer becomes available adds a
step that most chat customers will not expect. “Start Session” also sounds more
technical and voice-oriented than “New chat.” The separate start/end model and
conversation selector create ambiguity about whether ending a session saves,
closes, or deletes work.

### 4. Advanced work and trust

Uploads are placed next to the composer, with separate accessible buttons for
files and folders. Upload progress and terminal errors are shown inside the
conversation, keeping feedback close to the action. Structured JSON output is
progressively disclosed in a collapsed panel rather than dominating the
default experience.

The capability summary and tool catalog offer useful operational transparency,
but “Capability mode,” counts of local functions, bridge configuration, and raw
catalog status are administrator language. Customers primarily need a concise
answer to “What can PJ do right now?” with unavailable features explained only
when relevant.

Approval cards are a notable trust strength: they name the target, show the
arguments, and require an explicit approve/reject decision. This is much better
than allowing consequential tools to run invisibly. Showing raw arguments can,
however, be difficult for nontechnical customers; a plain-language impact
summary should precede the details.

### 5. Errors and recovery

The web client generally converts failures into visible system bubbles and
includes bounded details instead of failing silently. Uploads include progress,
stall detection, and specific terminal states. This is excellent feedback for
long-running operations.

Some error content exposes implementation language such as bridge state, WAF
rules, endpoint status, request IDs, and protocol failures. Those details are
useful for support but should sit behind “Technical details.” In the terminal,
missing credentials currently result in a Python traceback. A customer needs a
short setup instruction and a clean nonzero exit, not SDK internals.

## Strengths

1. **Clear workspace structure.** Status and session controls are separated
   from the conversation without hiding either.
2. **One surface for multiple workflows.** Voice, text, history, files, folders,
   and structured output coexist without requiring separate products.
3. **Strong continuity.** Durable conversations, search, resume, and transcript
   copy support longer-running customer work.
4. **Good action feedback.** System bubbles, upload progress, tool-status rows,
   and interruption states reduce uncertainty during asynchronous work.
5. **Thoughtful safety UX.** Consequential actions are explicitly approved or
   rejected, and capability degradation is visible rather than silently
   misrepresented.
6. **Progressive disclosure for specialist controls.** JSON schema controls are
   collapsed by default, keeping an expert feature available without consuming
   the primary canvas.
7. **Accessible intent on upload icons.** File and folder buttons have both
   tooltips and `aria-label` text rather than relying on icons alone.

## Weaknesses and recommended improvements

| Priority | Finding | Customer impact | Recommendation | Success signal |
| --- | --- | --- | --- | --- |
| P0 | The default composer is disabled until a session is explicitly started. | A first-time customer cannot perform the obvious action—typing—and may assume the app is broken. | Default to Full Power Text or let typing a first message create the appropriate session automatically. Keep voice as an intentional choice. | At least 90% of new visitors submit a first message without setup assistance; reduced abandonment before first turn. |
| P0 | Runtime configuration is presented as primary UI. | “Server URL” and capability refresh make the product feel unfinished or developer-only and invite accidental misconfiguration. | Auto-detect the same-origin backend. Move the URL and manual capability refresh into an “Advanced connection settings” disclosure, showing them only when connection recovery is needed. | Fewer connection-setting errors and fewer support questions mentioning server URL or bridge configuration. |
| P1 | The empty state provides no guidance. | Customers cannot quickly understand PJ's differentiated value or form a successful first request. | Add a short value statement and 3–4 task starters, such as “Summarize uploaded documents,” “Continue a saved project,” and “Create a structured plan.” Adapt starters to available capabilities. | Higher first-prompt completion and starter-to-success conversion. |
| P1 | Mode names do not explain trade-offs. | Customers must guess which option is appropriate and may choose voice or reduced capability accidentally. | Add one-line labels or an info popover comparing input, speed, tools, persistence, and best use. Consider names such as “Voice — quick,” “Voice — with tools,” and “Text — with tools.” Remember the last choice. | Fewer mode switches immediately after session start; improved mode-selection confidence in usability tests. |
| P1 | Customer and operator information share the same hierarchy. | Tool counts and infrastructure states add cognitive load and can undermine trust despite a healthy experience. | Replace the default catalog block with a customer summary (“Ready: web, files, documents, and 82 more tools”). Put exact models, counts, bridge state, and diagnostic errors in details. | Customers can correctly describe available abilities without explaining infrastructure terms. |
| P1 | Errors are diagnostic rather than task-oriented. | Customers see tracebacks, endpoint terminology, WAF guidance, or request IDs without a clear next action. | Use a three-part pattern: plain-language outcome, one recovery action, collapsible technical details. Catch missing terminal credentials before client construction and link to the exact setup step. | More successful self-recovery; fewer raw tracebacks in customer sessions. |
| P2 | Session language and lifecycle are ambiguous. | It is unclear whether “End Session” saves, closes, or deletes a conversation, and “Start Session” does not match familiar chat vocabulary. | Use “New chat” and “Close chat,” add a saved-state cue, and make destructive actions explicit. Keep conversation history visible after closing. | Customers can predict the result of closing a chat in task-based testing. |
| P2 | Important dynamic regions lack explicit assistive semantics. | Screen-reader users may not be notified when connection status, messages, uploads, or tool states change. Mode selection may not announce the selected state. | Add a polite live region for status and messages, use `role="status"` where appropriate, expose mode state with tabs or `aria-pressed`, and give the composer a persistent label. Validate focus movement for approvals and errors. | No serious findings in automated accessibility checks; successful keyboard/screen-reader completion of start, send, upload, and approve flows. |
| P2 | Approvals lead with raw arguments. | Nontechnical customers may approve without understanding impact or reject safe actions out of caution. | Show a generated, deterministic impact summary (action, destination, data affected, reversibility) above collapsible raw arguments. Keep approve and reject equally prominent. | Higher comprehension scores without increasing unsafe approvals. |
| P3 | Transcript actions could be more explicit. | “Clear” may be mistaken for deleting saved history, and copy feedback is not obvious from the static control. | Rename to “Clear view” if it is nondestructive, confirm destructive clearing, and announce “Transcript copied.” | Fewer mistaken-clear reports and clear confirmation in accessibility testing. |

## Suggested first-run experience

1. Open directly into **Text — with tools**, focused on an enabled composer.
2. Show “What would you like to accomplish?” plus capability-aware starters.
3. Create and title a durable conversation on first send; do not require a
   separate session action.
4. Offer voice beside the composer. Request microphone access only after the
   customer selects voice.
5. Show a compact readiness line such as “PJ is ready · Files and web enabled.”
   Put models, server URL, tool counts, and refresh actions under diagnostics.
6. When something is unavailable, preserve the customer's draft and give one
   recovery action before technical details.

## Recommended validation plan

Test the redesigned first-run flow with five to eight customers who have not
seen PJ. Ask each participant to: start a text conversation, identify which
voice mode can use tools, upload a document, continue a saved conversation, and
interpret an approval. Measure time to first message, first-attempt completion,
mode-selection confidence, recovery from a simulated connection failure, and
approval comprehension. Add keyboard-only and screen-reader passes for the same
tasks, plus mobile viewport testing once a graphical browser environment is
available.
