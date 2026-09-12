# tools

## Before writing a new one: an entry's keys are not the file's keys (#328)

`circuitbook.parse_book` renames some fields as it reads them, through an
alias table near the top of `circuitbook.py`. Three keys are renamed,
and every other key parses to itself:

| the `.cir` file says | the parsed entry's key is |
|---|---|
| `analysis:` | `domain` |
| `w:` | `omega` |
| `variables:` | `vars` |

(`domain:`, `omega:` and `vars:` are accepted too, and are what the
entry ends up carrying either way. The aliases exist so the file reads
naturally; the front end only ever sees the right-hand column.)

So `entry.get("analysis")` on a parsed entry is **always `None`**, and
`(entry.get("analysis") or "dc")` is always `"dc"`.

That does not fail. It runs every entry in the book as a DC solve, and
since most of them *are* DC, the run comes back clean and looks like a
sweep of the whole book. It cost a session exactly that in Sep 2026: a
new checker reported a complete pass over 310 systems while having
tested one third of what it claimed, and only reading a second field off
the same entry gave it away.

**Read `entry["domain"]`.** The existing tools do -- `verify_lesson.py`
line 91, `circuitbook.py`'s own AC branch -- so copy one of those rather
than the field name you see in the file.

Two more things a new tool wants to know about an entry:

* **`tool:`** is `er`, `th`, `port` or `ex`, and means the entry is not a
  plain solve. Its answers are that tool's, not `dc()`/`ac()`/`fd()`'s,
  so a checker comparing against a plain solve must skip those entries
  rather than mark them wrong.
* **A `domain` of `tr`** is solved in the s-domain and inverted back into
  time, so anything reasoning about the *system* for a transient has to
  stamp `_sources_to_s(desc)` and not `desc` (#176), or it builds a
  hybrid nobody solves.

## `review_schematics.py` — every example, drawn and checked

Renders every entry of every `.cir` book in `../examples/` with
`symbulator.schematic.to_svg` into a browsable HTML gallery, and checks
each drawing: exceptions, overlapping labels, wires through element
bodies or op-amp triangles (measured by instrumenting the canvas, not
by squinting), unusual hop counts, extreme sizes. `report.txt` in the
output folder lists findings worst-first.

    py review_schematics.py                # gallery into the temp folder
    py review_schematics.py C:\some\dir    # or a folder of your choosing

A clean run ends `failed=0 with_issues=0` — the state the Aug 2026
schematic rework left all 322 tutorial circuits in, and the bar any
schematic change should keep. Run it after anything that touches
`schematic.py`.

Since #212 it also checks that **no label touches a symbol or a wire**.
`schematic.py`'s canvas records where each body puts ink (`_Canvas.ink`,
deliberately narrower than the `obstacle` a *wire* has to clear — a
value label sits just above its own body by design), and the harness
compares every label box against it.

That check is an *estimate*: text metrics are guessed from a character
count, and the ink boxes come from the path geometry. It has a blind
spot it cannot close — the stroke. `pixel_clearance.py` below is what
closes it.

**It reviews the working tree**, preferring a sibling `repos/solver`
checkout over the installed package. That sentence was in this file
before #212 and was not true: the path had one `os.path.dirname` too
many and pointed at `Symbulator/solver`, which has never existed, so
the harness silently reviewed whatever `pip` had installed and could
not see an edit to `schematic.py` at all. It says so on stderr now if
the checkout is ever missing, rather than falling back in silence.

## `pixel_clearance.py` — the same question, asked of the pixels

`review_schematics.py` measures label clearance from geometry.
`pixel_clearance.py` measures it from the rendered picture: headless
Chrome draws each schematic with the labels forced to pure red and
every stroke to pure blue, and the blue mask is grown a ring at a time
until it meets the red one.

    py pixel_clearance.py                  # a five-drawing sample
    py pixel_clearance.py --all            # every entry of every book
    py pixel_clearance.py --all --min 3    # non-zero exit below 3px

