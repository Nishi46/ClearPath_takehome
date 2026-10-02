# UI/UX redesign plan: ClearPath Review

## Context
The app works (core loop, flags, audit) but looks like an accessibility-first wireframe: default system font, thin grey borders, every control the same weight, a plain-text header, and walls of same-size text. Nothing tells the user where to look first. The checklist scores product intuition and detail, so the UI must (a) make the next action obvious for each persona and (b) be calm and easy on the eyes. Constraints: Jinja + htmx + plain CSS, no build step. Keep the existing accessibility rules (status/severity never color-only, visible focus, no horizontal scroll, asset + flags visible together on a laptop). This is a visual and layout pass: no route, schema or rule changes.

## Diagnosis (from reading the current templates and CSS)
1. **No visual hierarchy.** Every button is a bordered white box. "Approve" is the only filled one; "Request changes" looks the same as "Apply" or "Post comment".
2. **Header is cluttered.** Brand, nav, "Role: Reviewer (current) Marketer", Reset demo and a hanging note all share one row. The role switcher reads as a pair of buttons, not as "who am I right now". Reset demo is as prominent as navigation, and risky.
3. **Queue is a dense 8-column table**, all one weight. Urgency is only a left border plus a tinted row. Submitter and Version take as much room as Title. There is no "what needs me now" cue. Filters need an Apply click.
4. **Review screen** is a long heading plus a metadata sentence joined by "·", then two independently scrolling panels. Copy, flags, comments, decision and history are all separate bordered boxes of equal weight. Decision is a textarea plus three buttons with no guidance on which to use.
5. **Marketer screens.** Feedback is a bordered box with the decision in small bold text. "Edit and resubmit" is a plain link. Submit form is a plain stack of fields.
6. **Colors.** Pure white on near-white with grey 1px borders, saturated red/orange row tints, and a heavy orange focus outline. Contrast is fine but it feels clinical and harsh.

## Design principles
- **One primary action per screen**, visually dominant. Reviewer: open the most urgent item; decide. Marketer: act on "Needs your action"; submit.
- **Group, don't box.** Use whitespace and a few cards, not borders on everything.
- **Quiet by default, loud for exceptions.** Neutral surfaces; color is reserved for urgency, severity and decisions.
- **Words + shape + color** for status and severity (keep current rule).
- **Role-aware chrome.** The header, landing page and nav change with the role so each persona sees only what is relevant.

## Visual system (edit `:root` tokens in app/static/style.css)
- **Type:** load Inter (Google Fonts, `font-display: swap`, system fallback). Scale 12/14/16/20/28; body 15-16px, line-height 1.55; titles 600 weight; tabular numbers for dates and counts. Max line length ~70ch for copy.
- **Color (light):** page `#f6f7f9`, cards `#fff` with a soft shadow `0 1px 2px rgba(16,24,40,.06)` and a 1px `#e4e7ec` border; text `#101828` / muted `#475467`; brand/primary a calmer indigo-blue `#3b5bdb` with a darker hover. Tinted semantic pairs (bg 50-level, text 700-level) for overdue, soon, approved, changes requested, rejected, in review, new. Reduce saturation of row tints; use a 4px accent edge plus a pill instead of a full-row wash.
- **Dark mode:** `prefers-color-scheme: dark` token overrides (easy on the eyes); verify the contrast of highlights and diff colors.
- **Shape/space:** 8px grid, radius 10px for cards and 8px for controls, 12px pill for chips, generous card padding (16-20px), main column 72rem with 24px gutters.
- **Focus:** keep a visible ring but switch to a 2px offset indigo ring (`#3b5bdb` at 40% glow) instead of the heavy orange outline; keep 3:1 contrast.
- **Icons:** small inline SVG set (flag, clock, check, alert, lock, arrow); decorative, `aria-hidden`, always paired with text.
- **Motion:** 120-150ms hover/focus transitions, only under `prefers-reduced-motion: no-preference`.

## Global chrome (app/templates/base.html)
- Slim top bar: logo mark + "ClearPath Review" on the left; primary nav as tabs with an active-state underline (Reviewer: Queue, Dashboard if built; Marketer: My submissions, Submit); right side: role segmented control + overflow menu.
- **Role switcher as a labelled segmented control** ("Viewing as: Reviewer | Marketer") with a filled active segment, so the current persona is unmistakable.
- **Reset demo** moves into a small "Demo" menu (`<details>`) with the note inside it, de-emphasized, still reachable in one click, and still using the existing confirm page.
- Primary "+ Submit" is a filled button, shown for the marketer. For the reviewer it is secondary.
- Footer line: "Rules are illustrative, not legal advice."

## Reviewer experience

### Queue (queue.html)
- Header row: title "Review queue" + a one-line summary rendered as stat chips ("14 items", "3 overdue / launching soon", "5 need review"). Chips are clickable shortcuts that set a filter (reuse existing filter query params).
- Filters in one compact toolbar: search, selects, auto-submit on change (small `queue.js` addition, with a `<noscript>` Apply fallback), and a "Clear" text button shown only when filters are active.
- **Row redesign** (keep a real `<table>` for accessibility and the existing mobile stacked-card CSS):
  - Left: urgency pill ("Overdue", "Tomorrow", "In 3 days") with an icon and a colored edge.
  - Title as the strong first column, with a muted second line: "Loan · Email · by Jordan".
  - Merge Product + Channel + Submitter into that subline (fewer columns, quicker scanning).
  - Launch date, Flags (count + highest-severity pill with a word "High"), Status chip, Version "v2".
  - Whole row is the click target (already in queue.js); add a hover lift and a chevron.
- Sticky table header; zebra-free, row separators only.
- Empty states get an illustration-free icon, a one-sentence explanation and a primary button (for example "Clear filters").

