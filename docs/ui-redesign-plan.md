# Podcast Clipper: UI/UX improvement plan

Audit of the app at `http://127.0.0.1:3000` on 2026-11-20 (home, clips page, stats, settings; dark desktop and light phone at 375 px), with a ranked list of improvements and a ready-to-paste prompt for each.

## Your choices

| Question | Choice |
|---|---|
| How far to go visually | **Evolve, keep the identity**: amber `#F5A524` (`--tally`), cool greys, Geist + Bricolage Grotesque, the sound-wave logo |
| Clip viewing | **Grid plus a full-screen viewer**: grid for overview, click opens a Shorts-style vertical viewer |
| Motion | **Purposeful, CSS + Motion**: animation answers actions; CSS plus the `motion` React library; reduced motion respected |
| Focus areas (ranked higher) | **Processing experience**, **Home and adding videos**, **Stats, settings, accessibility polish** |

A note on the skill's generated design system: `search.py "video creator tool content clipping" --design-system` proposed "Dark Mode (OLED)" with near-black, rose `#E11D48` and Inter / Playfair Display. That fights the identity you chose to keep, so it is used only for its motion tier (Standard stagger, 300-450 ms) and its checklist, not its palette or fonts.

## What works today

- Clear, calm hierarchy: one input bar, one list, one primary action per page.
- Real accessible names on most controls (`Rank 1, score 86 of 100. Show score details`), `aria-live` on progress, `:focus-visible` outline in the amber ring colour, a `prefers-reduced-motion` block.
- Honest, plain copy ("Gemini was busy, so these clips were picked without it. Re-rank to try Gemini again.").
- Stats page explains its own numbers in sentences instead of decorating them.

## Weak points found

| # | Where | Finding | Evidence |
|---|---|---|---|
| W1 | Clips page, phone | Touch targets below 44 px: copy and download buttons 32x32, score chip 59x26, header icons 36x36, "Download all" 36x32 | Measured with `getBoundingClientRect` at 375 px |
| W2 | Clips page, phone | "Download all" and "Details" become icon-only with no visible label or tooltip | Phone screenshot, header right |
| W3 | Clip cards | Native `<video controls>`: tiny on a 2-column phone grid, look different per browser, nothing pauses the other clips when one starts | `clip-card.tsx:312-316`, no `pause`/`onPlay` handling |
| W4 | Clip cards, fallback clips | Titles are raw transcript sentences ("So, from this AlphaFold moment to solving all…"), which breaks the "no transcript snippets on clip cards" rule; the white score chip (45) is unexplained on the card itself | Max Jaderberg job, phone |
| W5 | Processing | Stage labels are hidden on phone except the current one; no tab-title progress or "done" notification, so a 5-8 minute run has to be watched | `progress-strip.tsx:52-57`; no `document.title` anywhere |
| W6 | Motion | Only `transition-colors`, two spinners and one `tally-pulse`. Popovers, clip arrival, completion and page changes have no enter or exit; state changes are instant | grep of `components/` |
| W7 | Home | "Make clips" when empty is a grey block with grey text: reads as dead, not as "paste a link first". Helper text is 12 px muted | Home, both themes |
| W8 | Home list | Rows show only "N clips" and a date; nothing hints at the score of the best clip, failed reason or kept-video state until you open it | Home |
| W9 | Stats | Stage colours carry meaning alone (legend + colour); segment labels like "1m 04s" get cramped; tooltips need hover or tap | Stats "By stage" |
| W10 | Settings | "Saved" confirmation sits at the bottom of the page, far from the control that changed | Settings |

## Ranked improvements

Ranking = your focus areas first, then impact on daily use, then effort. Items 1 and 2 are foundations the others build on.