It exists because geometry got it wrong in a way geometry could not
see. `stroke-linejoin="miter"` runs a zigzag's peak **2.2px past its
own vertex**, so `REACH["r"]` — the path's 9px amplitude — understated
the resistor's ink by a quarter, and labels the fast harness called
3px clear were 1px clear on screen, which reads as touching. Every
`REACH` entry is an ink figure now, half a stroke wider than its path
and the resistor wider still, and `GAP` is 4px.

It has earned its keep twice. The mitre above was the first. The second
was **descenders**: labels were placed so their *baseline* sat GAP above
a symbol, and a baseline is not an edge -- `-4j`, `1/gx` and the node
names `ag`/`bg`/`cg` that the three-phase chapters use all hang below
it, which left 21 of the 330 example drawings with 1-2px of air. The
font's extents are measured now (`LABEL_ASCENT`, `LABEL_DESCENT`,
`CAP_DESCENT` in `schematic.py`; ascent 9.75, descent 3.12, and
capitals still drop 1.25 for Q's tail) and every placement is stated as
ink-clears-ink.

**Run it over `--all`, not a sample.** The eight hand-picked drawings
that proved the mitre fix were all clean while 21 others were not.

Three traps, all paid for once:

* **Read a pixel's ink from the channel it removes from white** — red
  ink takes the blue channel down, blue ink takes the red channel down.
  Classifying by "are the R and B values close?" reads a faint
  antialiased red (255,243,243) as carrying both inks, which made the
  first version report every drawing as a collision.
* **Prove it can fail.** Forcing `schematic.GAP` to −14 must make it
  report; a clearance checker that cannot be made to complain is
  measuring nothing.
* **Say which label.** A number per drawing tells you something is
  wrong and nothing about what; naming the nearest label turned the
  descender hunt from a guess into a five-minute diagnosis. It also
  reconfigures stdout to UTF-8, having once died printing a `µ` after
  a thirty-five minute run.

Needs Chrome, numpy and Pillow — none of which the package depends on,
which is why this is separate from the fast harness. About a second per
drawing, so `--all` is a twenty-minute run. Use `review_schematics.py`
routinely and this after anything that changes a symbol's shape, its
stroke width, or where a label sits.

## `verify_lesson.py` — the examples, checked against the book

Runs every entry of one `.cir` file in `../examples/` through the real app
and prints what a reader would see beside what that lesson's chapter
prints. This is how the 322 worked examples were verified.

    py verify_lesson.py Lesson_03
    py verify_lesson.py Lesson_03 --only 13
    py verify_lesson.py Lesson_03 --quiet      # only entries that disagree

The expected answers live beside it as `Lesson_*.expected.json`, one file
per lesson, keyed by entry name. They were transcribed from the chapters;
where an answer changes, the chapter and the JSON have to move together.

**It reads `nodes`, `elements` and `extras`, never `values`.** An earlier
version read `values`, which is the exact substitution dictionary the
Evaluate card is fed — it ignores the Rounding and SI settings entirely, so
an entry with the wrong rounding looked identical to one with the right
rounding and every check passed. What a reader actually sees is the
formatted `plain` strings, and those are what this compares.

Two categories in the output are not the same thing:

* **"entr(ies) with a problem"** — a real disagreement, or an entry that
  does not solve. This should be 0, with one exception: Lesson 4's
  Bo2 Example 3.11 is *supposed* to fail, because the chapter teaches it
  as a failure and then shows the way round it.
* **"whose book names were not found among the answers"** — the expected
  text mentions a name the run did not produce. Usually the answer comes
  from a mini-tool an input file cannot invoke (the gain examples), so it
  is recorded in the entry's `note:` instead. Worth reading, not alarming.

A full sweep takes roughly half an hour. Run it after anything that touches
value parsing, formatting, or the solver.

## `i18n.py` — the thirteen languages (#197, #202, #203, #206)

The app speaks thirteen languages by way of a **client-side dictionary applied
in the page**, and it has to: two of the three builds are static files with
Pyodide in the tab, so anything server-rendered would translate the hosted
app and leave the downloaded one in English. This script is the machinery
around that dictionary.

    py tools/i18n.py scan     # how many units, and where
    py tools/i18n.py tag      # give every unit its data-i18n key,
                              # and regenerate i18n/en.json
    py tools/i18n.py pack     # write the dictionaries into the templates
    py tools/i18n.py check    # is everything in step?