### Review screen (review.html), the key screen
- **Sticky header band:** back link; title; a row of chips (Product, Channel, launch with urgency, Status, version selector as a segmented control). Replaces the "·"-joined sentence.
- **Two-column layout, laptop first:** left "Asset copy" in a large, readable card (serif or 17px sans, comfortable line height) with inline highlights; right a **tabbed or stacked side panel: Flags | Comments | History**, with Flags the default and a count badge on each tab. This removes the long scroll and keeps copy + flags visible together.
- **Flag cards:** severity pill in the card header (icon + word), rule name, the matched text as a quoted snippet, one-line "why", then actions ("Use snippet" primary-ghost, "Dismiss" tertiary). "Missing: add this" cards get a dashed outline and an "Add this wording" suggestion block. Hover/focus on a card highlights its text in the copy and vice versa.
- **Decision bar:** sticky at the bottom of the viewport on laptop. Layout: reason textarea (auto-grows), then three clearly differentiated buttons: **Approve** (green filled), **Request changes** (amber outline), **Reject** (red outline). Helper text under the disabled state ("Add a reason to request changes"). Short inline guidance on when to use each outcome (tooltip text, also in visible helper text).
- **Decided/locked versions:** replace the banner with a calm "lock" strip (lock icon, outcome, who, when, reason). The decision bar is hidden.
- **Diff:** a segmented toggle "Current | Changes since v1"; added = green underline + "+" marker, removed = red strike + "-" marker (already non-color-only), and a summary chip ("+2 added, 1 removed").
- History becomes a vertical timeline (dot, event, timestamp) in the History tab.

## Marketer experience

### My submissions (mine.html)
- Greeting header with "Submitting as <name>" and a primary "+ New submission" button.
- **"Needs your action" section first**, with cards that have an amber left edge: title, status chip, the reviewer's reason as a quote block, and a filled **"Edit and resubmit"** (or "Fix and resubmit as new version" for rejected) button.
- "In progress" and "Done" as compact rows with a status chip, a human sentence ("Waiting for a reviewer", "Approved Oct 2, locked"), and a small progress indicator (Submitted > In review > Decision) so "where is my asset?" is answered at a glance.
- Empty state: friendly explanation + a primary "Submit your first asset".

### Submit / resubmit form (submit.html, resubmit.html)
- Single card, grouped into "Details" (title, product, channel, launch date) and "Copy" (large textarea with a live character count), then "Notes for reviewer".
- Labels above fields, required marker, helper text, 40px-high controls, inline errors with an icon (inputs keep their values, already done), rush/past-date warnings as amber inline callouts.
- **Pre-check panel on the right is the hero:** a heading "Pre-check (before a reviewer sees it)", an overall status ("2 issues to fix" or "No flags detected") and compact flag cards with a "Fix this" hint. Sticky while scrolling. On narrow screens it moves below the copy field.
- Resubmit: reviewer feedback in a pinned callout at the top, plus the "Changes since v1" diff beside the editor.
- Submit button is the sole filled button, disabled state has helper text; success banner links to My submissions.

## First impression and polish
- Reviewer lands on the queue sorted by launch date with the most urgent row visually dominant and the stat chips visible; one click to a flagged item.
- Consistent button hierarchy everywhere: primary (filled), secondary (outline), tertiary (text link); destructive only red-outlined.
- Loading/htmx states: subtle inline "Checking..." with a spinner on the pre-check; disabled buttons while a request is in flight (reuse the existing double-click guard).
- Copy pass: microcopy that says what happens ("Request changes sends this back to Jordan with your reason").
- Responsive: verify at 1280x720 (asset + first flag + decision visible without scrolling), 768 and 390 widths.

## Files to modify
- `app/static/style.css`: tokens, type, components, dark mode, new layout (bulk of the work, largely a rewrite organized by component).
- `app/templates/base.html`: header, role segmented control, demo menu, footer.
- `app/templates/queue.html`, `_flag_cards.html`, `review.html`, `mine.html`, `submit.html`, `resubmit.html`, `_precheck.html`, `error.html`, `reset_confirm.html`: markup for the new components (keep all existing ids, `aria-*`, form field names and route actions so `tests/` still pass).
- `app/static/queue.js` (auto-submit filters, stat chip shortcuts), `app/static/review.js` (tabs, card/copy hover link, auto-grow textarea, decision helper text).
- Small additions only in Python if a template needs derived text (for example a "due in N days" label or per-status progress); reuse the existing helpers in `app/queue.py` and `app/mine.py`.

## Implementation order
1. Tokens, type, buttons, chips and the header (global win, low risk).
2. Queue.
3. Review screen layout, flag cards and decision bar (highest value).
4. Marketer pages and submit form.
5. Dark mode, motion and responsive pass.

## Verification
- `pytest`: existing tests must pass unchanged, since selectors and text that tests assert must stay (check `tests/test_queue_page.py` and the review/submit tests before renaming any class or label).
- Run the app (`uvicorn app.main:app`) and walk it as both personas: reviewer (queue > flagged item > dismiss with note > snippet > request changes > approve another), marketer (submit with errors > pre-check > submit > see feedback > resubmit > diff).
- Check screenshots at 1280x720, 768 and 390 in light and dark; confirm there is no horizontal scroll and the asset, first flag and decision controls fit one laptop screen.
- Accessibility: keyboard-only pass, focus rings visible, contrast >= 4.5:1 text and 3:1 for UI (a contrast checker on the token pairs), status/severity always carry words.
- Re-check the submission checklist items touched (urgency distinct, flags + asset visible together, no console errors, first-time user completes the loop unaided) and report which are verified.
