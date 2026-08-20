# Moving these notes into Claude Design — operator handoff

This is an **operations note**, not a presentation note: it records how the
fifteen slide-source notes in this directory get from the repo into an existing
Claude Design presentation project, so the next pass (a new note, a re-cut of
the deck) doesn't rediscover it. The notes themselves stay the source of truth;
what goes to Claude Design is a generated, throwaway flattening of them.

## 1. Build the upload bundle

```sh
build-notes/claude/bundle_for_claude_design.sh            # → build-notes/claude/dist/ (gitignored)
build-notes/claude/bundle_for_claude_design.sh ~/Desktop  # or anywhere else
```

It produces `claude-code-build-notes.zip` containing:

| Path | What it is |
|---|---|
| `claude-code-build-notes.md` | One file: `README.md` (arc, evidence convention, one-paragraph story) followed by all 15 notes stitched **in presentation order**, each preceded by an `<!-- source: build-notes/claude/NN-*.md -->` marker, then `screenshots/README.md`. ~200 KB. |
| `screenshots/*.png` + `README.md` | The eight terminal screenshots and their captions (a caption is the intended speaker note for the slide that uses it). Relative `screenshots/…` links inside the notes keep resolving. |
| `notes/` | The same 15 notes + README as separate files, for attaching one at a time if a project balks at one big file. |

Presentation order is the README's *Recommended presentation arc* — 12 → 04/02/15
→ 01 → 03 → 05/07/13 → 06/08/09 → 10 — plus the two notes the arc omits, placed
where the notes themselves say they belong: **14** right after 12 (it is 12's
"output side of the loop" complement) and **11** after 01 (the sweep companion to
the workflows note). Change `ORDER=(…)` in the script if the arc changes; the
README is the source of truth for the arc, the script only mirrors it.

Why a generated bundle and not the raw directory: Claude Design gets one file to
read instead of sixteen, the arc is already applied so it can't re-derive a
different one, and nothing gets checked in that could drift from the notes —
`dist/` is gitignored for exactly that reason. Bash rather than zsh, deliberately:
`for n in $ORDER` does not word-split under zsh, and the first hand-run of the
stitch produced a "bundle" that was only the README.

## 2. Why this is a UI upload (the headless avenues, ruled out)

Per the CLAUDE.md "headless first" rule, the tool path was checked before
concluding this is a drag-and-drop:

- The Claude Code `DesignSync` tool (`/design-sync`) syncs a **local component
  library** into a Claude Design **design-system** project — tokens, `@dsCard`
  component previews, guidelines pages, ui kits. `list_projects` returns only
  writable *design-system* projects (that project type is fixed at creation), so
  a presentation project is invisible to it and cannot be written by it. It is
  the wrong tool for a deck. It would be the right tool if we ever extract the
  console's design language (the D57 canvas template, heading strip, gray/white
  surface alternation, chips and badges from `src/console/static/index.html`)
  into a synced library so Claude Design mockups of new console sections match
  the real console — a separate piece of work, not this one.
- The notes are on GitHub, but pasting URLs relies on the design tool fetching
  them; a local upload is the sure thing.

So: open the presentation project in Claude Design, add the zip (or the `.md`
plus the PNGs) as **project files**, and send the prompt below.

## 3. The prompt to send with the zip

Copy the block verbatim. It relies on the notes' own structure — the
`## Put this in the presentation` block (**Slide headline / bullets / Visual /
Do not**), the evidence grades in `## Evidence and limits`, and the
`<!-- source -->` ids — so its job is to make Claude Design *obey* the specs the
notes already carry, not reinterpret them. The last paragraph forces a
plan-before-build reply: with fifteen specs, a wrong reading of the arc costs a
full regeneration; a slide list costs one message.

```text
I've attached claude-code-build-notes.zip. It is slide SOURCE MATERIAL for a new
section of this presentation: "How this lab was built with Claude Code" — the
build-process story behind the A2A Interop Lab that the rest of the deck describes.
Please add that section to this existing deck; do not restructure or restyle the
slides that are already here — match their visual system, and use the deck's own
section-divider treatment where you need one.

WHAT'S IN THE ZIP
- claude-code-build-notes.md — one file: a README (arc, evidence convention,
  one-paragraph story) followed by 15 notes, already stitched IN PRESENTATION
  ORDER. Each note begins with a `<!-- source: build-notes/claude/NN-*.md -->`
  comment; treat that as the note's id.
- screenshots/*.png — 8 terminal screenshots. screenshots/README.md carries a
  caption per file; each caption is the intended speaker note for the slide
  that uses it.
- notes/ — the same 15 notes as separate files (reference only; ignore if the
  combined file is enough).

HOW TO TURN THE NOTES INTO SLIDES
1. Follow the README's "Recommended presentation arc" (8 movements) as the
   section's structure: one divider slide per movement, titled with the
   movement name, then the note slides that belong to it. Notes 14 and 11 sit
   in the arc where the file order puts them.
2. Every note ends with a "## Put this in the presentation" block. That block
   is the slide spec — use it, don't re-derive:
   - **Slide headline:** → the slide title, verbatim or lightly tightened.
   - the three bullets → the slide body (max three; no additions).
   - **Visual:** → build the described visual. Where it names a screenshot,
     use the PNG from screenshots/ and the called-out quote it mentions.
   - **Do not:** → a hard constraint on that slide's copy. Obey it.
   One slide per note by default; split only where the Visual block clearly
   describes two panels that won't fit.
3. Use "## Engineering takeaway" for the speaker notes' first line, and add
   the "## Teaching points for the deck" bullets (where a note has them) to
   the speaker notes, not the slide.
4. Evidence grades MUST survive onto each slide as a small footer tag:
   Repository-backed / Vendor-documented / Observed in this project — take
   the grade from the note's "## Evidence and limits" section. Anything graded
   "Observed" is phrased as an observation from this build ("we saw…", "in
   this project…"), never as a general product claim.
5. Keep file paths, ADR ids (D<n>) and numbers exactly as written; they are
   citations. Never invent metrics, quotes, or vendor claims that aren't in
   the notes.
6. Close the section with a slide from the README's "The one-paragraph story"
   (three disciplines: written decision log, saved multi-agent workflows,
   clean seams for delegation), then the note-10 cost slide as the final one
   — the README calls it the natural closing slide.

AUDIENCE AND TONE
Engineers and engineering leaders evaluating coding agents and agent
platforms; Claude familiarity optional. Every product-specific point is paired
with the reusable engineering principle it demonstrates — keep that pairing on
the slide.

Before you generate: read the README section first, then reply with (a) the
proposed slide list (movement → slide title → source note → screenshot used, if
any) and (b) any spec you can't honour, and wait for my OK before building the
slides.
```

## 4. Re-running after the notes change

Edit the note (and its `## Put this in the presentation` block), re-run the
script, re-upload the zip, and tell Claude Design which `<!-- source -->` ids
changed so it re-cuts only those slides. Screenshots follow the convention in
`screenshots/README.md` — add the file *and* its caption row, or the slide gets
an image with no speaker note.