**`en.json` is generated, not written.** The English lives in the template
markup and in the fallback argument of every `t()` / `tv()` call; a second
hand-kept copy of it would only drift. The other twelve are hand-written and
are the only files a translator edits — then `pack`, and the block between
the `BEGIN/END i18n dictionaries` markers in each template is rewritten.
Each page carries only the keys it asks for.

A **translation unit** is the outermost element containing text whose
descendants are all inline, so a whole paragraph is one entry and a
translation may move the `<code>` or the `<a>` where its own word order
wants them. Three things are never units: anything inside
`class="notranslate"` (the wordmark, the build stamp, the syntax columns of
the reference tables), anything spanning a `server-only` marker (the
offline build deletes those blocks, and a dictionary copy would paint them
back), and the regions the page's own JavaScript writes into.

The **key** is a readable slug plus four hex of the English's SHA-1, so
editing the English mints a new key and the stale translation shows up as
an orphan in `check` instead of quietly staying on screen.

What `check` catches, all of it learned the hard way:

* a unit that is not tagged, or tagged with a key its English no longer
  hashes to;
* `en.json` disagreeing with the page, and orphans in any language;
* a translation that drops an `id`, a `href` or a `%{slot}` the English
  had — each of those fails silently at runtime, because dictionary values
  are written into the page as innerHTML;
* a translation that introduces a `<script>` or `<style>`;
* `t(key, ...)` with a **variable** key, which `en.json` can never see, so
  the string would fall back to English in all twelve languages with
  nothing to say so;
* a new element kind or two-port description in `symbulator_ui.py` that no
  language has a word for yet.

`pack` escapes `<`, `>`, `&` and `{` inside every string, so no translation
can close the script element or grow a Jinja construct — `{#` in a template
took every server page down on 30 Aug 2026, and twelve languages of new text
is a lot of new opportunity.

### `check` does not measure the ribbon — you have to

`i18n.py check` compares keys and structure. It knows nothing about
pixels, and the ribbon is where a translation actually breaks: `banner.css`
caps `.subbar nav` at one line-box with `overflow: clip`, so a label too
wide for the row does not wrap visibly and does not scroll — the overflow
is **silently gone**, usually taking the Tutorial link with it.

