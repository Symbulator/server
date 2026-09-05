"""#228: the handful of strings that make this build *this* build.

Version 9 and version X are the same application. They are meant to stay
the same application -- X exists to try things that may later cross back
to 9, and it does that by merging `v9/main` regularly. Everything else in
this repository is therefore identical between the two trees, on purpose.

The banner is the one exception, and it has to be. On 2 Sep 2026
PythonAnywhere disabled the `symbulatorx` account for content that "might
be related to phishing activities": X was serving pages byte-identical to
`symbulator.pythonanywhere.com` under a hostname one letter away, from a
second account, which is exactly what an automated scanner reads as a
phishing clone. It was also, quite fairly, indistinguishable to a human.
So X must *look* different, and this file is where that difference is
allowed to live.

**It is the only file the two trees are expected to disagree about.**
Putting the difference here rather than in the template is the whole
point: a fork that edits `templates/index.html` collides with version 9
every time version 9 touches the banner's neighbourhood in a 5,000-line
file, and the resolution is by hand, every time, with a silent failure
mode -- take version 9's side by reflex and X quietly becomes a clone
again, which is the thing that got the account disabled. Two constants in
a file version 9 changes about once a year collide almost never, and when
they do the conflict is two lines and obviously about branding.

**The footer is not one of these, and that is Roberto's ruling of 4 Sep
2026.** The page foot reads the same in a fork -- since #285 *Symbulator
by Roberto Perez-Franco (1999–2026) · Release <stamp>*, naming no
version at all -- and stays that way: version 9 and X are the same application, so
the version the footer names is the version of the code, not of the
site serving it. It was queried the day X was merged up to #255 and
answered *leave it at 9 until there is any substantial difference in X*.
So the list below is the whole list -- a fifth value is a change to make
when X stops being version 9 with a different banner, and not before.
The footer also lives in the shared template, where a fork's edit would
collide on every build (the stamp is rewritten each time), which is the
second reason not to.

So: **do not move these strings back into the markup**, and when merging
`v9/main` into a fork, keep the fork's copy of this file.

Read at request time by the context processor in `app.py`, and at build
time by `build_local.py`, which bakes the values into the offline page --
that page has no Jinja, and the build refuses to emit a `{{ ` it did not
resolve.
"""

#: What follows the wordmark: version 9 shows "9" with the beta mark
#: (#137 tracks removing the beta when 9 leaves beta), version X shows
#: "X". Never translated -- it is part of the name.
BRAND_TM = "9"

#: The beta mark that follows it, drawn at 80% of the numeral's height
#: by `banner.css`. Its own value rather than part of BRAND_TM because
#: it is a separate glyph with its own styling, and because #137
#: removes it when version 9 leaves beta -- at which point this becomes
#: "" and the markup disappears with it. A fork not in beta leaves it
#: empty.
BRAND_BETA = "β"

#: An optional colour for that mark, as a CSS colour.
#:
#: Empty for version 9, which takes the sky blue `banner.css` gives every
#: property. A fork may set one -- version X uses the gold #d9a521 -- and
#: it is rendered as an inline style on the mark alone.
#:
#: Inline, and declared here, precisely so that `banner.css` stays the
#: one shared source of the lockup that all five sites are guarded
#: against. A fork that edited the stylesheet would be back to the
#: problem #228 exists to solve, one file further down.
BRAND_TM_COLOR = ""

#: The line under the wordmark.
#:
#: Empty here on purpose, and that is meaningful rather than missing:
#: empty means "use the translated subtitle in the template", which is
#: what version 9 wants -- its subtitle is a real UI string with a
#: translation in each of the twelve languages, and turning it into a
#: constant would throw those away.
#:
#: A fork sets it to its own tagline, and the template then renders that
#: instead, marked `notranslate`. That suits a fork that is English-only
#: by standing rule, and it keeps the fork's tagline out of the i18n
#: dictionaries entirely -- changing a translated string would orphan its
#: key in all twelve and demand twelve translations.
BRAND_SUB = ""
