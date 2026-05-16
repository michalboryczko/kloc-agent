---
phase: 05-ui-structural-fix-polish-a11y-dead-code
verified: 2026-05-16T00:00:00Z
status: human_needed
score: 4/4 must-haves verified at code level
overrides_applied: 0
human_verification:
  - test: "Open the chat view on a 1440x900 viewport with at least one session active"
    expected: "Chat panel fills the right column from header bottom to viewport bottom; the SessionRail is visible on the left (280px); no large dark void surrounding a floating sidebar (F-1 resolved)"
    why_human: "Visual smoke check on a real viewport; geometry cannot be confirmed by code inspection alone"
  - test: "Reload the picker page (no session selected)"
    expected: "Headline fades up over 220ms; eyebrow / button / list arrive in a staggered 20-40-60ms cascade; reduced-motion users see no animation"
    why_human: "Animation timing and feel can only be assessed visually; reduced-motion behavior depends on OS preference"
  - test: "Hover over an existing session row in the picker"
    expected: "Row shifts ~3px to the right and the arrow glyph fades in"
    why_human: "Visual confirmation of the hover-shift + arrow reveal"
  - test: "Observe RUNNER STATE pill in SessionRail while a runner is fresh/warm"
    expected: "Status dot has a soft amber/green glow (box-shadow visible against dark background)"
    why_human: "Subjective visual perception of the glow; needs comparison against design-direction.md"
  - test: "Open a tool-call card and toggle the <details> arguments/result sections"
    expected: "Chevron rotates 90 degrees with a 120ms ease on open; reverses on close"
    why_human: "Animation feel and direction cannot be programmatically confirmed"
  - test: "Inspect compiled DOM of the chat textarea after mount"
    expected: "Textarea has aria-label=\"Ask kloc analyst\"; persists across CopilotKit internal remounts"
    why_human: "Requires running app and DOM inspection to confirm MutationObserver applies the label"
  - test: "Send a message and observe whether a streaming indicator appears in the chat input row"
    expected: "Some streaming indicator (CopilotKit native OR mounted StreamingDots) is visible while the agent is producing tokens"
    why_human: "UI-P4 spec calls for a 3-dot pulse in chat input. The implementation defers to CopilotKit's native indicator (documented decision). Need human to confirm CopilotKit's indicator is actually visible and adequate for the F-2 'no streaming cue' concern"
  - test: "Run a manual WCAG AA contrast check on --text-mute and --text-dim against bg-0 / bg-1"
    expected: "Both tokens meet WCAG AA contrast (4.5:1 for normal text or 3:1 for large text) where used"
    why_human: "A-2 requires measured contrast; tokens are defined but contrast measurement requires a tool"
---

# Phase 5: UI structural fix, polish, a11y, dead code — Verification Report

**Phase Goal:** Fix the "75% black space" complaint (F-1) by replacing `<CopilotSidebar>` with inline `<CopilotChat>` + a new `SessionRail`. Then layer in polish, accessibility, and dead-code deletion.

**Verified:** 2026-05-16
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| SC1 | `<CopilotSidebar>` replaced with inline `<CopilotChat>`; `SessionRail.tsx` houses session card + runner-state pill + artifacts; artifacts wiring moved from `AgentBody` to `SessionRail`; chat fills viewport | VERIFIED (code-level) — visual on 1440x900 needs human | `grep -rn 'CopilotSidebar' frontend/src/` returns empty. `frontend/src/app/page.tsx:5,122` imports and mounts `<CopilotChat>`. Grid wrapper at line 118: `grid grid-cols-1 min-[880px]:grid-cols-[280px_1fr]`. `SessionRail` rendered at line 119 with three sections (`SessionCard`, `RunnerStateSection`, `ArtifactsSection` in `SessionRail.tsx:57-59`). `AgentBody.tsx` no longer contains artifacts UI — it only registers the wildcard tool-call renderer (33 lines). |
| SC2 | Polish layer: picker entrance animation (220ms fade-up + 20ms stagger), hover-shift, runner-state dot glow, streaming 3-dot pulse, chevron rotation on `<details[open]>`, `<title>`/`<meta>` polish | PARTIAL — streaming indicator deviates | See sub-table below |
| SC3 | A11y A-1..A-5 cleared | VERIFIED (code-level) — contrast measurement needs human | A-1: `A11yChatTextarea.tsx` applies `aria-label="Ask kloc analyst"` via MutationObserver and is mounted around `<CopilotChat>` in `page.tsx:121-130`. A-2: zero `opacity:` text-muting; all remaining `opacity` is decorative grain, disabled state, or hover-reveal arrow. A-3: `aria-label="Back to sessions"` at `page.tsx:93`. A-4: cursor-wait scoped only to busy row at `SessionPicker.tsx:89-93`; `aria-busy={busyId === s.id}` at line 96. A-5: `<h1>` in `SessionPicker.tsx:36` and `page.tsx:97`; `<h2>` for chat session subtitle at `page.tsx:104`. |
| SC4 | Dead modules deleted: `ChatWindow.tsx`, `Composer.tsx`, `agui-http-agent.ts`, `sseParser.ts`; no imports remain | VERIFIED | `ls` shows all four files absent. `grep -rn "ChatWindow\|Composer\|agui-http-agent\|sseParser" frontend/src/` returns empty. |