Ukrainian hit this on 31 Aug 2026 (#203/#206): *Локальний застосунок* is
149px against English's 62px. It shipped past the first check because that
check tested whether the link's right edge had passed the nav's right edge
— and a wrapped element is not to the right, it is *below*.

Measure it on the axis it fails on. For each language, at 375, 481, 520,
768 and 1100px:

```js
nav.scrollHeight - nav.clientHeight   // must be 0
[...nav.children].filter(e =>
  e.getBoundingClientRect().bottom - nav.getBoundingClientRect().top
    > nav.clientHeight + 1)           // must be empty
```

481px is the band to watch: it is the narrowest viewport that still shows
the *wide* labels. The fix is nearly always the wording, not the CSS.

## `check_example_images.py` — the examples' pictures (#219)

An entry in `examples/*.cir` may carry an `image:` line: a link to a
picture of its circuit, shown in a card of its own when the entry is
picked. The pictures are the **tutorial's own figures**, and they live in
the *docs* tree (`Documentation/assets/`), served from
`learn.symbulator.com`. Nothing else joins the two trees, so nothing else
notices when a figure is renamed or removed on the docs side.

```
py tools/check_example_images.py           # every link names a real file
py tools/check_example_images.py --live    # ...and the live site serves it
```

The reason it exists is that this particular breakage is **invisible**.
The app hides the picture's card when an image fails to load — deliberately,
so that an entry without a picture, a dead link and an offline reader all
look the same: no card, no gap, no broken-image box. That is the right
behaviour on the page and the worst possible behaviour for a fault, because
a missing circuit looks exactly like an entry that never had one.

`build_local.py` runs the plain form on every build. It is soft on a
missing docs tree (that tree is a neighbour, not a dependency of the offline
build) and hard on a link it can actually check and finds broken.

**`--live` is the one to run before shipping**, and it is slow on purpose.
An early version fetched all 248 pictures eight at a time and reported 23
dead links; every one of them served 200 when asked again on its own. It
now retries, runs three at a time, and distinguishes an HTTP status — which
it believes — from a transport failure, which it reports as "could not
reach" rather than as a broken link. A check that cries wolf gets ignored,
and then it is not a check.


## `check_export_fields.py` — an entry survives the file (#250)

    python tools/check_export_fields.py

Every entry the app saves goes from `inputsSnapshot()` in the template,
through one of two exports (`app.py`'s `/api/export`, or `export_book` in
the offline bridge), into `circuitbook.format_book`, and back out through
`circuitbook.parse_book`. Until #250 each export kept its own list of the
fields to carry across, and the two had drifted apart in different
directions — the server dropped `defines` and `evaluate_conditions`, the
offline build dropped `plotx` and `defines`, and neither carried `polar`
or `show_equations`. Nothing showed inside a session, because the entries
live in the browser; the values went missing only in a downloaded file.

There is one list now, derived from the parser's own tables, and one
sanitiser (`circuitbook.clean_circuits`) behind both exports. This script
proves it from three directions: a circuit with every field set must make
the write–parse–clean–write round trip with every value intact and the
file text stable byte for byte; every key `inputsSnapshot()` returns must
be one the format knows; and both exports must still call the shared
function. It exits 1 naming the field on the first failure.

`build_local.py` runs it on every build, hard. To watch it go red, delete
`"plotx"` from `circuitbook._KEYS`, or add `bogus: 1,` to
`inputsSnapshot()` — it was proved that way, four sabotages, before it was
trusted.


## `check_example_plots.py` — every example's plot, run (#255)

    py tools/check_example_plots.py            # every book
    py tools/check_example_plots.py Lesson_11  # one book

Since #255, 67 of the 330 built-in entries carry a plot — a time plot, a
Bode plot, a Bode plot of a typed H(s), or a DC sweep — in the same
`plottool` / `plotkey` / `plotx` / `plotmin` / `plotmax` / `plotpoints`
keys the app saves. Each was sized from the entry's own answer when it
was written, but a plot is only proved by running it, and nothing else
here does: `verify_lesson.py` posts the solve, not the plot, and the
schematic harnesses draw the circuit.

This script calls, for every entry with a `plottool:`, the same ui
function the app's endpoint calls (`plot_time_ui`, `bode_ui`,
`bode_tf_ui`, `sweep_ui`) with the entry's own key, range and point
count, its Expert Mode extras, and its Define lines expanded first. It
fails on a `plottool` the menu does not offer (`time` shipped in two
entries for a day; the menu's value is `plot_time`), a plot the engine
refuses (quoting the engine's reason), samples that are not finite, a
flat trace — a horizontal line is a wrong key or a wrong range — or a
range that does not run low to high. Every failure is listed, so a book
can be fixed in one pass.

Not wired into `build_local.py`: it solves 67 circuits and takes a minute
or two. Run it after touching a book, the plot tools or
`symbulator_ui.py`. It was proved red three ways on Lesson 11 before it
was trusted: a wrong key, `time`, and an inverted range.

## `check_solveq_conditions.py` — the Solve card reads `name = value` as Expert Mode does (#433)

Runs NR12's Example 3.10 — the Wheatstone bridge with the galvanometer
as a short — through the real app, `solve_ui` then `solveq_ui` on the
values the page holds, and asserts that a condition `R_3 = 10`
substitutes, that `R_3 = 10` as a second equation is solved rather than
dropped, that the answer stays the symbolic `4 R_3` when nothing sets
`R_3`, that an inequality still filters, that an equality on the unknown
itself still filters, and that the Solve card and Expert Mode agree to
the digit.

    py tools/check_solveq_conditions.py
    py tools/check_solveq_conditions.py --prove-red    # both halves off; must FAIL

Run it after touching `solveq_ui`, `_equality_binding` or
`_conditions_hold` in `symbulator_ui.py`.