| Rank | Improvement | Fixes | Effort | Skills |
|---|---|---|---|---|
| 1 | Motion and interaction foundation (done, `feat/ui-foundation`) | W6, part of W1 | S | ui-ux-pro-max, design-system, ui-styling |
| 2 | Touch, labels and accessibility pass (done, `feat/ui-foundation`) | W1, W2, W9 (part) | S-M | ui-ux-pro-max, ui-styling |
| 3 | Live run experience (done, `feat/ui-live-run`) | W5, W6 | M | ui-ux-pro-max, frontend-design, ui-styling |
| 4 | "Something is running" logo signal and tab title (done, `feat/ui-live-run`) | W5 | S | frontend-design, ui-ux-pro-max |
| 5 | Input bar and drop zone (done, `feat/ui-home`) | W7 | M | frontend-design, ui-ux-pro-max, ui-styling |
| 6 | Video list at a glance (done, `feat/ui-home`) | W8 | M | ui-ux-pro-max, ui-styling |
| 7 | Stats page polish (done, `feat/ui-stats-settings`) | W9 | M | ui-ux-pro-max (chart), dataviz, ui-styling |
| 8 | Settings feedback (done, `feat/ui-stats-settings`) | W10 | S | ui-ux-pro-max, ui-styling |
| 9 | Full-screen clip viewer | W3 | L | frontend-design, ui-ux-pro-max, ui-styling |
| 10 | Clip cards: one player, fallback titles | W3, W4 | M | ui-ux-pro-max, ui-styling |
| 11 | Re-rank comparison motion | W6 | S | ui-ux-pro-max |

---

## How to use the prompts

1. Start a fresh Claude Code session in the repo root for each item (or each pair of small items).
2. Paste the **shared preamble** below, then the item's prompt.
3. Each prompt names the skills to invoke with their slash commands. The UI/UX Pro Max search tool runs from its own folder:
   `python "C:/Users/akash/.claude/plugins/cache/ui-ux-pro-max-skill/ui-ux-pro-max/2.15.0/.claude/skills/ui-ux-pro-max/scripts/search.py" "<query>" --domain <domain>`
   (queries: one intent, 2-5 terms; retry once narrower; never present a 0-result search as data).

### Shared preamble (paste before every prompt)

```text
Context: Podcast Clipper frontend (Next.js 15 + Turbopack, React 19, Tailwind v4, lucide-react, sonner) in
ai-podcast-clipper-frontend. Read CLAUDE.md first and follow "How Akash wants work done".

Branch: create feat/ui-<topic> from an up-to-date feat/optimized-version. Do not commit, push or merge;
hand over ready-to-paste git commands with a conventional-commit message (feat(frontend): ...).

Keep the identity: amber accent --tally (#F5A524) with --tally-ink and --tally-soft, cool greys, Geist for UI and
Bricolage Grotesque for headings (self-hosted in src/fonts), the sound-wave logo, Light / Dark / System themes.
Use tokens from src/styles/globals.css, never raw hex in components. Only components/ui/sonner.tsx is shadcn;
do not pull in the full shadcn kit, build small local components instead.

House rules: sentence case; no all-caps labels; no "A · B · C" meta strings; no file paths in the UI; no transcript
snippets on clip cards; lucide icons only (no emoji); plain verbs; errors say what happened and what to do.

Motion: use CSS transitions/keyframes plus the `motion` package (motion/react) only where CSS cannot do it
(layout, exit, shared element). Durations from the motion tokens (item 1). Every animation must answer an action
or show a state change; nothing loops except a "running" indicator. Respect prefers-reduced-motion (final state,
no movement). Animate transform and opacity only, never width/height/top/left (use scaleX for bars).

Verify before handing over: npx tsc --noEmit, npm run lint, npm run format:check; screenshots in light, dark and
375 px phone width of every page touched (use a fake DATA_DIR with sample job.json files for running/failed states,
as CLAUDE.md describes). Say plainly what you could not test. Update README.md, CHANGES.md, docs/advanced.md
and CLAUDE.md where the UI they describe changed.
```

---

### 1. Motion and interaction foundation (done)

**Why:** W6. Every later item needs the same durations, easings and a reduced-motion switch; doing it once keeps the app from getting "one duration for everything" or scattered effects.

**What:**
- Motion tokens in `globals.css` (`--dur-fast` 120 ms, `--dur-base` 200 ms, `--dur-slow` 320 ms, `--ease-out`, `--ease-in-out`, `--ease-spring` for `motion`), exits about 30% faster than enters.
- Add `motion` (pin an exact v12 release) and a tiny `lib/motion.ts` exporting shared variants (`fadeRise`, `popIn`, `listStagger`) and a `useReducedMotion` re-export.
- Popover (`components/popover.tsx`) gets enter/exit (scale 0.96 to 1 + fade, origin at the trigger).
- Button press feedback: `active:scale-[0.97]` on buttons and icon buttons.
- Skeletons get a calm shimmer (disabled under reduced motion).

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:design-system /ui-ux-pro-max:ui-styling