**Score:** 4/4 must-haves verified at code level (visual + contrast checks routed to human verification).

### SC2 Polish Sub-Items

| Item | Status | Evidence |
| ---- | ------ | -------- |
| Picker entrance animation (220ms title fade-up + staggered 20/40/60ms cascade) | VERIFIED | `globals.css:160-185` defines `@keyframes kloc-fade-up` and 4 utility classes at 220ms. `SessionPicker.tsx` lines 30, 36, 41, 45, 78 apply `motion-safe:animate-kloc-fade-up[-delay-N]` on eyebrow / headline / button-row / list. |
| Hover-shift on session rows (translateX(3px) + arrow) | VERIFIED | `SessionPicker.tsx:93` `hover:translate-x-[3px]` and `SessionPicker.tsx:118` arrow glyph with `opacity-0 group-hover:opacity-100`. |
| Runner-state dot glow | VERIFIED | `SessionRail.tsx:22,26` apply `shadow-[0_0_8px_rgba(74,222,128,0.5)]` to the fresh/warm dot. |
| Streaming 3-dot pulse in chat input | DEVIATION (documented) — see human verification | `StreamingDots.tsx` exists and the `kloc-pulse-dot` keyframe is in `globals.css:192-211`, but the component is NOT mounted in chat input. Plan 05-02 decided to defer to CopilotKit's native streaming indicator (documented at `05-02-SUMMARY.md` decisions). Spec text says "streaming indicator (3-dot pulse) in chat input" — this is a deviation needing human confirmation that the CopilotKit-native indicator suffices for F-2. |
| Tool-card chevron rotation on `<details[open]>` | VERIFIED | `ToolCallCard.tsx:59,73` `group-open:rotate-90 transition-transform duration-[120ms]`. |
| `<title>` + `<meta>` polish in layout.tsx | VERIFIED | `layout.tsx:27-30` sets `title: "kloc agent"` and the full analyst-chat description. |

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `frontend/src/components/SessionRail.tsx` | SESSION + RUNNER STATE + ARTIFACTS, useCoAgent, 280px aside | VERIFIED | 135 lines, three sub-components, `useCoAgent<KlocAgentState>` for `runner_state` + `artifacts`, 2px amber left edge in `SessionCard`, dot+glow in `RunnerStateSection`, empty-state italic in `ArtifactsSection`. |
| `frontend/src/components/A11yChatTextarea.tsx` | Applies aria-label via MutationObserver | VERIFIED | 48 lines, useEffect + MutationObserver pattern; default label "Ask kloc analyst". |
| `frontend/src/components/StreamingDots.tsx` | 3-dot pulse component | VERIFIED (exists) / ORPHANED (unused) | 38 lines, exported, references `animate-kloc-pulse-dot` + delay classes. No import sites in `frontend/src/`. Documented as intentional in 05-02 summary. |
| `frontend/src/app/page.tsx` | Grid layout, CopilotChat, SessionRail, AgentBody, h1+h2 | VERIFIED | All present at expected lines. |
| `frontend/src/app/layout.tsx` | Metadata polish | VERIFIED | Title "kloc agent", full description. |
| `frontend/src/app/globals.css` | 2 keyframes + animation utility classes + chat-pane height fix | VERIFIED | `@keyframes kloc-fade-up`, `@keyframes kloc-pulse-dot`, 4 fade-up utilities, 3 pulse-dot utilities, `.kloc-copilot-chat` height fill at line 138-151. |
| `frontend/src/components/AgentBody.tsx` | Slim — wildcard tool renderer only; no artifacts UI | VERIFIED | 34 lines, returns `null`, only registers `useCopilotAction({name: "*"})`. Artifacts wiring confirmed moved out. |
| `frontend/src/components/ToolCallCard.tsx` | `group-open:rotate-90` chevron | VERIFIED | Two `<details>` blocks, each chevron has the rotation class. |
| Dead: `ChatWindow.tsx`, `Composer.tsx`, `agui-http-agent.ts`, `sseParser.ts` | Deleted | VERIFIED | `ls` confirms absent; `grep` confirms no imports. |

### Key Link Verification

