# -*- coding: utf-8 -*-
"""The app's translation machinery (#197).

Symbulator ships as three builds of one template and two of them run no
Python at all -- the install site and the downloaded ZIP are static files
with Pyodide in the tab.  So the interface cannot be translated the usual
server-side way: no Flask-Babel, no gettext, no per-language template.  It
has to be a **client-side dictionary applied in the page**, which is what
this script builds.

Three jobs, in the order they run:

``tag``
    Walk the markup, decide which elements are translation units, and
    write a ``data-i18n="<key>"`` attribute into each one's start tag.
    Idempotent: an element that already carries the right key is left
    alone.

``pack``
    Read ``i18n/*.json`` and write them into the template between the
    ``BEGIN/END i18n`` markers as one inline ``<script>``.  Inline
    because the offline build is a single page opened from a folder: it
    cannot fetch a dictionary it might not have cached.

``check``
    Fail if the template's English has drifted from ``i18n/en.json``, if
    the packed block is stale, or if a language is missing keys.

A translation unit is the OUTERMOST element that contains text and whose
descendants are all inline (``<code>``, ``<em>``, ``<a>`` ...).  Keying the
whole paragraph rather than its text nodes is what lets a translation move
the ``<code>`` or the ``<a>`` to where its own sentence wants it -- word
order is the first thing that changes between languages, and a
per-text-node scheme freezes the English one.

Three things are deliberately never a unit:

* anything inside ``class="notranslate"`` -- the wordmark, the build
  stamp, the syntax columns of the reference tables;
* anything spanning a ``server-only`` marker, because ``build_local.py``
  deletes those blocks from the offline page and a dictionary entry
  holding a copy would paint them straight back in;
* the regions the page's own JavaScript writes into, listed in
  ``SKIP_IDS``.  Those translate through ``t()`` instead.

The key is a readable slug plus four hex of the English's SHA-1, so
editing the English produces a NEW key and the stale translation shows up
as an orphan in ``check`` instead of quietly staying on screen.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent
I18N = SERVER / "i18n"
# #204: the dictionaries are no longer inlined into the templates.
# `pack` emits one JavaScript file per language here, and the page
# loads only the language actually in use. These are generated --
# edit i18n/<lang>.json, never dist/<lang>.js.
DIST = I18N / "dist"

TEMPLATES = ["templates/index.html", "templates/eqsheet.html"]

# The offline builds carry markup and script this repo does not: the boot
# bar, the install bar, and the Pyodide bridge's own messages, all injected
# by build_local.py from constants. They are reader-facing, so they are
# translated too -- which means their keys have to be collected from that
# file and packed into THIS template, since the offline page is generated
# from it. The path is the sibling layout build_local.py itself requires.
LOCAL_BUILD = SERVER.parent / "local" / "build_local.py"


def local_build_source() -> str:
    return (LOCAL_BUILD.read_text(encoding="utf-8")
            if LOCAL_BUILD.exists() else "")

# Every language the app speaks, in menu order, with the name each one
# calls itself: a reader looking for their own language is looking for
# the word they would write, not its English name.
LANGS = [
    ("en", "English"),
    ("es", "Espanol"),
    ("eo", "Esperanto"),
    ("fr", "Francais"),
    ("de", "Deutsch"),
    ("pt", "Portugues"),
    ("zh", "Zhongwen"),
    ("ja", "Nihongo"),
    ("ko", "Hangugeo"),
    ("id", "Bahasa Indonesia"),
    ("hi", "Hindi"),
    ("bn", "Bangla"),
    ("uk", "Ukrainska"),
]
TARGETS = [c for c, _ in LANGS if c != "en"]

INLINE = {
    "a", "abbr", "b", "br", "code", "em", "i", "kbd", "s", "samp", "small",
    "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
}
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
NEVER_UNIT = {"code", "pre", "kbd", "samp", "var", "svg", "path", "textarea"}
OPAQUE = {"script", "style", "svg"}

SKIP_IDS = {
    "results", "out", "err", "schematic", "plotbox", "solutionPick",
    "examplesList", "entriesList", "eqTable", "varTable", "residuals",
    "status", "evalOut", "solveqOut", "miniOut", "spiceOut", "noteBox",
    "siNote", "roundNote", "expertEqFlag",
}

TEXT_ATTRS = ("placeholder", "title", "aria-label", "alt")

TAG_RE = re.compile(
    r"<(/?)([a-zA-Z][-a-zA-Z0-9]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# Digits belong in the name class: without them `data-i18n` parses as the
# attribute `n`, every element looks untagged, and `check` reports the
# whole page as stale on a file that is perfectly in order.
ATTR_RE = re.compile(r"([-a-zA-Z0-9_:]+)\s*=\s*\"([^\"]*)\"")

BEGIN = "<!-- BEGIN i18n dictionaries -->"
END = "<!-- END i18n dictionaries -->"


class Node:
    __slots__ = ("tag", "attrs", "open_start", "open_end", "close_start",
                 "parent", "kids", "text")

    def __init__(self, tag, attrs, open_start, open_end, parent):
        self.tag = tag
        self.attrs = attrs
        self.open_start = open_start
        self.open_end = open_end
        self.close_start = None
        self.parent = parent
        self.kids = []
        self.text = 0


def parse(body: str, offset: int = 0):
    """A deliberately small tag walker.

    Not an HTML parser and not trying to be: it needs source offsets so
    ``tag`` can be surgical, and these two templates are hand written,
    well formed, and free of ``<`` inside attribute values.
    """
    root = Node("#root", {}, 0, 0, None)
    cur = root
    nodes = []
    pos = 0
    blanked = COMMENT_RE.sub(lambda m: " " * len(m.group(0)), body)
    opaque_until = None
    for m in TAG_RE.finditer(blanked):
        if opaque_until is not None:
            if m.group(1) and m.group(2).lower() == opaque_until:
                opaque_until = None
            pos = m.end()
            continue
        tag = m.group(2).lower()
        chunk = blanked[pos:m.start()]
        if chunk.strip():
            cur.text += len(chunk.strip())
        pos = m.end()
        if not m.group(1):
            if tag in OPAQUE:
                opaque_until = tag
                continue
            attrs = dict(ATTR_RE.findall(m.group(3)))
            n = Node(tag, attrs, m.start() + offset, m.end() + offset, cur)
            cur.kids.append(n)
            nodes.append(n)
            if tag in VOID or m.group(4):
                n.close_start = m.end() + offset
                continue
            cur = n
        else:
            walk = cur
            while walk is not root and walk.tag != tag:
                walk = walk.parent
            if walk is not root:
                walk.close_start = m.start() + offset
                cur = walk.parent
    return root, nodes


def subtree_text(n: Node) -> int:
    return n.text + sum(subtree_text(k) for k in n.kids)


def all_inline(n: Node) -> bool:
    return all(k.tag in INLINE and all_inline(k) for k in n.kids)


def is_nt(n: Node) -> bool:
    return "notranslate" in (n.attrs.get("class") or "").split()


def has_nt_inside(n: Node) -> bool:
    return any(is_nt(k) or has_nt_inside(k) for k in n.kids)


def in_skipped(n: Node) -> bool:
    while n is not None:
        if n.attrs.get("id") in SKIP_IDS or is_nt(n):
            return True
        n = n.parent
    return False


def units(root: Node, src: str):
    out = []

    def walk(n: Node):
        for k in n.kids:
            if k.tag in NEVER_UNIT or in_skipped(k):
                continue
            if k.close_start is None:
                walk(k)
                continue
            inner_src = src[k.open_end:k.close_start]
            if (subtree_text(k) and all_inline(k)
                    and not has_nt_inside(k)
                    and "server-only" not in inner_src
                    and has_words(strip_markup(inner_src))):
                out.append(k)
            else:
                walk(k)

    walk(root)
    return out


def strip_markup(s: str) -> str:
    return COMMENT_RE.sub("", TAG_RE.sub("", s))


def has_words(text: str) -> bool:
    """Two letters or more -- counted, not required to be adjacent.

    The group headings are spaced capitals, `[ I N P U T S ]`, so a
    two-consecutive-letters test reads them as having no words at all and
    leaves the three loudest headings on the page in English.
    """
    return len(re.findall(r"[A-Za-z]", text)) >= 2


def clean(s: str) -> str:
    """The text as the dictionary stores it: comments gone, edge
    whitespace gone, runs of whitespace collapsed to one space.

    Collapsing matters more than it looks.  The template wraps its prose
    at 72 columns, so the same sentence indented differently would key
    differently, and a translator would be handed the same words twice.
    """
    s = COMMENT_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def key_for(text: str, tag: str) -> str:
    words = re.findall(r"[A-Za-z]+", strip_markup(text).lower())
    slug = "-".join(words[:4])[:28].strip("-") or tag
    slug = unicodedata.normalize("NFKD", slug).encode("ascii", "ignore").decode()
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:4]
    return f"{slug}.{h}"


def scan(path: Path):
    """(source, [(node, kind, english)]) for one template."""
    src = path.read_text(encoding="utf-8")
    b0 = src.index("<body>")
    b1 = src.rindex("</body>") + len("</body>")
    root, nodes = parse(src[b0:b1], offset=b0)
    found = []
    for u in units(root, src):
        found.append((u, "text", clean(src[u.open_end:u.close_start])))
    for n in nodes:
        if in_skipped(n):
            continue
        for a in TEXT_ATTRS:
            v = n.attrs.get(a)
            if v and re.search(r"[A-Za-z]{2}", v):
                found.append((n, a, v))
    return src, found


# ---------------------------------------------------------------- tag ---

def tag_templates(write: bool) -> tuple[dict, list]:
    """Give every unit its data-i18n attribute; return the English map."""
    en = {}
    problems = []
    for rel in TEMPLATES:
        path = SERVER / rel
        src, found = scan(path)
        edits = []
        for node, kind, text in found:
            k = key_for(text, node.tag)
            if k in en and en[k] != text:
                problems.append(f"key collision on {k}")
            en[k] = text
            attr = "data-i18n" if kind == "text" else f"data-i18n-{kind}"
            if node.attrs.get(attr) == k:
                continue
            edits.append((node, attr, k))
        if not write:
            if edits:
                problems.append(
                    f"{rel}: {len(edits)} element(s) not tagged or tagged "
                    f"with a stale key -- run tools/i18n.py tag")
            continue
        # Right to left, so earlier offsets stay valid.
        out = src
        for node, attr, k in sorted(edits, key=lambda e: -e[0].open_start):
            head = out[node.open_start:node.open_end]
            existing = re.search(r'\s' + re.escape(attr) + r'="[^"]*"', head)
            if existing:
                head = head[:existing.start()] + head[existing.end():]
            # Last, not first. The markup still reads `<p class="hint"
            # data-i18n="...">` rather than opening with bookkeeping, and
            # every string in this repo that matches on the START of a tag
            # -- build_local.py has several -- goes on matching.
            insert = len(head) - (2 if head.endswith("/>") else 1)
            head = head[:insert] + f' {attr}="{k}"' + head[insert:]
            out = out[:node.open_start] + head + out[node.open_end:]
        if out != src:
            path.write_text(out, encoding="utf-8")
    return en, problems


# --------------------------------------------------------------- pack ---

def js_string(obj) -> str:
    """JSON that can never grow a tag or a Jinja construct.

    ``<`` would let a translation close the script element; ``{`` is how
    Jinja's comment, block and expression openers all start, and one of
    those inside an HTML comment took every server page down on 30 Aug
    2026 while the offline builds -- which never see Jinja -- stayed
    green.  Escaping both at the source means no translation, in any of
    eight languages none of us can proofread as code, can do it again.
    """
    esc = {"<": "\\u003c", ">": "\\u003e", "{": "\\u007b", "&": "\\u0026"}
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"))
    # Inside string literals only -- the braces holding the object together
    # are structure, and escaping those produces a script that does not
    # parse at all (which is how this was found).
    out = []
    in_str = False
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                out.append(s[i:i + 2])
                i += 2
                continue
            if c == '"':
                in_str = False
            else:
                c = esc.get(c, c)
        elif c == '"':
            in_str = True
        out.append(c)
        i += 1
    return "".join(out)


def load(lang: str) -> dict:
    p = I18N / f"{lang}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def keys_used_by(src: str) -> set:
    """The keys one template actually asks for.

    Each page carries only its own share. The Numerical Solver needs
    about fifty strings; shipping it the app's four hundred made its
    page seven times larger than the page itself.
    """
    keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', src))
    keys |= {m.group(2) for m in
             re.finditer(r"(?<![\w.$])(t|tv)\(\s*'([a-zA-Z0-9_.-]+)'", src)}
    if "tSrv(" in src:
        keys |= {"srv." + w for w in srv_vocabulary()}
    return keys


def emitted() -> dict:
    """The generated file for each language, as {code: text}.

    One file per language, carrying the whole dictionary rather than a
    per-page subset. Two reasons: the file is then byte-comparable with
    the source a translator edits (#207), and the Numerical Solver's
    saving -- it uses about sixty of the keys -- is not worth a second
    set of files to keep in step.

    A .js file rather than .json because it is loaded two ways: a
    parser-blocking <script> at boot, and an injected <script> when the
    reader picks a language. One format serves both; JSON would need a
    second copy of every file for the boot path to work.
    """
    out = {}
    for code in TARGETS:
        d = {k: v for k, v in load(code).items() if v}
        out[code] = ("// Generated by tools/i18n.py -- do not edit.\n"
                     "// Source: i18n/%s.json\n"
                     "window.SYMB_I18N_LANG = %s;\n"
                     "window.SYMB_I18N_D = %s;\n"
                     % (code, js_string(code), js_string(d)))
    return out


def stamp(files: dict) -> str:
    """A short hash of every dictionary, used as the ?v= on the URL.

    It changes when any translation changes, which is exactly when a
    cached copy has to be given up. The service worker ignores query
    strings (`ignoreSearch: true`), so this does nothing offline -- there
    CACHE_VERSION governs, as it does for every other file.
    """
    h = hashlib.sha256()
    for code in sorted(files):
        h.update(code.encode("utf-8"))
        h.update(files[code].encode("utf-8"))
    return h.hexdigest()[:10]


def pack_block(rel: str) -> str:
    """The block that goes between the markers: a loader and a stamp.

    It lives in the <head> and it is deliberately parser-blocking.
    `applyLang()` runs at boot BEFORE the page takes any element
    reference, because it replaces innerHTML and would otherwise leave
    those references pointing at detached nodes. A fetch would defer it
    past that line. So the boot language is loaded here, ahead of the
    body. A language chosen later is fetched instead, which is safe --
    by then the references are long taken.
    """
    files = emitted()
    codes = sorted(files)
    lines = [
        BEGIN,
        "<!-- Generated by tools/i18n.py -- do not edit between the markers;",
        "     edit i18n/<lang>.json and run `python tools/i18n.py pack`. -->",
        "<script>",
        "// #204: the dictionaries are files now, not a payload. Only the",
        "// language actually in use is ever loaded, and English is never",
        "// loaded at all -- it is this page's own markup.",
        "//",
        "// The <script> written below is parser-blocking on purpose: see",
        "// pack_block() in tools/i18n.py for why it cannot be a fetch.",
        'window.SYMB_I18N_V = ' + js_string(stamp(files)) + ';',
        '// Rewritten to a relative path by build_local.py: the offline page',
        '// sits at the root of its own folder, and the server serves the',
        '// app at / and the Numerical Solver at /eqsheet/, so only a',
        '// root-absolute path works for both of those.',
        'window.SYMB_I18N_BASE = "/i18n/";',
        'window.SYMB_I18N_CODES = ' + js_string(codes) + ';',
        "(function () {",
        "  var lang = null;",
        "  try { lang = localStorage.getItem('symbulator-lang'); } catch (e) {}",
        "  if (!lang || lang === 'en'",
        "      || window.SYMB_I18N_CODES.indexOf(lang) < 0) { return; }",
        "  document.documentElement.setAttribute('lang', lang);",
        "  document.documentElement.classList.add('i18n-pending');",
        "  // A hidden page that never comes back is far worse than a flash,",
        "  // so the hide expires on its own whatever happens below --",
        "  // including a 404, which simply leaves the reader in English.",
        "  setTimeout(function () {",
        "    document.documentElement.classList.remove('i18n-pending');",
        "  }, 2000);",
        "  document.write('<scr' + 'ipt src=\"' + window.SYMB_I18N_BASE",
        "                 + lang + '.js?v=' + window.SYMB_I18N_V",
        "                 + '\"><\\/scr' + 'ipt>');",
        "})();",
        "</script>",
        END,
    ]
    return "\n".join(lines)


def pack(write: bool) -> list:
    problems = []
    files = emitted()
    DIST.mkdir(parents=True, exist_ok=True)
    for code, text in files.items():
        p = DIST / f"{code}.js"
        cur = p.read_text(encoding="utf-8") if p.exists() else None
        if cur == text:
            continue
        if not write:
            problems.append(f"i18n/dist/{code}.js is stale -- run "
                            f"`python tools/i18n.py pack`")
            continue
        p.write_text(text, encoding="utf-8")
    if write:
        # A language removed from LANGS leaves its file behind, and the
        # service worker would go on caching a dictionary nothing loads.
        for p in DIST.glob("*.js"):
            if p.stem not in files:
                p.unlink()
    for rel in TEMPLATES:
        block = pack_block(rel)
        path = SERVER / rel
        src = path.read_text(encoding="utf-8")
        a = src.find(BEGIN)
        b = src.find(END)
        if a < 0 or b < 0:
            problems.append(f"{rel}: no i18n markers")
            continue
        cur = src[a:b + len(END)]
        if cur == block:
            continue
        if not write:
            problems.append(f"{rel}: packed dictionaries are stale -- run "
                            f"`python tools/i18n.py pack`")
            continue
        path.write_text(src[:a] + block + src[b + len(END):], encoding="utf-8")
    return problems


# -------------------------------------------------------------- checks ---

STR_START = re.compile(r"""\s*(['"])""")


def _read_js_string(src: str, i: int):
    """The JavaScript string literal starting at `i`, and where it ends."""
    q = src[i]
    j = i + 1
    buf = []
    while j < len(src):
        c = src[j]
        if c == "\\":
            nxt = src[j + 1]
            if nxt == "u":
                # … and friends. Without this the backslash is simply
                # dropped and the dictionary holds "first" + "u2026".
                buf.append(chr(int(src[j + 2:j + 6], 16)))
                j += 6
                continue
            if nxt == "x":
                buf.append(chr(int(src[j + 2:j + 4], 16)))
                j += 4
                continue
            buf.append({"n": "\n", "t": "\t", "'": "'", '"': '"',
                        "\\": "\\"}.get(nxt, nxt))
            j += 2
            continue
        if c == q:
            return "".join(buf), j + 1
        buf.append(c)
        j += 1
    raise ValueError("unterminated string literal")


def _read_concat(src: str, i: int):
    """A run of string literals joined by `+`, as one string.

    The English fallbacks in the page are wrapped at 72 columns like
    everything else, so most of them arrive as `'...' + '...' + '...'`.
    """
    parts = []
    while True:
        m = STR_START.match(src, i)
        if not m:
            break
        text, i = _read_js_string(src, m.start(1))
        parts.append(text)
        m2 = re.match(r"\s*\+\s*", src[i:])
        if not m2:
            break
        i += m2.end()
    return "".join(parts), i


# ---------------------------------------------------------------------
# #209: the strings that never reach the dictionary
#
# Everything else `check` does is about a string that is *already* in the
# scheme -- untagged markup, a key whose English has moved, an orphan, a
# dropped id or slot, a variable key. A literal that never calls t() at
# all is invisible to all of it, because there is nothing to compare it
# against. That is how "DC analysis · 12 result(s) · 0.06s" -- the line
# under every set of answers -- stayed English through #197 to #206 and
# was found only by someone reading the page in Chinese.
#
# So this rule works the other way round: take every literal that reaches
# a reader, subtract the ones inside a t()/tv()/tSrv() call, and complain
# about what is left.
#
# It found twenty-one on its first run, and each widening of it found
# more: a two-word filter walked past 'solving…', a three-letter-word
# filter walked past `${key} vs. ${xname}`, and neither looked at
# showNote(), which is not a DOM property at all. Widen it again if you
# find a twenty-second; do not delete it.
# ---------------------------------------------------------------------

#: Where a string becomes something a person reads. showNote() is the
#: app's own; the rest are the DOM's.
READER_SINKS = re.compile(
    r"\.(?:textContent|innerHTML|placeholder|title|alt|value)\s*=(?!=)"
    r"|\b(?:confirm|alert|prompt|showNote)\s*\(")

#: Deliberate exceptions, each one looked at. Keep this list explicit and
#: short: an exception nobody had to write down is an exception nobody
#: checked. Match is on the literal's exact text.
NOT_FOR_READERS = {
    # State and mode values, compared against rather than shown.
    "known", "unknown", "complex", "real", "imag", "any", "pos", "neg",
    "range", "exact", "approx", "both", "solve", "equiv", "sweep", "time",
    "bode", "bode_tf", "dc", "ac", "fd", "tr", "out", "val", "rstcell",
    "least-squares", "bounded", "plot", "schematic",
    # The mathematics, which is never translated.
    "time (s)", "dB", "v_2", "100/(s^2 + 10*s + 100)", "circuits.cir",
    # Markup and layout fragments.
    "result-row", "result-math", "result-name", "msg", "msg bad", "msg ok",
    "badge", "lcd-meta", "error",
}

#: A word a person would read: three or more letters, or one of the short
#: ones that only occur in prose.
_PROSE_WORD = re.compile(r"\b[A-Za-z]{3,}\b|\b(?:vs|of|to|in|is|no|by|at|on)\b",
                         re.IGNORECASE)

#: Not prose: selectors, CSS, URLs, single identifiers, pure punctuation,
#: and TeX handed to MathJax (#280 put \displaystyle at the head of every
#: typeset result, and "displaystyle" is three letters or more).
_NOT_PROSE = re.compile(
    r"^(?:[.#][\w-]+)$"
    r"|^\\\\\("
    r"|^[a-z-]+\s*:\s*[^;]*;?$"
    r"|^https?://"
    r"|^[\w.$-]+$"
    r"|^[^A-Za-z]*$")


def _t_call_spans(src: str):
    """(start, end) of every t()/tv()/tSrv() call, brackets balanced."""
    spans = []
    for m in re.finditer(r"(?<![\w.$])(?:t|tv|tSrv)\s*\(", src):
        i, depth = m.end() - 1, 0
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    spans.append((m.start(), i + 1))
                    break
            i += 1
    return spans


_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
_LITERAL = re.compile(
    r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`""",
    re.S)


def untranslated() -> list:
    """Reader-facing literals in the templates' scripts that skip t()."""
    out = []
    for rel in TEMPLATES:
        text = (SERVER / rel).read_text(encoding="utf-8")
        for sm in _SCRIPT.finditer(text):
            body, off = sm.group(1), sm.start(1)
            spans = _t_call_spans(body)
            for m in READER_SINKS.finditer(body):
                # The statement the sink feeds, to the next semicolon.
                stmt = body[m.end():m.end() + 500].split(";")[0]
                for lm in _LITERAL.finditer(stmt):
                    pos = m.end() + lm.start()
                    if any(a <= pos < b for a, b in spans):
                        continue          # inside a t() call: accounted for
                    raw = next(g for g in lm.groups() if g is not None)
                    lit = raw.strip()
                    if not lit or lit in NOT_FOR_READERS or _NOT_PROSE.match(lit):
                        continue
                    # Judge the prose, not the interpolations or the markup.
                    plain = re.sub(r"\$\{[^}]*\}|<[^>]*>|&[a-z]+;", " ", lit)
                    if not _PROSE_WORD.search(plain):
                        continue
                    line = text.count("\n", 0, off + pos) + 1
                    out.append(f"{rel}:{line}: {lit[:60]!r} reaches a reader "
                               f"without t() -- wrap it, or add it to "
                               f"NOT_FOR_READERS in tools/i18n.py with a "
                               f"reason")
    return out


# ---------------------------------------------------------------------
# #200: message text in the Python that never reaches msg()
#
# The #209 guard asks this of the page's JavaScript. This asks it of the
# engine, and it had to: three hand sweeps of symbulator_ui.py found 34
# messages, then 5, then 8, and each one was sure it was the last. A
# fourth pass, run mechanically, found six more -- including two notes
# that refuse a name outright and the "did you mean" warning, none of
# which any sweep had reached.
#
# A finding is a string literal that reads like a sentence and is
# returned, or appended to a list of notes, without going through
# msg(). Anything inside msg(), _exc_text() or tSrv() is accounted for.
# ---------------------------------------------------------------------

#: The engine's own Python, where its messages live.
MESSAGE_SOURCES = ("symbulator_ui.py", "eqsheet.py")

#: Sentences that are deliberately not messages. Keep this explicit and
#: short, and give each one a reason.
NOT_A_MESSAGE = {
    # A comment line inside the SymPy export -- a .py file the reader
    # downloads and opens in an editor. Its comments are code, and stay
    # English with the rest of the file.
    "# left out (their answers contain delta(t), "
    "which has no numeric value): ",
    # The engine's own phrase, looked up by tSrv() in the page because
    # it carries a node name and cannot be looked up whole (#168).
    "current into port at node ",
}

_MSG_WORD = re.compile(r"\b[A-Za-z][a-z]{1,}\b")
_MSG_FUNCTION_WORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "not",
    "no", "is", "are", "be", "it", "its", "this", "that", "you", "your",
    "with", "from", "at", "as", "but", "so", "than", "then", "there",
    "when", "which", "what", "how", "why", "give", "enter", "could",
    "cannot", "does", "run", "try", "one", "two", "each", "every", "some",
    "needs", "need", "must", "too", "many", "first", "already", "still",
    "was", "were", "has", "have",
}


def _covered_spans(tree):
    """Line spans of every msg() / _exc_text() / tSrv() call."""
    spans = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        if name in ("msg", "_exc_text", "tSrv"):
            spans.append((n.lineno, n.end_lineno))
    return spans


def _literals(node):
    """(line, text) for every string under `node`, f-strings joined."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append((n.lineno, n.value))
        elif isinstance(n, ast.JoinedStr):
            text = "".join(v.value for v in n.values
                           if isinstance(v, ast.Constant)
                           and isinstance(v.value, str))
            if text:
                out.append((n.lineno, text))
    return out


def uncoded_messages() -> list:
    """Sentences the engine hands a reader without a code."""
    out, seen = [], set()
    for rel in MESSAGE_SOURCES:
        path = SERVER / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        spans = _covered_spans(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                target = node.value
            elif (isinstance(node, ast.Expr)
                  and isinstance(node.value, ast.Call)
                  and getattr(node.value.func, "attr", None) == "append"):
                target = node.value
            else:
                continue
            if target is None:
                continue
            for lineno, text in _literals(target):
                if any(a <= lineno <= b for a, b in spans):
                    continue
                if text in NOT_A_MESSAGE:
                    continue
                words = [w.lower() for w in _MSG_WORD.findall(text)]
                if len(words) < 3:
                    continue
                if not any(w in _MSG_FUNCTION_WORDS for w in words):
                    continue
                key = (rel, lineno, text[:40])
                if key in seen:
                    continue
                seen.add(key)
                out.append(f"{rel}:{lineno}: {text[:56]!r} reaches a reader "
                           f"without a code -- wrap it in msg(), or add it "
                           f"to NOT_A_MESSAGE in tools/i18n.py with a reason")
    return out


def js_calls():
    """{key: English} for every t('key', 'English') and tv(...) in the page.

    The English fallback in the code IS the English dictionary: writing it
    twice, once in the call and once in a JSON file, is how the two would
    come to disagree.
    """
    out = {}
    dup = []
    sources = [(SERVER / rel).read_text(encoding="utf-8") for rel in TEMPLATES]
    sources.append(local_build_source())
    for src in sources:
        for m in re.finditer(r"""(?<![\w.$])(t|tv)\(\s*'([a-zA-Z0-9_.-]+)'\s*,""",
                             src):
            key = m.group(2)
            try:
                en, _ = _read_concat(src, m.end())
            except ValueError:
                continue
            if not en:
                continue
            if key in out and out[key] != en:
                dup.append(key)
            out[key] = en
    return out, dup


# The maths engine's own closed vocabularies (symbulator_ui.py). The engine
# is a published package with its own release cycle and stays English; the
# page looks these up instead, through tSrv(). `check` re-reads them from
# symbulator_ui.py so a new element kind or parameter description cannot
# arrive untranslated without saying so.
SRV_SOURCES = ("_KIND_LABEL", "_ELEMENT_KEYS", "_TOOL_LABELS",
               "_PORT_LABELS", "_QUANTITY_WORDS")


def srv_vocabulary() -> set:
    src = (SERVER / "symbulator_ui.py").read_text(encoding="utf-8")
    words = set()
    for name in SRV_SOURCES:
        i = src.index("\n" + name)
        j = src.index("\n}", i) if name != "_ELEMENT_KEYS" else src.index("\n]", i)
        block = src[i:j]
        if name == "_ELEMENT_KEYS":
            for m in re.finditer(r'"[^"]*",\s*"[^"]*",\s*"([^"]+)"', block):
                words.add(m.group(1))
        else:
            for m in re.finditer(r':\s*"([^"]+)"', block):
                words.add(m.group(1))
    # Not in any of those tables: built inline, one per port node (#168).
    words.add("current through")
    words.add("voltage drop")
    return words


STRUCT = {
    "an id": re.compile(r'id="[^"]+"'),
    "a link": re.compile(r'href="[^"]+"'),
    "a slot": re.compile(r"%\{\w+\}"),
    "a script or style tag": re.compile(r"</?(?:script|style)\b"),
}


def structure(code: str, en: dict, d: dict) -> list:
    """What a translation must carry over from its English unchanged.

    A dictionary value is written straight into the page as innerHTML, so
    a translation that drops an `id` takes the element the app looks up
    with it, and one that drops a `%{n}` slot loses the number the
    sentence was about. Both fail silently at runtime and loudly here.
    """
    out = []
    for key, text in d.items():
        src = en.get(key)
        if src is None:
            continue
        for what, rx in STRUCT.items():
            if what == "a script or style tag":
                if rx.search(text) and not rx.search(src):
                    out.append(f"i18n/{code}.json: {key!r} introduces "
                               f"{what}")
                continue
            if sorted(rx.findall(src)) != sorted(rx.findall(text)):
                out.append(f"i18n/{code}.json: {key!r} does not carry over "
                           f"{what} from the English")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command",
                    choices=["scan", "tag", "pack", "check", "seed"])
    args = ap.parse_args()

    if args.command == "scan":
        total = 0
        for rel in TEMPLATES:
            _, found = scan(SERVER / rel)
            texts = [f for f in found if f[1] == "text"]
            print(f"{rel}: {len(texts)} text units, "
                  f"{len(found) - len(texts)} attributes")
            total += len(found)
        print(f"{total} units")
        return 0

    if args.command in ("tag", "seed"):
        en, problems = tag_templates(write=True)
        for p in problems:
            print(p)
        I18N.mkdir(exist_ok=True)
        # en.json is entirely generated. The English lives in the template
        # markup and in the fallback argument of every t()/tv() call; a
        # second hand-kept copy of it would only drift from those.
        calls, dup = js_calls()
        for k in dup:
            print(f"two different English texts for {k!r} in the page")
        srv = {"srv." + w: w for w in srv_vocabulary()}
        merged = dict(sorted({**en, **calls, **srv}.items()))
        (I18N / "en.json").write_text(
            json.dumps(merged, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        print(f"tagged; i18n/en.json has {len(merged)} keys "
              f"({len(en)} markup, {len(calls)} script, {len(srv)} engine)")
        return 0

    if args.command == "pack":
        problems = pack(write=True)
        for p in problems:
            print(p)
        print("packed")
        return 1 if problems else 0

    # check
    bad = []
    en_gen, problems = tag_templates(write=False)
    bad += problems
    calls, dup = js_calls()
    for k in dup:
        bad.append(f"two different English texts for {k!r} in the page")
    # A t() call whose key is a variable is invisible to js_calls, so its
    # key never reaches en.json and never reaches a translator -- and the
    # page falls back to English in all eight languages with nothing to
    # say so. Spell every key out at the call.
    # `key` is the runtime's own parameter name, in `function t(key, en)`
    # and in tv()'s body; everything else with a variable there is a call
    # that would go unseen.
    for rel in TEMPLATES:
        src = (SERVER / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"(?<![\w.$])(t|tv)\(\s*(?!['\"])(\w+)", src):
            if m.group(2) == "key":
                continue
            bad.append(f"{rel}: {m.group(1)}({m.group(2)}, ...) -- the key "
                       f"must be a literal, or it never reaches i18n/en.json")
    expected = {**en_gen, **calls,
                **{"srv." + w: w for w in srv_vocabulary()}}
    en = load("en")
    for k, v in expected.items():
        if k not in en:
            bad.append(f"i18n/en.json is missing {k!r} -- run "
                       f"`python tools/i18n.py tag`")
        elif en[k] != v:
            bad.append(f"i18n/en.json disagrees with the page on {k!r}")
    for k in en:
        if k not in expected:
            bad.append(f"i18n/en.json has an orphan key {k!r}")
    for code in TARGETS:
        d = load(code)
        if not d:
            bad.append(f"i18n/{code}.json is missing or empty")
            continue
        missing = [k for k in en if k not in d or not d[k]]
        orphan = [k for k in d if k not in en]
        if missing:
            bad.append(f"i18n/{code}.json: {len(missing)} untranslated key(s), "
                       f"first {missing[:3]}")
        if orphan:
            bad.append(f"i18n/{code}.json: {len(orphan)} orphan key(s) whose "
                       f"English has changed, first {orphan[:3]}")
        bad += structure(code, en, d)
    bad += untranslated()
    bad += uncoded_messages()
    bad += pack(write=False)
    for b in bad:
        print(b)
    print("i18n check:", "ok" if not bad else f"{len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