Task: add a motion foundation to the Podcast Clipper frontend. No visual redesign yet.

1. Research first with the ui-ux-pro-max search tool and quote what you apply:
   - "easing duration exit faster" --domain ux
   - "reduced motion" --domain ux
   - "micro interaction button press" --domain gsap  (use only the timings; we are not adding GSAP)
   Read references/quick-reference.md section 7 (Animation) of the ui-ux-pro-max skill.
2. Follow the design-system skill's three-layer token architecture (references/token-architecture.md):
   primitive durations/easings -> semantic (--motion-enter, --motion-exit, --motion-emphasis) in
   src/styles/globals.css, exposed to Tailwind v4 through @theme so classes like duration-(--motion-enter) work.
3. Add the `motion` package at an exact version and src/lib/motion.ts with shared variants
   (fadeRise, popIn, listStagger with 30-40 ms stagger capped at 8 items) that read the same token values,
   and a hook that returns static variants when prefers-reduced-motion is set.
4. Apply only to existing pieces: popover enter/exit (components/popover.tsx), button/icon-button press scale,
   skeleton shimmer (components/skeletons.tsx), theme icon cross-fade in components/theme-menu.tsx.
5. Extend the existing @media (prefers-reduced-motion: reduce) block so shimmer and new keyframes stop.
Show me the token table and a 10-second screen recording or before/after screenshots of the popover.
```

---

### 2. Touch, labels and accessibility pass (done)

**Why:** W1, W2, part of W9. On the phone the clip actions are 32 px and the header actions lose their words. WCAG 2.2 AA needs 24 px; the skill's touch rule and Apple/Google guidance want 44/48 on touch screens.

**What:**
- One `IconButton` component: 36 px visual, 44 px hit area on coarse pointers (`@media (pointer: coarse)` or a pseudo-element), required `label` prop that becomes `aria-label` and a tooltip.
- Phone clips header: "Download all" and "Details" keep a short visible label or move into a bottom action sheet; score chip grows to 32 px tall on touch.
- Tooltip component (focus and long-press, not hover only).
- Contrast check of `--muted-foreground` on `--card` and `--tally-soft` notice in both themes; fix any pair under 4.5:1.
- Stats stage legend: add a text label or pattern so colour is not the only cue.

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: accessibility and touch pass across all pages. Use the motion tokens from the foundation branch if merged.

1. Search one outcome at a time and quote the rule you apply:
   - "touch target size" --domain ux
   - "icon button accessible label" --domain icons
   - "tooltip keyboard focus" --domain ux
   - "color not only indicator" --domain ux
   - "dark mode contrast" --domain ux
   Then run the pre-delivery checklist in the ui-ux-pro-max skill's references/pro-rules.md.
2. Measured problems to fix (375 px phone): copy and download buttons 32x32, score chip 59x26, header icons
   36x36, "Download all" 36x32 and icon-only with no visible label, "Details" icon-only.
3. Build components/ui/icon-button.tsx (required label, tooltip, 44 px hit area on coarse pointers without
   changing desktop density) and components/ui/tooltip.tsx (shows on hover, focus and long-press; Esc closes).
   Replace the ad-hoc icon buttons in clip-card.tsx, video/action-button.tsx, nav-header.tsx.
4. Write a small script (scratch, not committed) that computes contrast for every text/background token pair
   in both themes; fix pairs under 4.5:1 (3:1 for large text and UI parts) by adjusting tokens, not components.
5. Stats "By stage": give each segment an accessible name ("Render, 1 min 6 s, 10.72 rupees") and make the
   legend usable without colour (label inside the bar when it fits, else a pattern or an outline per stage).
Deliver a before/after table of target sizes and contrast ratios, plus screenshots in light, dark, phone.
```

---

### 3. Live run experience (done)

**Why:** W5, W6, and your top focus. A run is 4-8 minutes; today it is a thin bar, a sentence and a timer. The run has a real story (audio up, transcribe, find moments, download in parallel, send clips, render N clips at once, save) that can be shown without noise.

