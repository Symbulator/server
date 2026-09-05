"""
Symbulator server -- a thin Flask front end over the `symbulator` package
(https://pypi.org/project/symbulator/).

Design notes:

- The actual solving runs in a separate, killable child process with a
  hard timeout. Symbolic solving can run essentially forever on
  pathological inputs, and on a public site anyone can type anything
  into the box; a timeout on the request thread alone wouldn't stop the
  underlying computation, so the child process is terminated outright.

- Input is validated *before* it ever reaches SymPy. Circuit values are
  parsed with sympy.sympify, which is an expression evaluator -- so the
  web layer restricts the character set (no brackets, braces, equals,
  quotes, backslashes...) and rejects double underscores, which closes
  off the classic sympify attack surface (attribute access / dunder
  tricks). Lengths are capped so nobody submits a megabyte of "circuit".

- WSGI throughout (Flask, with the WSGI server supplied by the host)
  -- see DEPLOY.md.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import re
import time

from flask import (Flask, jsonify, render_template, request,
                   send_from_directory)

from circuitbook import parse_book

app = Flask(__name__)

# EqSheet, the what-if numerical solver, mounted at /eqsheet/. It is its
# own Blueprint (its /api/solve must not collide with this file's), and
# the "What if..." button after a solve opens it preloaded via ?import=.
# The Blueprint lives in eqsheet_web.py; eqsheet.py itself is Flask-free
# so the offline builds can import it into Pyodide (#208).
from eqsheet_web import bp as eqsheet_bp                      # noqa: E402
app.register_blueprint(eqsheet_bp)

# #228: the banner strings a fork is allowed to differ on. Both pages
# want them -- index.html and eqsheet.html carry the same lockup -- and a
# context processor reaches the Blueprint's templates as well as this
# file's, so neither render call has to remember to pass them.
import branding                                              # noqa: E402


@app.context_processor
def _brand():
    return {"brand_tm": branding.BRAND_TM,
            "brand_sub": branding.BRAND_SUB,
            "brand_tm_color": getattr(branding, "BRAND_TM_COLOR", ""),
            "brand_beta": getattr(branding, "BRAND_BETA", "")}


# #227: who may put this page in a frame.
#
# The documentation's split view (#224) shows the tutorial beside the
# live app, which means learn.symbulator.com legitimately frames this
# host. Anyone else doing it is either clickjacking or passing this off
# as their own, and until now nothing said so: the app sent no framing
# header at all, so every origin on the internet was allowed.
#
# That silence stopped being academic on 2 Sep 2026, when PythonAnywhere
# disabled the separate `symbulatorx` account for content that "might be
# related to phishing activities" -- a fork whose pages were byte-identical
# to this one under a near-identical hostname. Nothing about *this* site
# was in that notice, but a page that anybody may frame is exactly what an
# automated scanner reads as a phishing surface, and saying who may frame
# it is both the honest answer and a real defence.
#
# frame-ancestors only. A full Content-Security-Policy would have to
# account for the inline styles and scripts this template is built from,
# the KaTeX and MathJax CDNs and Google Fonts, and getting one of those
# wrong breaks the page for everyone; this directive touches nothing but
# framing.
#
# And *only* this header. The obvious instinct is to send X-Frame-Options
# beside it for older browsers, but there is no safe value to send: it has
# no syntax for "me and one other origin", `ALLOW-FROM` was removed from
# every current browser, and `SAMEORIGIN` would forbid the split view --
# the one framing this exists to permit. A browser too old for
# frame-ancestors is a browser too old to run Symbulator's front end
# anyway.
FRAME_ANCESTORS = "'self' https://learn.symbulator.com"


@app.after_request
def _frame_policy(resp):
    resp.headers.setdefault("Content-Security-Policy",
                            f"frame-ancestors {FRAME_ANCESTORS}")
    return resp


# Uploaded circuit books are plain text; half a megabyte is far more
# than any realistic file and keeps a hostile upload from filling RAM.
MAX_UPLOAD_BYTES = 512 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "examples")
#: The generated per-language dictionaries (#204). One file per language,
#: loaded only when that language is actually used; the offline builds
#: carry the same files beside the page instead.
I18N_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "i18n", "dist")
#: A language code, and nothing else: this arrives from the URL and is
#: joined to a path. Two letters is every code the app has or plans.
_LANG_RE = re.compile(r"^[a-z]{2}$")
#: A file the reader may ask for by name. Kept deliberately tight: this
#: is a name arriving from a query string and being joined to a path.
_EXAMPLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.cir$")

# ---------------------------------------------------------------------------
# Limits and validation
# ---------------------------------------------------------------------------

SOLVE_TIMEOUT_S = float(os.environ.get("SYMBULATOR_TIMEOUT", "25"))

# Everything below the HTTP layer lives in symbulator_ui, which the
# browser build loads into Pyodide unchanged. This file is only the web
# server around it: validation is re-used from there, and the actual
# solving runs in a killable child process.
from symbulator_ui import (                                   # noqa: E402
    solve_ui, evaluate_ui, solveq_ui, mini_tool_ui, MINI_TOOLS,
    spice_ui,
    normalise_imaginary,
    plot_time_ui, bode_ui,
    _validate, _validate_extras, _expand_and, _clean_digits, _exc_text,
    _exc_msg,
    # #200: the validators return coded messages now, and _err is what
    # keeps the code beside the English on the way out of this file.
    _err,
    parse_defines, expand_defines, expand_defines_in_desc,
    define_shadow_notices,
    _ALLOWED, _ALLOWED_EQ, _ALLOWED_COND, _VARNAME,
    MAX_DESC_LEN, MAX_OMEGA_LEN, MAX_VARIABLES, MAX_EXTRA, MAX_EXTRA_LEN,
    MAX_PLOT_POINTS, VALID_DOMAINS,
    schematic_ui,
)


def _call_worker(conn, fn_name, args):
    """Child-process entry point: run one symbulator_ui function and
    send its result dict back down the pipe."""
    import symbulator_ui
    try:
        conn.send(getattr(symbulator_ui, fn_name)(*args))
    except Exception as exc:  # noqa: BLE001
        # _exc_msg, not _exc_text: a CircuitError carries a code since
        # #199, and this is the pipe every solve comes back through.
        conn.send(symbulator_ui._err(symbulator_ui._exc_msg(exc)))
    finally:
        conn.close()


def _run_in_process(fn_name, args):
    """Run one symbulator_ui function in a killable child process.

    Returns (ok, payload). On success `payload` is the result dict's
    contents; **on failure it is the failure dict itself** -- not just
    its sentence, as it was until #200. That is what lets the coded
    message reach the page: this file lists its response fields by hand,
    so anything it does not name is dropped, and a code named in six
    places is a code forgotten in the seventh. `_refusal` below names it
    once.

    Symbolic solving can run away on pathological input, and only a
    separate process can be reliably stopped."""
    parent_conn, child_conn = mp.Pipe(duplex=False)
    proc = mp.Process(target=_call_worker, args=(child_conn, fn_name, args))
    proc.start()
    child_conn.close()  # only the child writes to this end

    if parent_conn.poll(SOLVE_TIMEOUT_S):
        result = parent_conn.recv()
        proc.join(1)
    else:
        proc.terminate()
        proc.join(1)
        # app.py's own words, so no 8xx code: #200 covers what
        # symbulator_ui writes. Shaped like a failure dict all the same,
        # so _refusal has one thing to handle.
        return False, {"error": (
            f"The solver took longer than {SOLVE_TIMEOUT_S:g} seconds and "
            "was stopped. Try a simpler circuit, or fewer requested "
            "variables for TR analysis.")}
    if proc.is_alive():
        proc.kill()

    if not result.get("ok"):
        result.setdefault("error", "Unknown error.")
        return False, result
    return True, {k: v for k, v in result.items() if k != "ok"}


def _refusal(payload, **extra):
    """One refusal from the worker, forwarded whole.

    `payload` is symbulator_ui's own failure dict, so the coded message
    (#200) rides along without every route naming the field. `error`
    stays the English, which is what an older page reads.
    """
    out = {"ok": False, "error": payload.get("error")}
    if payload.get("err"):
        out["err"] = payload["err"]
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/i18n/<lang>.js")
def i18n_dict(lang):
    """One language's dictionary, as a file.

    Root-absolute on purpose: the app is served at / and the Numerical
    Solver at /eqsheet/, so a relative path would resolve differently on
    the two pages. The offline builds rewrite it to a relative path,
    where there is only one page and it sits at the root.

    Cached hard because the URL carries a ?v= stamp that changes with
    the dictionaries -- see tools/i18n.py, stamp().
    """
    if not _LANG_RE.match(lang or ""):
        return jsonify(error="unknown language"), 404
    path = os.path.join(I18N_DIR, lang + ".js")
    if not os.path.isfile(path):
        return jsonify(error="unknown language"), 404
    resp = send_from_directory(I18N_DIR, lang + ".js",
                               mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


# --- #207: the dictionaries as files a translator can take away ------
#
# Server-only, deliberately. A translator works from the website: they
# need to send a corrected file back, which needs a network anyway, so
# there is nothing for the offline builds to carry. Shipping 817 KB of
# source JSON in a 30 MB download for a feature its users cannot
# complete would be paying for it twice over, and it would put install
# and local out of step with each other, which this project does not do.
#
# The app links here by absolute URL, the same way it links the Tutorial
# and the Acknowledgements: an outward link that needs the internet, kept
# rather than hidden in the offline build.
I18N_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")


@app.get("/i18n/<lang>.json")
def i18n_source(lang):
    """One language's dictionary, in the form a translator edits.

    Not the same file as /i18n/<lang>.js, which is the packed form the
    page loads: that one is escaped, stamped and machine-shaped. This is
    the JSON under it -- one line per phrase, and the only thing anybody
    should hand-edit.

    en.json is served too, and is generated: half its keys are markup the
    page could hand back at runtime, but the js.* half lives as literal
    fallbacks inside t() calls and cannot be harvested in a browser at
    all. A translator needs both halves or the template looks broken.
    """
    if not _LANG_RE.match(lang or ""):
        return jsonify(error="unknown language"), 404
    if not os.path.isfile(os.path.join(I18N_SRC_DIR, lang + ".json")):
        return jsonify(error="unknown language"), 404
    # No immutable caching here, unlike the packed .js: that URL carries a
    # ?v= stamp and this one does not, and a translator coming back for a
    # fresh copy after a release must not be served yesterday's.
    resp = send_from_directory(I18N_SRC_DIR, lang + ".json",
                               mimetype="application/json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/translate")
def translate():
    """The page that explains what to do with those files.

    English-only on purpose: it is addressed to somebody about to
    translate, who reads English by definition, and a version of it in
    their own language would be written by the very machine whose work
    they came to check."""
    return render_template("translate.html")


@app.get("/")
def index():
    """Serve the single-page app shell -- everything else (solving,
    examples, upload/export) happens over the /api/* routes below via
    JavaScript, so this route just returns the static page once."""
    return render_template("index.html")


# The build stamp shown in the page footer, read out of the template the
# same way a reader would read it off the page.
_BUILD_RE = re.compile(
    r"Release (\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC)")
_TEMPLATE = os.path.join(app.root_path, "templates", "index.html")


def _build_stamp():
    """The stamp currently written in the template file, or None."""
    try:
        with open(_TEMPLATE, encoding="utf-8") as fh:
            found = _BUILD_RE.search(fh.read())
    except OSError:
        return None
    return found.group(1) if found else None


# Captured once, at import: this is the build the running process started
# with. Compared against the file on disk it answers the question that
# cost an hour on 22 Aug 2026 -- the files had been pulled, git status was
# clean, the page showed the new stamp, and the API was still answering
# from the previous app.py because the web app had never been reloaded.
# A pull moves the disk; only a reload moves the process.
_LOADED_BUILD = _build_stamp()

try:
    from symbulator import __version__ as _SOLVER_VERSION
except Exception:                                  # pragma: no cover
    _SOLVER_VERSION = None


@app.get("/healthz")
def healthz():
    """Liveness check for the hosting platform to poll, and the fastest
    way to tell what is actually deployed: which build this process is
    serving, which build is on disk, and which solver is loaded. When the
    two builds differ the process is running stale code and wants a
    reload -- a `git pull` alone does not do it."""
    on_disk = _build_stamp()
    return {
        "ok": True,
        "build": _LOADED_BUILD,
        "build_on_disk": on_disk,
        "needs_reload": bool(_LOADED_BUILD and on_disk
                             and _LOADED_BUILD != on_disk),
        "solver": _SOLVER_VERSION,
    }


def _decode(data: bytes) -> str:
    """Best-effort text decode of an uploaded file, tolerating a BOM and
    Windows-1252 text saved by Notepad."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@app.get("/api/examples")
def api_examples():
    """The built-in examples: a folder of input files, each with its own
    title.

    With no arguments this lists the set -- the folder is read on every
    call, so a file can be added or edited without restarting. With
    `?file=` it returns that one file's entries.

    The offline build cannot do the listing half: it has no server, and a
    fetch cannot enumerate a directory. It reads `examples/examples.json`
    instead, which build_local.py writes from this same folder and
    `build_local.py --check` keeps honest. Both ends therefore answer the
    same shape, and the front end does not know which it is talking to."""
    wanted = (request.args.get("file") or "").strip()

    if wanted:
        if not _EXAMPLE_NAME_RE.match(wanted):
            return jsonify({"ok": False, "error": "No such example."}), 400
        path = os.path.join(EXAMPLES_DIR, wanted)
        # realpath before comparing: the pattern above already refuses a
        # separator, and this refuses anything that reaches outside the
        # folder by some route the pattern did not anticipate.
        if os.path.dirname(os.path.realpath(path)) != os.path.realpath(EXAMPLES_DIR):
            return jsonify({"ok": False, "error": "No such example."}), 400
        try:
            with open(path, "rb") as fh:
                text = _decode(fh.read())
        except OSError:
            return jsonify({"ok": False, "error": "No such example."}), 404
        circuits, warnings, title = parse_book(text)
        return jsonify({"ok": True, "name": wanted, "title": title,
                        "circuits": circuits, "warnings": warnings})

    return jsonify({"ok": True, "files": _example_files()})


def _example_files() -> list:
    """[{name, title}] for the examples folder, in filename order.

    Filename order is the reader-visible order, which is why the files are
    named Lesson_01 and not Lesson1: it is what puts lesson 10 after
    lesson 9 rather than after lesson 1."""
    out = []
    try:
        names = sorted(os.listdir(EXAMPLES_DIR))
    except OSError:
        return out
    for name in names:
        if not _EXAMPLE_NAME_RE.match(name):
            continue
        try:
            with open(os.path.join(EXAMPLES_DIR, name), "rb") as fh:
                text = _decode(fh.read())
        except OSError:
            continue
        _circuits, _warnings, title = parse_book(text)
        out.append({"name": name, "title": title or name})
    return out


@app.post("/api/upload")
def api_upload():
    """Parse an uploaded circuit-book file. Nothing is stored on the
    server -- the circuits go straight back to the browser, which holds
    them for the session only."""
    fh = request.files.get("file")
    if fh is None:
        return jsonify({"ok": False, "error": "No file was uploaded."}), 400
    data = fh.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False,
                        "error": f"File is too large (limit "
                                 f"{MAX_UPLOAD_BYTES // 1024} KB)."}), 400
    if not data.strip():
        return jsonify({"ok": False, "error": "That file is empty."}), 400

    circuits, warnings, title = parse_book(_decode(data))
    if not circuits:
        return jsonify({"ok": False,
                        "error": "No circuits found in that file. Each circuit "
                                 "needs a [Name] heading above its element lines.",
                        "warnings": warnings}), 400
    name = os.path.basename(fh.filename or "uploaded file")[:80]
    return jsonify({"ok": True, "filename": name, "title": title,
                    "circuits": circuits, "warnings": warnings})


@app.post("/api/export")
def api_export():
    """Render the browser's live file of circuits as circuit-book text,
    so the user can download it and open it again later (or keep
    building it up). Nothing is stored on the server -- the list of
    circuits comes in whole with every call."""
    from circuitbook import format_book, clean_circuits, MAX_TITLE_LEN

    data = request.get_json(silent=True) or {}
    raw_circuits = data.get("circuits")
    if not isinstance(raw_circuits, list) or not raw_circuits:
        return jsonify({"ok": False, "error": "Nothing to save yet."}), 400

    # #250: the field list lives in circuitbook, next to the parser's own
    # tables, and the offline bridge calls the same function. This used to
    # be a hand-kept copy here and another in bridge.py, and the two had
    # drifted apart in different directions.
    circuits = clean_circuits(raw_circuits, desc_len=MAX_DESC_LEN,
                              extra_len=MAX_EXTRA_LEN, max_items=MAX_EXTRA)
    if not circuits:
        return jsonify({"ok": False, "error": "Nothing to save yet."}), 400
    title = str(data.get("title") or "")[:MAX_TITLE_LEN]
    return jsonify({"ok": True, "text": format_book(circuits, title)})



_MAX_SOLVE_EQS = 10


@app.post("/api/solveq")
def api_solveq():
    """Solve user-supplied equations against the circuit's answers."""
    data = request.get_json(silent=True) or {}
    raw_eqs = data.get("equations") or ""
    if isinstance(raw_eqs, list):
        equations = [str(x).strip() for x in raw_eqs if str(x).strip()]
    else:
        equations = [ln.strip() for ln in re.split(r"[\r\n]+", str(raw_eqs))
                     if ln.strip()]
    # One per line, or joined with ` and ` -- same as Expert Mode's boxes.
    equations = _expand_and(equations)
    unknowns = [u.strip() for u in
                re.split(r"[,\s]+", str(data.get("unknowns") or "")) if u.strip()]
    values = data.get("values") or {}

    raw_conds = data.get("conditions") or ""
    if isinstance(raw_conds, list):
        conditions = [str(x).strip() for x in raw_conds if str(x).strip()]
    else:
        conditions = [ln.strip() for ln in re.split(r"[\r\n]+", str(raw_conds))
                      if ln.strip()]
    conditions = _expand_and(conditions)

    defines, define_err = parse_defines(data.get("defines") or "")
    if define_err:
        return jsonify(_err(define_err)), 400
    if defines:
        equations = [expand_defines(e, defines) for e in equations]
        conditions = [expand_defines(c, defines) for c in conditions]
        unknowns = [expand_defines(u, defines) for u in unknowns]

    if not equations:
        return jsonify({"ok": False,
                        "error": "Enter at least one equation to solve."}), 400
    if len(equations) > _MAX_SOLVE_EQS:
        return jsonify({"ok": False,
                        "error": f"Too many equations (max {_MAX_SOLVE_EQS})."}), 400
    for eq in equations:
        if len(eq) > MAX_EXTRA_LEN or not _ALLOWED_EQ.match(eq) or "__" in eq:
            return jsonify({"ok": False,
                            "error": f"Equation contains invalid characters: {eq!r}"}), 400
    if len(conditions) > _MAX_SOLVE_EQS:
        return jsonify({"ok": False,
                        "error": f"Too many conditions (max {_MAX_SOLVE_EQS})."}), 400
    for c in conditions:
        if len(c) > MAX_EXTRA_LEN or not _ALLOWED_COND.match(c) or "__" in c:
            return jsonify({"ok": False,
                            "error": f"Condition contains invalid characters: {c!r}"}), 400
    if len(unknowns) > MAX_EXTRA:
        return jsonify({"ok": False, "error": "Too many unknowns."}), 400
    for u in unknowns:
        if not _VARNAME.match(u):
            return jsonify({"ok": False, "error": f"Invalid unknown name: {u!r}"}), 400
    if not isinstance(values, dict) or len(values) > 300:
        return jsonify({"ok": False, "error": "Invalid values payload."}), 400

    clean = {}
    for k, v in values.items():
        if (isinstance(k, str) and _VARNAME.match(k) and isinstance(v, str)
                and len(v) <= 4000 and _ALLOWED.match(v) and "__" not in v):
            clean[k] = v

    digits = _clean_digits(data.get("digits"))
    si = bool(data.get("si"))
    approx = bool(data.get("approx"))
    units = bool(data.get("units"))
    real_only = bool(data.get("real_only"))

    t0 = time.time()
    domain = str(data.get("domain", "")).strip().lower()
    ok, payload = _run_in_process(
        "solveq_ui",
        (equations, unknowns, clean, digits, si, approx, units, real_only,
         conditions, domain, bool(data.get('dual'))))
    elapsed = round(time.time() - t0, 2)
    if not ok:
        return jsonify(_refusal(payload, elapsed=elapsed)), 422
    return jsonify({"ok": True, "elapsed": elapsed, **payload})


@app.errorhandler(413)
def too_large(_exc):
    """Flask calls this automatically when a request body exceeds
    MAX_CONTENT_LENGTH, before the route handler even runs -- catches an
    oversized upload that slips past api_upload's own length check
    because Flask rejects it at the WSGI layer first."""
    return jsonify({"ok": False,
                    "error": f"File is too large (limit "
                             f"{MAX_UPLOAD_BYTES // 1024} KB)."}), 413


_VALID_TOOLS = {"solve", "th", "er", "port"}
_NODE_RE = re.compile(r"^[A-Za-z0-9_]{1,20}$")


@app.post("/api/solve")
def api_solve():
    """Main solve endpoint: validate the posted circuit and options,
    resolve any ambiguous bare-suffix values (asking the browser back for
    a decision if needed -- see the "Ambiguity check" block below), then
    hand off to solve_ui/th/er/port (via symbulator_ui, in the killable
    child process) and return its formatted answers. Handles both a
    normal circuit solve and the th/er/port two-terminal tools, selected
    by `tool` in the posted JSON."""
    data = request.get_json(silent=True) or {}
    desc = str(data.get("desc", "")).strip()
    # Elements may be separated by ":" (calculator style) or by new
    # lines (natural in the textarea) -- normalize before validating.
    desc = re.sub(r"[\r\n]+", ":", desc)
    desc = re.sub(r":{2,}", ":", desc).strip(":")
    domain = str(data.get("domain", "")).strip().lower()
    omega = str(data.get("omega", "")).strip()
    variables = data.get("variables") or None
    if isinstance(variables, str):
        variables = [v.strip() for v in variables.split(",") if v.strip()]
    tool = str(data.get("tool", "solve")).strip().lower() or "solve"
    n1 = str(data.get("n1", "")).strip()
    n2 = str(data.get("n2", "")).strip()
    kind = str(data.get("kind", "z")).strip().lower()
    digits = _clean_digits(data.get("digits"))
    si = bool(data.get("si"))
    units = bool(data.get("units"))
    use_rms = bool(data.get("use_rms"))
    polar = bool(data.get("polar"))
    approx = bool(data.get("approx"))
    # #175: "exact and approximate to n digits" -- one mode, not two
    # settings, so it rides beside `digits` rather than replacing it.
    dual = bool(data.get("dual"))

    def _lines(field):
        """Read `field` from the posted JSON as a list of non-blank
        strings, whether the browser sent it as an actual JSON array or
        as one newline/carriage-return-separated block of text (which is
        what a plain <textarea> gives you) -- so the expert-mode
        equations/conditions boxes work the same either way."""
        raw = data.get(field) or ""
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [ln.strip() for ln in re.split(r"[\r\n]+", str(raw)) if ln.strip()]

    # Equations and conditions alike: one per line, or several on one
    # line joined with ` and ` -- "re = 12'k and ir3 = 6'm" is two
    # equations, the calculator's own idiom. Equations joined the
    # conditions on 28 Aug 2026, when the Expert Mode hint started
    # saying so and Roberto asked whether it was actually true.
    extra_equations = _expand_and(_lines("equations"))
    extra_conditions = _expand_and(_lines("conditions"))
    extra_unknowns = [u.strip() for u in
                      re.split(r"[,\s]+", str(data.get("unknowns") or ""))
                      if u.strip()]

    # The Define field, expanded before anything else looks at the text --
    # including the ambiguity check below, so a definition that introduces
    # a bare "1k" is questioned exactly as if it had been typed inline.
    defines, define_err = parse_defines(_lines("defines"))
    if define_err:
        return jsonify(_err(define_err)), 400
    define_notices = []
    if defines:
        define_notices = define_shadow_notices(defines, desc)
        desc = expand_defines_in_desc(desc, defines)
        extra_equations = [expand_defines(e, defines) for e in extra_equations]
        extra_conditions = [expand_defines(c, defines) for c in extra_conditions]
        extra_unknowns = [expand_defines(u, defines) for u in extra_unknowns]

    err = _validate(desc, domain, omega, variables)
    if not err:
        err = _validate_extras(extra_equations, extra_unknowns, extra_conditions)
    if not err and tool not in _VALID_TOOLS:
        err = "Unknown tool."
    if not err and tool != "solve":
        if domain not in ("dc", "ac", "fd"):
            err = ("Thevenin / impedance / two-port tools work in DC, AC "
                   "or FD -- not in the time domain.")
        elif not (_NODE_RE.match(n1) and _NODE_RE.match(n2)):
            err = "Give the two port nodes (n1 and n2) for this tool."
        elif tool == "port" and kind not in ("z", "y", "h", "g", "a", "b"):
            err = "Two-port kind must be one of z, y, h, g, a, b."
    if err:
        return jsonify(_err(err)), 400

    # ---- Ambiguity check: a bare value like "1k" could be the SI unit
    # (1'k = 1000) or number*variable (1*k). If any are present and the
    # user hasn't said which, send the question back instead of solving;
    # once choices arrive, rewrite the description to the explicit form.
    choices = data.get("suffix_choices") or {}
    if not isinstance(choices, dict):
        choices = {}
    choices = {str(k): str(v) for k, v in choices.items()
               if isinstance(k, str) and len(k) <= 30 and v in ("si", "var")}

    desc_used = None
    imaginary_notes = []
    normalised, imaginary_notes = normalise_imaginary(desc, domain)
    if normalised != desc:
        desc = normalised
    try:
        from symbulator.elements import (parse_circuit, ambiguous_in_elements,
                                         _VALUE_FIELD_IDX)
        from symbulator.si_prefix import bare_suffix_match, _BARE_SUFFIX_EXP
        # expand_si=False: keep SI-prefix shorthand (4.7'M) as typed in
        # these elements' fields, since they're what desc_used gets
        # rebuilt from below -- it gets expanded to a real number the
        # normal way when solve_ui parses `desc` again for the actual
        # solve.
        elements = parse_circuit(desc, expand_si=False)
        ambiguous = ambiguous_in_elements(elements)
    except Exception as exc:  # parse errors get the same friendly text
        # _exc_msg, not str(exc): this is the parse step, run in the
        # parent process rather than the worker, and it was the one place
        # a CircuitError's code (#199) was still being flattened away.
        return jsonify(_err(_exc_msg(exc))), 422

    if ambiguous:
        unresolved = [a for a in ambiguous if a["token"] not in choices]
        if unresolved:
            groups = {}
            for a in ambiguous:
                g = groups.setdefault(a["token"], {
                    "token": a["token"], "number": a["number"],
                    "letter": a["letter"],
                    "exponent": _BARE_SUFFIX_EXP[a["letter"]],
                    "elements": []})
                g["elements"].append(a["element"])
            return jsonify({"ok": False, "ambiguous": list(groups.values())})
        # Every token has an answer: rewrite each ambiguous value field
        # to the explicit spelling the user chose.
        for e in elements:
            for idx in _VALUE_FIELD_IDX.get(e.kind, ()):
                if idx >= len(e.fields):
                    continue
                m = bare_suffix_match(e.fields[idx])
                if m:
                    tok = e.fields[idx].strip()
                    sep = "'" if choices[tok] == "si" else "*"
                    e.fields[idx] = f"{m[0]}{sep}{m[1]}"
                    # Keep the typed copy in step: `desc` is rebuilt from
                    # raw_fields below, so the resolved spelling has to
                    # land there too or the choice would be lost.
                    _raw = getattr(e, "raw_fields", None)
                    if _raw and idx < len(_raw):
                        _raw[idx] = e.fields[idx]

    # Always echo the circuit back one element per line, regardless of
    # whether anything above needed fixing up -- easier to read/edit
    # than a single ':'-joined line, and consistent every time you run,
    # not just on the two occasions (imaginary-unit normalizing, an
    # ambiguous suffix being resolved) that used to trigger it. Because
    # `elements` was parsed with expand_si=False, any SI-prefix shorthand
    # (4.7'M) is still sitting in e.fields as typed -- this reconstructs
    # `desc_used` (what the user sees) with that notation intact; `desc`
    # (what actually gets solved, below) gets it expanded to a real
    # number the normal way when solve_ui parses it again.
    #
    # Each element re-emits from raw_fields -- the fields as typed --
    # not from fields, where the `[...]` shortcut has already been
    # rewritten to pr(...). Re-emitting the rewrite was #116: solve_ui's
    # own parse then recorded pr(...) as "what the reader typed", so an
    # error about the value quoted `rxpr(1'k)` for a reader who wrote
    # `rx[1'k]`. raw_fields is empty when nothing was rewritten (fields
    # is identical) and when the shortcut's inner commas made the typed
    # text split differently (unrecoverable -- see parse_circuit), so
    # falling back to fields loses nothing. An unbalanced bracket cannot
    # reach here: it raised in the parse above. getattr because the
    # server takes symbulator from PyPI and may briefly be a release
    # behind this file.
    desc = ":".join(
        e.name + "," + ",".join(getattr(e, "raw_fields", None) or e.fields)
        for e in elements)
    desc_used = desc.replace(":", "\n")

    t0 = time.time()
    ok, payload = _run_in_process(
        "solve_ui", (desc, domain, omega, variables, tool, n1, n2, kind,
                        extra_equations, extra_unknowns, extra_conditions,
                        digits, si, units, use_rms, approx, polar, dual))
    elapsed = round(time.time() - t0, 2)

    if not ok:
        # The notes matter most when the solve failed: "normalised '5*i'
        # to '5j'" is often the explanation for the error underneath it.
        return jsonify(_refusal(payload, elapsed=elapsed,
                                notes=define_notices + imaginary_notes)), 422

    payload.setdefault("notes", [])
    payload["notes"] = define_notices + imaginary_notes + list(payload["notes"])
    # solve_ui may have switched "exact" to "approximate" itself (an
    # approximate value was found in the inputs) -- echo back what it
    # actually used, not what was requested, so the UI can reflect it.
    return jsonify({"ok": True, "domain": domain, "tool": tool,
                    "elapsed": elapsed, "desc_used": desc_used,
                    "digits": digits, "si": si, "units": units,
                    "use_rms": use_rms, "polar": polar, "dual": dual,
                    "approx": payload.get("approx", approx),
                    "approx_forced": payload.get("approx_forced", False),
                    "nodes": payload["nodes"],
                    "elements": payload["elements"], "extras": payload["extras"],
                    # #292: the th tool's answers for a load on the port,
                    # shown only when the reader has said there is one.
                    "load_extras": payload.get("load_extras") or [],
                    "values": payload["values"],
                    # Every root of the system, each formatted the same way
                    # as the fields above -- which mirror the first of them.
                    # The page offers a picker when there is more than one.
                    "solutions": payload.get("solutions") or [],
                    "equations": payload["equations"],
                    # The EqSheet import payload (present for dc, and for
                    # ac with a numeric omega). The fields here are listed
                    # by hand, so a key added in symbulator_ui must be
                    # named or the server variant silently drops it.
                    "eqsheet": payload.get("eqsheet"),
                    # #176: the same system, grouped and with LaTeX per
                    # line, for the Equations card. Named here for the
                    # reason the comment above gives.
                    "system": payload.get("system"),
                    "notes": payload["notes"]})


_VALID_PLOT_TOOLS = {"time", "bode", "bode_tf", "sweep"}
_MAX_RANGE = 1e15  # generous ceiling; keeps a typo from hanging np.logspace/linspace


def _clean_range(raw, lo_default, hi_default):
    """Parse a plot range's min/max into floats, falling back to the
    given defaults for blank input. Returns (lo, hi, error)."""
    try:
        lo = float(raw.get("min")) if raw.get("min") not in (None, "") else lo_default
        hi = float(raw.get("max")) if raw.get("max") not in (None, "") else hi_default
    except (TypeError, ValueError):
        return None, None, "Range values must be numbers."
    if not (-_MAX_RANGE < lo < _MAX_RANGE and -_MAX_RANGE < hi < _MAX_RANGE):
        return None, None, "Range values are out of bounds."
    return lo, hi, None


@app.post("/api/schematic")
def api_schematic():
    """Draw the circuit, without solving it. Separate from /api/solve on
    purpose: the picture earns its keep on a circuit that does not solve,
    and the drawer parses SI shorthand without the ambiguity negotiation
    the solver needs, so `1k` draws where a solve would stop and ask."""
    data = request.get_json(silent=True) or {}
    desc = str(data.get("desc") or "")
    if len(desc) > MAX_DESC_LEN:
        return jsonify({"ok": False, "error": "That circuit description is too long."}), 400
    t0 = time.time()
    ok, payload = _run_in_process("schematic_ui", (desc,))
    elapsed = round(time.time() - t0, 2)
    if not ok:
        return jsonify(_refusal(payload, elapsed=elapsed)), 422
    # _run_in_process has already unwrapped the ui dict's own "ok": on
    # failure it hands back the failure dict, which _refusal forwards
    # above, so there is nothing left to check here.
    #
    # Enumerated by hand like the other routes -- a key added in
    # symbulator_ui reaches the offline build automatically but is
    # silently dropped here until it is named.
    return jsonify({"ok": True, "svg": payload["svg"], "elapsed": elapsed})


@app.post("/api/plot")
def api_plot():
    """Plot endpoint for the sampling-based tools: "Plot vs time" (tr()'s
    response over a time range), "Bode plot" (fd()'s magnitude/phase over
    a frequency sweep), "Bode plot of a transfer function" (a typed H(s),
    no circuit at all) and "Plot against a variable" (a DC answer sampled
    against one symbolic value). Separate from /api/solve
    because the shape of both the request (a range + point count instead
    of a domain) and the response (number arrays for a chart instead of
    formatted equations) are different enough that folding them into the
    same endpoint would complicate both."""
    data = request.get_json(silent=True) or {}
    desc = str(data.get("desc", "")).strip()
    desc = re.sub(r"[\r\n]+", ":", desc)
    desc = re.sub(r":{2,}", ":", desc).strip(":")
    tool = str(data.get("tool", "")).strip().lower()
    key = str(data.get("key", "")).strip()
    try:
        n = int(data.get("n", 200))
    except (TypeError, ValueError):
        n = -1

    def _lines(field):
        raw = data.get(field) or ""
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [ln.strip() for ln in re.split(r"[\r\n]+", str(raw)) if ln.strip()]

    # Equations and conditions alike: one per line, or several on one
    # line joined with ` and ` -- "re = 12'k and ir3 = 6'm" is two
    # equations, the calculator's own idiom. Equations joined the
    # conditions on 28 Aug 2026, when the Expert Mode hint started
    # saying so and Roberto asked whether it was actually true.
    extra_equations = _expand_and(_lines("equations"))
    extra_conditions = _expand_and(_lines("conditions"))
    extra_unknowns = [u.strip() for u in
                      re.split(r"[,\s]+", str(data.get("unknowns") or ""))
                      if u.strip()]

    defines, define_err = parse_defines(_lines("defines"))
    if define_err:
        return jsonify(_err(define_err)), 400
    if defines:
        desc = expand_defines_in_desc(desc, defines)
        extra_equations = [expand_defines(e, defines) for e in extra_equations]
        extra_conditions = [expand_defines(c, defines) for c in extra_conditions]
        extra_unknowns = [expand_defines(u, defines) for u in extra_unknowns]

    xname = str(data.get("xname", "")).strip()
    err = None
    if tool not in _VALID_PLOT_TOOLS:
        err = "Unknown plot tool."
    elif tool == "bode_tf":
        # No circuit involved: `key` carries the transfer function itself,
        # validated the way Evaluate validates an expression.
        if not key:
            err = "Give a transfer function of s, e.g. 100/(s^2 + 10*s + 100)."
        elif len(key) > _EXPR_MAX or not _ALLOWED.match(key) or "__" in key:
            err = "Transfer function contains invalid characters."
    elif not desc:
        err = "Please enter a circuit description."
    elif len(desc) > MAX_DESC_LEN:
        err = f"Circuit description too long (max {MAX_DESC_LEN} characters)."
    elif not _ALLOWED.match(desc) or "__" in desc:
        err = "Circuit description contains characters that aren't used in Symbulator syntax."
    elif not key or not _VARNAME.match(key):
        err = "Give a variable to plot, e.g. v_2 or i_r1."
    elif tool == "sweep" and (not xname or not _VARNAME.match(xname)):
        err = "Give a variable to sweep along the x-axis, e.g. rx."
    if not err and not (2 <= n <= MAX_PLOT_POINTS):
        err = f"Number of points must be between 2 and {MAX_PLOT_POINTS}."
    if not err and tool != "bode_tf":
        err = _validate_extras(extra_equations, extra_unknowns, extra_conditions)
    if err:
        return jsonify(_err(err)), 400

    if tool == "time":
        t_min, t_max, rng_err = _clean_range(data, 0.0, 1.0)
        if rng_err:
            return jsonify({"ok": False, "error": rng_err}), 400
        fn_name, args = "plot_time_ui", (desc, key, t_min, t_max, n,
                                         extra_equations, extra_unknowns, extra_conditions)
    elif tool == "sweep":
        x_min, x_max, rng_err = _clean_range(data, 0.0, 1.0)
        if rng_err:
            return jsonify({"ok": False, "error": rng_err}), 400
        fn_name, args = "sweep_ui", (desc, key, xname, x_min, x_max, n,
                                     extra_equations, extra_unknowns, extra_conditions)
    else:
        f_min, f_max, rng_err = _clean_range(data, 1.0, 1000.0)
        if rng_err:
            return jsonify({"ok": False, "error": rng_err}), 400
        if f_min <= 0 or f_max <= 0:
            return jsonify({"ok": False, "error": "Bode frequencies must be positive (Hz)."}), 400
        if tool == "bode_tf":
            fn_name, args = "bode_tf_ui", (key, f_min, f_max, n)
        else:
            fn_name, args = "bode_ui", (desc, key, f_min, f_max, n,
                                        extra_equations, extra_unknowns, extra_conditions)

    t0 = time.time()
    ok, payload = _run_in_process(fn_name, args)
    elapsed = round(time.time() - t0, 2)
    if not ok:
        return jsonify(_refusal(payload, elapsed=elapsed)), 422
    return jsonify({"ok": True, "tool": tool, "elapsed": elapsed, **payload})


_EXPR_MAX = 500


@app.post("/api/evaluate")
def api_evaluate():
    """Evaluate a standalone expression (the "Evaluate" card): substitute
    the posted name->value pairs into `expr` and format the result the
    same way a solved circuit answer would be -- lets the user plug
    solved values into a follow-up formula (e.g. power = v * i) without
    re-solving the whole circuit."""
    data = request.get_json(silent=True) or {}
    expr = str(data.get("expr", "")).strip()
    values = data.get("values") or {}
    defines, define_err = parse_defines(data.get("defines") or "")
    if define_err:
        return jsonify(_err(define_err)), 400

    if not expr:
        return jsonify({"ok": False, "error": "Enter an expression to evaluate."}), 400
    if defines:
        expr = expand_defines(expr, defines)
    if len(expr) > _EXPR_MAX or not _ALLOWED.match(expr) or "__" in expr:
        return jsonify({"ok": False, "error": "Expression contains invalid characters."}), 400
    if not isinstance(values, dict) or len(values) > 300:
        return jsonify({"ok": False, "error": "Invalid values payload."}), 400
    clean = {}
    for k, v in values.items():
        if (isinstance(k, str) and _VARNAME.match(k) and isinstance(v, str)
                and len(v) <= 4000 and _ALLOWED.match(v) and "__" not in v):
            clean[k] = v

    digits = _clean_digits(data.get("digits"))
    si = bool(data.get("si"))
    approx = bool(data.get("approx"))
    dual = bool(data.get("dual"))                                   # #175
    # The Conditions box (#96) -- read and guarded exactly as the Solve
    # card's is, including `and` between clauses, so the two boxes accept
    # the same things. _ALLOWED is the wrong guard here: it has no `=`,
    # `<` or `>` in it, because it is for a single expression.
    raw_conds = data.get("conditions") or ""
    if isinstance(raw_conds, list):
        conditions = [str(x).strip() for x in raw_conds if str(x).strip()]
    else:
        conditions = [ln.strip() for ln in str(raw_conds).splitlines()
                      if ln.strip()]
    conditions = _expand_and(conditions)
    if defines:
        conditions = [expand_defines(c, defines) for c in conditions]
    if len(conditions) > _MAX_SOLVE_EQS:
        return jsonify({"ok": False,
                        "error": f"Too many conditions "
                                 f"(max {_MAX_SOLVE_EQS})."}), 400
    for cond in conditions:
        if len(cond) > MAX_EXTRA_LEN or not _ALLOWED_COND.match(cond) or "__" in cond:
            return jsonify({"ok": False,
                            "error": f"Condition contains invalid "
                                     f"characters: {cond!r}"}), 400
    t0 = time.time()
    domain = str(data.get("domain", "")).strip().lower()
    ok, payload = _run_in_process("evaluate_ui", (expr, clean, digits, si,
                                                  approx, domain, conditions,
                                                  dual))
    elapsed = round(time.time() - t0, 2)
    if not ok:
        return jsonify(_refusal(payload, elapsed=elapsed)), 422
    return jsonify({"ok": True, "elapsed": elapsed, **payload})


@app.post("/api/minitool")
def api_minitool():
    """Run one of the small version 7 helpers (the "Mini-tools" card).

    These differ from Evaluate in what they hand back: `aa` answers with a
    magnitude and an angle, `pf` with a number and a direction, `gain` with
    four figures at once. None of that is an expression, so none of it can
    go through the Evaluate path -- but the arguments are still resolved
    against the posted answers, which is what lets a user write `i_r1`
    rather than copying a phasor out of the results by hand."""
    data = request.get_json(silent=True) or {}
    tool = str(data.get("tool", "")).strip()
    args = data.get("args") or []
    values = data.get("values") or {}

    if tool not in MINI_TOOLS:
        return jsonify({"ok": False, "error": "Unknown tool."}), 400
    if not isinstance(args, list) or len(args) > 8:
        return jsonify({"ok": False, "error": "Invalid arguments."}), 400
    clean_args = []
    for a in args:
        a = str(a or "").strip()
        if len(a) > _EXPR_MAX or (a and (not _ALLOWED.match(a) or "__" in a)):
            return jsonify({"ok": False,
                            "error": "Values contain invalid characters."}), 400
        clean_args.append(a)
    if not isinstance(values, dict) or len(values) > 300:
        return jsonify({"ok": False, "error": "Invalid values payload."}), 400
    clean = {}
    for k, v in values.items():
        if (isinstance(k, str) and _VARNAME.match(k) and isinstance(v, str)
                and len(v) <= 4000 and _ALLOWED.match(v) and "__" not in v):
            clean[k] = v

    digits = _clean_digits(data.get("digits")) or 4
    t0 = time.time()
    ok, payload = _run_in_process("mini_tool_ui",
                                  (tool, clean_args, clean, digits))
    elapsed = round(time.time() - t0, 2)
    if not ok:
        return jsonify(_refusal(payload, elapsed=elapsed)), 422
    return jsonify({"ok": True, "elapsed": elapsed, **payload})


@app.post("/api/spice")
def api_spice():
    """Translate between Symbulator notation and a SPICE netlist (the
    SPICE Translator card, #160). Pure parsing, no solve, so it runs
    in-process; the solver's own guarded sympify vets every value."""
    data = request.get_json(silent=True) or {}
    direction = str(data.get("direction", "")).strip()
    text = data.get("text", "")

    if direction not in ("to_spice", "from_spice"):
        return jsonify({"ok": False, "error": "Unknown direction."}), 400
    if not isinstance(text, str) or len(text) > 20000:
        return jsonify({"ok": False,
                        "error": "The circuit text is too long."}), 400

    payload = spice_ui(direction, text)
    if not payload.get("ok"):
        return jsonify(payload), 422
    return jsonify({"ok": True, "output": payload["output"],
                    "warnings": payload.get("warnings") or []})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