| From | To | Via | Status |
| ---- | -- | --- | ------ |
| `page.tsx` | `<CopilotChat>` | Import from `@copilotkit/react-ui`, mounted inside `<CopilotKit>` at line 122 | WIRED |
| `page.tsx` | `SessionRail` | Import line 16, mounted at line 119 with `sessionId` prop | WIRED |
| `SessionRail` | agent state (runner_state, artifacts) | `useCoAgent<KlocAgentState>` at line 44 | WIRED |
| `page.tsx` | `A11yChatTextarea` | Wraps `<CopilotChat>` at lines 121-130 | WIRED |
| `page.tsx` | `AgentBody` | Imported line 14, mounted inside `<CopilotKit>` at line 117 | WIRED |
| `SessionPicker` | fade-up animations | `motion-safe:animate-kloc-fade-up[-delay-N]` at lines 30,36,41,45,78 | WIRED |
| `StreamingDots` | chat input | NOT WIRED | NOT_WIRED — DEVIATION (documented in 05-02-SUMMARY; CopilotKit native indicator used instead) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| `SessionRail` | `runner_state`, `artifacts` | `useCoAgent<KlocAgentState>` (CopilotKit shared state from runner via AG-UI) | Yes (when runner is live); falls back to "unknown" + "No artifacts yet." empty state | FLOWING — empty state correctly handled, no hardcoded mock |
| `SessionPicker` | `sessions` prop | `listSessions()` API call in `page.tsx:36` populated into state, passed down | Yes — real backend call | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Frontend build compiles cleanly | `cd frontend && npm run build` | "Compiled successfully in 6.2s"; 5 routes (`/`, `/_not-found`, `/api/agent-proxy`, `/api/copilotkit`); no TypeScript errors | PASS |
| Dead modules absent | `ls frontend/src/components/ChatWindow.tsx ...` (4 files) | All 4 "No such file or directory" | PASS |
| No CopilotSidebar references | `grep -rn 'CopilotSidebar' frontend/src/` | empty | PASS |
| No dead-module imports | `grep -rn 'ChatWindow\|Composer\|agui-http-agent\|sseParser' frontend/src/` | empty | PASS |
| No inline styles in app/components | `grep -rn 'style={{' frontend/src/components frontend/src/app` | empty | PASS |
| Keyframes present | `grep '@keyframes kloc-' frontend/src/app/globals.css` | 2 matches (`kloc-fade-up`, `kloc-pulse-dot`) | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| UI-P3 | 05-01 | CopilotSidebar replaced, SessionRail created with 3 sections | SATISFIED | SC1 verified; SessionRail.tsx exists, 2-col grid in page.tsx |
| UI-P4 | 05-02 | 6 polish items | SATISFIED (with documented deviation on streaming-in-chat) | SC2 verified for 5/6 items; streaming indicator deferred to CopilotKit native (decision documented in 05-02-SUMMARY) |
| UI-P5 | 05-03 | A-1..A-5 | SATISFIED | A-1 via A11yChatTextarea; A-2..A-5 verified by inspection. Contrast measurement (A-2) routed to human verification. |
| UI-P6 | 05-04 | 4 modules deleted | SATISFIED | All 4 files absent; no remaining imports |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |

(none) — debt-marker grep on modified files returned zero matches. No `TODO`, `FIXME`, `TBD`, `XXX`, `HACK`, or `PLACEHOLDER` markers in any file modified by this phase.

### Human Verification Required

(See `human_verification` block in frontmatter — 8 items total covering visual viewport check (F-1), animation feel, glow visibility, chevron rotation, MutationObserver-applied aria-label, streaming indicator adequacy, and WCAG AA contrast measurement.)

### Gaps Summary

No blocking gaps. All four roadmap success criteria are observably true at the code level: layout swap is complete (no `CopilotSidebar` reference, grid + `SessionRail` + inline `CopilotChat` mounted), polish layer is in place (5 of 6 sub-items verified — the 6th, "3-dot pulse in chat input", is intentionally deferred to CopilotKit's native indicator and documented in 05-02 summary), a11y items A-1..A-5 are addressed (A-1 by MutationObserver helper, A-2..A-5 by direct verification), and the four dead modules are deleted with no lingering imports.

The deviation worth flagging to the user is the streaming indicator: the spec says "3-dot pulse in chat input" but the implementation kept the StreamingDots primitive available and relied on CopilotKit's native indicator for the chat surface. The user should confirm whether the CopilotKit-native indicator is acceptable (F-2 concern), or whether StreamingDots should be wired in as an additional surface. This is captured in the human verification list.

All eight human verification items are visual / DOM-runtime / contrast-measurement checks that cannot be programmatically confirmed.

---

*Verified: 2026-05-16*
*Verifier: Claude (gsd-verifier)*