**What:**
- Stage rail with every label visible on phone (2-row wrap or short labels), done stages get a check, the current one an animated fill (scaleX) or the existing pulse when indeterminate.
- A parallel "Download" lane under the cloud stages while `progress.download` is active, so the overlap is visible.
- Clip slots: as soon as moments are known, show N 9:16 placeholders with rank and title; each slot shows "Rendering" then flips into the real clip when it is saved (`liveClips`), with a short amber edge sweep.
- One completion moment: the strip collapses into a "5 clips ready in 6 min 12 s" line and a sonner toast; no confetti.
- Failure state: names the stage that failed, the reason in plain words, "Try again" as the primary button.

**Prompt:**

```text
/frontend-design:frontend-design /ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: redesign the live run view on the clips page (components/video/progress-strip.tsx, video-page.tsx,
clip-card.tsx, skeletons.tsx). Data already exists: job.progress (stage, fraction, etaSeconds, download),
stepsFor(job), currentStage(job), stageSentence(job), liveClips/clipVersions, clipDetails, failedStage.

Design pass first (frontend-design): write a short plan with ASCII wireframes for desktop and 375 px phone,
for four states: phase 1 (audio, transcribe, rank, download in parallel), rendering (2 of 5 ready),
done, failed. Spend the boldness in one place: the moment a clip slot turns into a playable clip.
Keep everything else quiet. Check the plan against the skill's list of generic defaults and revise.

Research (ui-ux-pro-max), quote what you apply:
  - "progress indicators multi step" --domain ux
  - "loading skeleton placeholder" --domain ux
  - "live region announcements" --domain ux
  - "card flip reveal" --domain gsap  (timings only)

Build:
1. Stage rail: all labels visible on phone (wrap to two rows or use the short labels), done = check icon,
   current = scaleX fill with the motion tokens, indeterminate = existing tally-pulse. aria-current on the step.
2. A thin parallel lane for the laptop video download while progress.download is active.
3. Clip slots: N placeholders (9:16, rank, title once known, "Rendering") that are replaced in place by the
   real clip card when saved; use motion's layout animation so the grid does not jump; announce
   "Clip 2 of 5 ready" through the existing aria-live region, at most once per clip.
4. Done: the rail collapses into one line ("5 clips ready in 6 min 12 s") and a sonner toast; the action
   and the toast use the same words.
5. Failed: say which stage failed and what to do; "Try again" is the primary button; keep "Details".
6. Reduced motion: no flip, no sweep, instant swap.
Test with fake job.json files for each state (running pid required for active jobs), screenshot all four
states in light, dark and phone.
```

---

### 4. "Something is running" signal: logo and tab title (done)

**Why:** W5. Runs are long and you leave the tab. A shared signal tells you from any page and from the tab strip.

**What:**
- While any job is active, the header logo's sound-wave bars move gently (CSS, 3 bars, 1.2 s, stops under reduced motion). It is the one looping animation in the app.
- `document.title` shows progress: "Rendering 2 of 5, Podcast Clipper" while rendering, "Clips ready, Podcast Clipper" for 30 s after, then the normal title (a comma, not a middle dot, per the house rules).
- Optional, opt-in from Settings: a browser notification when a run finishes or fails.

**Prompt:**

```text
/frontend-design:frontend-design /ui-ux-pro-max:ui-ux-pro-max

Task: give the app a global "a video is being processed" signal.
1. components/logo.tsx: when any job is active (reuse the job list polling in dashboard-client / use-polling,
   do not add a new poller), animate the existing sound-wave bars with a CSS keyframe on transform: scaleY,
   staggered, 1.2 s, ease-in-out. Static when idle and under prefers-reduced-motion. Add an sr-only
   "Processing a video" next to it while active.
2. Tab title: a small hook that sets document.title to "Rendering 2 of 5, Podcast Clipper" (wording from
   stageSentence/progress), "Clips ready, Podcast Clipper" for 30 s after completion, "Failed, Podcast Clipper"
   on failure, and restores the page title otherwise. Polling already pauses in hidden tabs: make sure the title
   still updates on the next visible poll and does not lie while hidden.
3. Optional notification: a Settings switch "Notify me when a video is done" (off by default) stored in
   data/settings.json through the existing PUT /api/settings; request Notification permission only when the
   switch is turned on.
Research: "notification permission request timing" --domain ux and "looping animation attention" --domain ux.
Verify the logo looks right at 16 px favicon size is unchanged (only the header logo animates).
```

---

### 5. Input bar and drop zone (done)

**Why:** W7, your second focus. The first thing you touch every day.

**What:**
- "Make clips" reads as ready-but-waiting: amber outline when empty, solid amber with `--tally-ink` text once a valid link or file is in. Disabled reason as helper text, not a grey block.
- Live link check as you paste: valid YouTube link shows a small row with the video id recognised and the duplicate check result inline ("You added this 2 days ago: Open clips / Re-rank / Make clips again"), using the existing `/api/jobs/duplicate`.
- Paste anywhere on the home page (Ctrl+V outside the field) fills the field.
- Full-window drop overlay when an MP4 is dragged over the page: dashed amber 9:16 frame from the logo, "Drop to upload". Reject non-MP4 with a clear message.
- Upload progress morphs from the button (the button becomes the progress bar), then the new row slides into "Your videos".

**Prompt:**

```text
/frontend-design:frontend-design /ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: redesign the home input (components/new-job/new-job-bar.tsx, duplicate-notice.tsx, upload-progress.tsx,
lib/upload.ts) without changing the API.

Design pass (frontend-design): plan the five states (empty, valid link, duplicate found, dragging a file,
uploading) with ASCII wireframes for desktop and phone; the memorable element is the drop overlay that
borrows the logo's amber 9:16 frame. Keep the headline "Find the moments worth posting." as is.

Research (ui-ux-pro-max), quote what you apply:
  - "inline validation input" --domain ux
  - "disabled button state" --domain ux
  - "drag and drop upload" --domain ux
  - "visible label placeholder" --domain ux   (the field today has a placeholder only; add a visible
    or sr-only label as the rule says)

Build:
1. Primary button states: empty = outline amber + helper text "Paste a YouTube link or drop an MP4";
   ready = solid --tally with --tally-ink; busy = spinner + "Adding"; never a grey dead block.
2. Validate YouTube links as typed/pasted (watch, youtu.be, shorts, live URLs); invalid shows a short
   error under the field. Valid triggers the existing duplicate check and shows the notice inline with a
   height animation (motion layout), not a jump.
3. Document-level paste handler on the home page that fills the field when focus is not in another input.
4. Full-window drop overlay via react-dropzone (already a dependency), MP4 only, with a clear rejection
   message for other files.
5. Upload: the button turns into the progress bar (same element, scaleX fill, bytes and ETA in tabular
   numbers), then the new job row enters the list with fadeRise.
Screenshots of each state in light, dark, phone.
```

---

### 6. Video list at a glance (done)

**Why:** W8. The list is where you decide what to open; today every row looks the same.

**What:**
- Active jobs pinned to the top with a full-width mini stage rail (from item 3) instead of the 48 px bar on phone.
- Done rows: "5 clips" plus the best clip's score chip (amber if it came from Gemini), and a small "Choose clips to keep" badge when a comparison waits.
- Failed rows: "Failed at Render" plus Try again (exists) with the reason in a tooltip.
- Empty state for a fresh install that points at the input bar.
- Optional search field once there are more than 10 videos.

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: make "Your videos" (components/video-list.tsx, lib/jobs.ts JobSummary) readable at a glance.
Research: "list item status badge" --domain ux, "empty state first use" --domain ux,
"badge chip label wraps" --domain ux, "search filter list" --domain ux. Quote what you apply.
1. Pin active jobs on top with the stage rail from the live run work (or a compact version of it) and the
   stage sentence; on phone the rail spans the row width.
2. Add to JobSummary only what the row needs (best score and whether it was Gemini-scored, comparison
   pending, failedStage) and keep the ETag/304 behaviour.
3. Done rows: clip count + best-score chip; comparison pending gets a "Choose clips to keep" badge.
4. Failed rows: "Failed at <stage>" with the reason in a tooltip (tooltip component from the a11y pass).
5. Empty state: one sentence and an arrow-free pointer to the input bar; no illustration.
6. If more than 10 videos, a search field filtering by title (client side).
7. New and changed rows animate with listStagger only on first load and when a row changes state.
Keep the whole-row link and its focus ring behaviour (has-[a:focus-visible]) intact.
```

---

### 7. Stats page polish (done)

**Why:** W9, your third focus. The page is clear; the charts need to work without hover and without colour, and the first view can show the numbers arriving.

**What:**
- Tiles count up once on first load (400 ms, tabular numbers), never on polling refresh.
- Stage bars grow from 0 with scaleX once; segment labels move outside when they do not fit.
- Tooltips work on focus and tap; each segment is focusable with a full sentence name.
- Runs table on phone: stacked blocks already exist; add the failed reason and keep row height steady.

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling   (also load the dataviz skill before touching chart code)

Task: polish components/stats/* without changing what the page measures or the decisions in CLAUDE.md
("Stats page v2": no pie/donut, no Cost/Time toggle, no expandable tables).
Research: "stacked bar comparison" --domain chart, "chart tooltip keyboard" --domain ux,
"number animation count" --domain ux, "data table mobile" --domain ux. Quote what you apply.
1. Tiles: count-up once on first mount (motion, 400 ms, tabular-nums, reduced motion = final value);
   polling refreshes change the value without animation.
2. By stage: bars enter with scaleX from the left once; a segment too narrow for its label shows the label
   outside or only in the tooltip; every segment is keyboard-focusable with a sentence name; legend is
   readable without colour (from the a11y pass).
3. Run bar tooltips (run-bar.tsx): open on hover, focus and tap; one open at a time; Esc closes.
4. Runs on phone: failed rows show "Failed at <stage>"; no layout shift when the page polls.
5. Check stage colours against the dataviz skill's validator in both themes.
Screenshots in light, dark, phone; note the n<3 "early estimate" states still render.
```

---

### 8. Settings feedback (done)

**Why:** W10. Autosave is right; the confirmation is in the wrong place.

**What:** an inline "Saved" check that appears next to the control that changed (fades after 1.5 s), the bottom "Saved" removed; failed saves show an inline error with Retry; the clips stepper animates the number (rolls up/down); disabled "Audience weight" explains itself on focus too.

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: improve feedback on the settings page (components/settings-page.tsx, components/ui/segmented.tsx).
Research: "autosave confirmation feedback" --domain ux, "error near field" --domain ux,
"stepper input" --domain ux. Quote what you apply.
1. Replace the page-level "Saved" with an inline check next to the control that changed (role=status,
   fades out after 1.5 s with the exit token). Failed PUT /api/settings shows an inline error next to the
   control with a Retry button and restores the previous value.
2. Clips per video stepper: number rolls up/down (motion, 150 ms), buttons 44 px on touch, keyboard arrows work.
3. Segmented control (currency): sliding selection indicator with motion layoutId.
4. Disabled "Audience weight": the reason stays visible and is announced (aria-describedby).
5. "Remove kept videos": confirm with the size in the dialog ("Remove 1 video, 302 MB?"), then a toast.
```

---

### 9. Full-screen clip viewer

**Why:** your viewer choice, and W3. Reviewing 5-8 vertical clips in 150 px cards with native controls is the weakest part of the clips page.

**What:**
- Clicking a card (or Enter on it) opens a full-screen viewer: the clip at full height in 9:16, score breakdown and why-text beside it (below on phone), actions (Copy title, Download, open in Details), rank and title.
- Up/Down or J/K and swipe move to the next clip; Esc closes and returns focus to the card; URL gets `?clip=2` so it can be linked and the back button closes it.
- Custom minimal controls: play/pause on tap, scrubber with time, mute; autoplay muted on open, sound on first tap.
- Shared-element open: the card's poster grows into the viewer (motion `layoutId`), reduced motion = fade.

**Prompt:**

```text
/frontend-design:frontend-design /ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: add a full-screen clip viewer to the clips page; keep the grid as the overview.
Files: components/clip-card.tsx, components/video/video-page.tsx, a new components/video/clip-viewer.tsx.
Media comes from /api/media/<id>/<clip> (range requests, ?v=<clipsVersion> cache key); keep that.

Design pass (frontend-design): ASCII wireframes for desktop (clip centred, details panel right) and phone
(clip full width, details in a bottom sheet you can drag up). The memorable moment is the card growing into
the viewer. Check against the generic-default list and revise before coding.

Research (ui-ux-pro-max), quote what you apply:
  - "modal focus trap return focus" --domain ux
  - "swipe gesture alternative" --domain ux   (every swipe must have a button/keyboard equivalent)
  - "video autoplay muted controls" --domain ux
  - "deep linking back behavior" --domain ux

Build:
1. Open from a card click/Enter; ?clip=N in the URL (router.replace so Back closes); focus trap; Esc closes
   and focus returns to the card.
2. Next/previous with arrow keys, J/K, on-screen buttons and swipe (pointer events, no new gesture library
   unless needed); preload only the next clip's metadata.
3. Custom controls: tap to play/pause, scrubber with current time, mute; starts muted, first tap unmutes;
   pausing happens when the viewer closes or the clip changes.
4. Side panel: rank, score with the six rubric bars (reuse the score-details content), the why-text,
   "Most replayed"/"Viewer highlight" badge when present, Copy title, Download.
5. Shared-element transition card -> viewer with motion layoutId; reduced motion = 150 ms fade.
6. Works in compare mode (?set=new) without mixing the two sets.
Screenshots in light, dark, phone; keyboard-only walkthrough described in the handover.
```

---

### 10. Clip cards: one player at a time, fallback titles

**Why:** W3, W4. Several clips can play at once; fallback clips show transcript sentences as titles.

**What:**
- Starting one clip pauses the others; hover (desktop, fine pointer) plays a muted preview after 400 ms, stops on leave.
- Fallback and replay-only clips get a neutral title ("Clip 1, 0:59 to 1:58 in the video" or a short title from the backend) and a small "Picked without Gemini" tag; the transcript text is not shown on the card.
- Explain the white vs amber score chip in the chip's accessible name and tooltip.

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max /ui-ux-pro-max:ui-styling

Task: fix two clip-card problems (components/clip-card.tsx).
1. Playback: a small shared controller (context or module-level) so starting one <video> pauses any other;
   on fine pointers, hovering a card for 400 ms plays a muted preview from the poster frame and stops on
   leave; never on touch. Research "video autoplay muted controls" --domain ux.
2. Fallback/replay clips (detail.source === "fallback" | "replay"): CLAUDE.md forbids transcript snippets on
   clip cards. Show a neutral title built from the clip's position in the video and a "Picked without Gemini"
   tag; keep the explanation in the score details. If a short title can be produced in the backend
   (moments.py fallback path) without a Gemini call, propose it in the handover instead of building it.
   Research "badge chip label wraps" --domain ux.
3. Score chip: tooltip and aria-label say whether the score came from Gemini or from pacing/replay data.
```

---

### 11. Re-rank comparison motion

**Why:** W6, small. Ticking clips in the comparison view and confirming "Keep the new set" changes the order of clips with no visual link between before and after.

**Prompt:**

```text
/ui-ux-pro-max:ui-ux-pro-max

Task: add motion to components/video/compare-view.tsx using the foundation tokens.
Research "reorder list animation" --domain ux and "selection state checkbox card" --domain ux.
1. Ticking a clip: the card gets the amber selected ring with a 120 ms scale pop; the running count
   ("4 clips kept") updates with a number roll.
2. After "Keep the new set"/"Keep the previous set" the kept clips reorder by score with motion layout
   animation, then the comparison UI exits; reduced motion = instant.
3. While the file swap runs, buttons show a busy state and stay disabled; a failed swap keeps the comparison
   open with the error next to the buttons.
```

---

## Suggested batches

| Batch branch | Items | Why together |
|---|---|---|
| `feat/ui-foundation` | 1, 2 | Tokens, `IconButton`, `Tooltip` used by everything else |
| `feat/ui-live-run` | 3, 4 | Same data and polling; the main daily wait |
| `feat/ui-home` | 5, 6 | Same page |
| `feat/ui-stats-settings` | 7, 8 | Small, independent polish |
| `feat/ui-clip-viewer` | 9, 10, 11 | Clips page review flow |

## Not recommended

- Adopting the skill's generated "OLED dark + rose" system or Inter/Playfair: replaces the identity you chose to keep.
- GSAP: the `motion` library covers layout, exit and shared-element needs at a fraction of the weight.
- Scroll-triggered section reveals, page-load choreography on every page, confetti: noise for a daily tool.
- A full shadcn install: CLAUDE.md keeps only `sonner`; two or three local components are enough.
