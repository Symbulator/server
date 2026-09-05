"""
symbulator_ui -- everything the Symbulator front ends need, with no web
framework attached.

This module is shared verbatim by two front ends:

* the Flask app (`app.py`), which runs it in a killable subprocess and
  serves the results over HTTP, and
* the browser build, which loads this same file into Pyodide and calls
  it directly, with no server involved at all.

Keeping one copy is deliberate: the formatting rules, unit handling,
element ordering and variable aliasing are fiddly enough that two
implementations would drift apart within a week.

Every entry point returns a plain dict -- {"ok": True, ...} or
{"ok": False, "error": "..."} -- so it can cross a process pipe, an
HTTP response or a JavaScript boundary unchanged.
"""

from __future__ import annotations

import re


def _ok(payload):
    """Wrap a successful result as the {"ok": True, ...} dict every entry
    point returns (see the module docstring). A dict payload is merged in
    directly (its keys become top-level keys of the result); anything
    else is wrapped under a single "result" key."""
    if isinstance(payload, dict):
        return {"ok": True, **payload}
    return {"ok": True, "result": payload}


def _err(message):
    """Wrap a failure as the {"ok": False, "error": ...} dict every entry
    point returns on failure.

    Since #200 `message` may be a coded message from msg() as well as a
    bare string. A string is what this file *forwards* rather than
    writes -- a sentence out of the solver package, which has no codes
    until #199 -- and it keeps working untouched.

    `error` is always the English either way. That is what makes a
    partial rollout invisible: an uncoded message renders its English
    exactly as it did before, so #199 and #200 can land in either order
    without the page ever showing a bare number.
    """
    if isinstance(message, dict) and "code" in message:
        return {"ok": False, "error": message["text"], "err": message}
    return {"ok": False, "error": message}


# ---------------------------------------------------------------------
# The messages, as codes (#200)
#
# Roberto's ruling of 31 Aug 2026, the same shape #198 proved on
# eqsheet.py: the engine returns a code and its arguments, the interface
# puts them into words. This is the 8xx range.
#
# The three rules from #198 hold here too. A code is **permanent once
# published** -- never reused, never renumbered. **Severity is a field**,
# so a warning and an error about one thing need one code. And **the
# English stays here**, because it is the generation source for
# i18n/en.json and the only thing a traceback can quote.
#
# What is *not* here: every message this file forwards from the solver
# package -- `_err(_exc_msg(exc))` and friends. Those words are the
# package's, and they get codes in #199. Mixing them in would mean
# inventing 8xx numbers for sentences that are about to acquire 1xx-6xx
# ones.
# ---------------------------------------------------------------------

# Validation: the circuit description and the analysis
M_NO_DESC          = 801
M_DESC_TOO_LONG    = 802
M_DESC_CHARS       = 803
M_DESC_TOKEN       = 804
M_BAD_DOMAIN       = 805
M_BRACES_FD_ONLY   = 806
M_AC_NEEDS_OMEGA   = 807
M_OMEGA_CHARS      = 808
M_VARS_LIST        = 809
M_VAR_NAME         = 810
# Validation: added equations, conditions, unknowns, definitions
M_BARE_SI_SUFFIX   = 811
M_CIRCULAR_DEFINE  = 812
M_TOO_MANY_EXTRA   = 813
M_EXTRA_CHARS      = 814
M_TOO_MANY_UNKNOWN = 815
M_UNKNOWN_NAME     = 816
# Plotting
M_TF_NOT_NUMERIC   = 820
M_SWEEP_RANGE      = 821
M_NOT_IN_DC        = 822
M_NEEDS_MISSING    = 823
M_STILL_DEPENDS    = 824
# Evaluate, conditions, Solve equations
M_BAD_CONDITION    = 830
M_CONDITION_SOLVES = 831
M_CONDITION_SHAPE  = 832
M_NOTHING_TO_SOLVE = 833
# The schematic drawer
M_NO_DRAWER        = 840
M_NEED_CIRCUIT     = 841
# Mini-tools
M_UNKNOWN_TOOL     = 850
M_TOOL_NEEDS_1     = 851
M_TOOL_NEEDS_N     = 852
M_PF_TWO_VALUES    = 853
M_PF_NEEDS_NUMBERS = 854
# SPICE
M_BAD_DIRECTION    = 860
# Notes -- severity "note", which is why severity is a field and not a
# number range: these sit beside errors in the same catalogue and the
# same renderer.
M_NORMALISED       = 870
M_ORDINARY_VARIABLE = 871
M_DEFINE_SHADOWS   = 872
M_TR_STEP_ONE      = 873
M_TR_STEP_MANY     = 874
M_FD_IMPULSE_ONE   = 875
M_FD_IMPULSE_MANY  = 876
M_APPROX_SWITCHED  = 877
# Definitions again. 817-819 were the last free numbers next to the
# 811-816 block; 825-826 sit in the gap after plotting because the block
# had filled up by the time these were found. A code is permanent once
# published, so the tidy grouping loses to that rule rather than the
# other way round -- which is the rule working, not failing.
M_TOO_MANY_DEFINES = 817
M_DEFINE_TOO_LONG  = 818
M_DEFINE_FORM      = 819
M_DEFINE_CHARS     = 825
M_DEFINE_TWICE     = 826
# Limiting a TR run's results, and the mini-tools' one-number reader.
M_TR_CANNOT_LIMIT  = 827
M_GIVE_A_VALUE     = 828
M_NEEDS_A_NUMBER   = 829
# Found by the guard, after three hand sweeps had each declared
# themselves complete. See tools/check_messages.py.
M_COMPLEX_NEEDS_AC = 834
M_BAD_ELEMENT_NAME = 835
M_BAD_NODE_NAME    = 836
M_LOOKS_LIKE_ANSWER = 837
M_NO_SOLUTION_COND = 838
M_NO_REAL_SOLUTION = 839

CATALOGUE = {
    M_NO_DESC:       ("error", "Please enter a circuit description."),
    M_DESC_TOO_LONG: ("error", "Circuit description too long "
                               "(max %{max} characters)."),
    M_DESC_CHARS:    ("error", "Circuit description contains characters that "
                               "aren't used in Symbulator syntax. Allowed: "
                               "letters, digits, , : . + - * / ( ) ' ^"),
    M_DESC_TOKEN:    ("error", "Circuit description contains an invalid token."),
    M_BAD_DOMAIN:    ("error", "Unknown analysis type. Choose DC, AC, FD, or TR."),
    M_BRACES_FD_ONLY: ("error",
                       "The `{...}` shorthand is only allowed in FD. It marks "
                       "a source value as written in the time domain, and FD "
                       "is the one analysis that reads its sources in the "
                       "s-domain \u2014 every other analysis already reads "
                       "them as functions of time. Drop the braces."),
    M_AC_NEEDS_OMEGA: ("error", "AC analysis needs an angular frequency (omega)."),
    M_OMEGA_CHARS:   ("error", "Omega contains invalid characters."),
    M_VARS_LIST:     ("error", "Invalid variables list."),
    M_VAR_NAME:      ("error", "Invalid variable name: %{name}"),
    M_BARE_SI_SUFFIX: ("error",
                       "Added %{label} %{item} uses %{token} as a bare unit "
                       "suffix, which isn't allowed here (unlike a circuit "
                       "value field, an equation can't ask which meaning you "
                       "intend). Write the SI-unit meaning explicitly with an "
                       "apostrophe \u2014 %{si} \u2014 matching circuit "
                       "syntax, or the variable meaning with a star "
                       "\u2014 %{star}."),
    M_CIRCULAR_DEFINE: ("error", "Circular definition: %{loop}"),
    M_TOO_MANY_EXTRA: ("error", "Too many added %{label}s (max %{max})."),
    M_EXTRA_CHARS:   ("error", "Added %{label} contains invalid characters: "
                               "%{item}"),
    M_TOO_MANY_UNKNOWN: ("error", "Too many added unknowns (max %{max})."),
    M_UNKNOWN_NAME:  ("error", "Invalid unknown name: %{name}"),
    M_TF_NOT_NUMERIC: ("error",
                       "The transfer function must be numeric apart from s "
                       "\u2014 it still contains %{strays}. Write H(s) with "
                       "numbers everywhere else, e.g. 100/(s^2 + 10*s + 100)."),
    M_SWEEP_RANGE:   ("error", "The sweep range's end must be after its start."),
    M_NOT_IN_DC:     ("error", "'%{name}' was not found in the DC solution."),
    M_NEEDS_MISSING: ("error", "'%{name}' needs %{missing}, which was not "
                               "found in the DC solution."),
    M_STILL_DEPENDS: ("error",
                      "'%{name}' still depends on %{strays}, which has no "
                      "numeric value \u2014 pin it with a condition (e.g. "
                      "\"%{example}\") before plotting, or sweep it instead."),
    M_BAD_CONDITION: ("error", "Could not read the condition `%{line}`: "
                               "%{error}"),
    M_CONDITION_SOLVES: ("error",
                         "`%{line}` is an equation to solve rather than a "
                         "value to substitute, and Evaluate does not solve. "
                         "Put a single name on the left \u2014 `t = to` "
                         "\u2014 or use the Solve card."),
    M_CONDITION_SHAPE: ("error",
                        "`%{line}` is neither a value to substitute nor a "
                        "comparison. Write `t = to` to substitute, or "
                        "`pr1 > 0` to assume."),
    M_NOTHING_TO_SOLVE: ("error",
                         "Nothing left to solve for \u2014 every symbol in "
                         "those equations already has a value. Name an "
                         "unknown, or use Evaluate to compute a value "
                         "instead."),
    M_NO_DRAWER:     ("error", "This build has no schematic drawer. It needs "
                               "symbulator 0.5.0 or newer."),
    M_NEED_CIRCUIT:  ("error", "Enter a circuit first."),
    M_UNKNOWN_TOOL:  ("error", "Unknown tool `%{tool}`."),
    M_TOOL_NEEDS_1:  ("error", "`%{tool}` needs %{n} value: %{hint}."),
    M_TOOL_NEEDS_N:  ("error", "`%{tool}` needs %{n} values: %{hint}."),
    M_PF_TWO_VALUES: ("error", "`pf` needs two values: a voltage and a "
                               "current, as in `pf(v_1, i_r1)`."),
    M_PF_NEEDS_NUMBERS: ("error",
                         "`pf` needs numbers, and `%{arg}` still contains "
                         "%{unknown}. Solve the circuit in AC first, then "
                         "refer to its answers by name."),
    M_BAD_DIRECTION: ("error", "Unknown direction `%{direction}`."),
    M_NORMALISED:    ("note", "normalised '%{was}' to '%{now}' in %{element}"),
    M_ORDINARY_VARIABLE: ("note",
                          "'%{name}' was read as an ordinary variable; "
                          "SymPy's built-in meaning was ignored."),
    M_DEFINE_SHADOWS: ("note",
                       "%{name} is both a definition and one of this "
                       "circuit's own answers; the definition wins everywhere "
                       "it appears."),
    M_TR_STEP_ONE:   ("note", "Source '%{name}' with a value of %{value} is "
                              "simulated as a step source: %{value}*u(t)."),
    M_TR_STEP_MANY:  ("note", "Sources %{names} have constant values and are "
                              "simulated as step sources: %{shown}."),
    # Singular and plural as two codes, not one with a %{plural} slot:
    # English glues an "s" on, and no other language has to.
    M_FD_IMPULSE_ONE: ("note",
                       "Source %{names} took a constant s-domain value. That "
                       "is an impulse, not a steady level, so it contributes "
                       "nothing for t > 0. For a step of %{value} volts (or "
                       "amps) switched on at t = 0, write %{value}/s."),
    M_FD_IMPULSE_MANY: ("note",
                        "Sources %{names} took constant s-domain values. "
                        "Those are impulses, not steady levels, so they "
                        "contribute nothing for t > 0. For a step of "
                        "%{value} volts (or amps) switched on at t = 0, "
                        "write %{value}/s."),
    M_TOO_MANY_DEFINES: ("error", "Too many definitions (max %{max})."),
    M_DEFINE_TOO_LONG: ("error", "Definition is too long: %{item}"),
    M_DEFINE_FORM:   ("error", "Each definition needs the form "
                               "name = expression: %{item}"),
    M_DEFINE_CHARS:  ("error", "Definition contains invalid characters: "
                               "%{item}"),
    M_DEFINE_TWICE:  ("error", "%{name} is defined twice."),
    M_TR_CANNOT_LIMIT: ("error",
                        "Cannot limit the results to %{names}. A transient "
                        "analysis answers in element currents and node "
                        "voltages \u2014 an element's voltage drop, such as "
                        "v_r1, is worked out from its nodes and may be asked "
                        "for too. Powers are not available in TR. Check the "
                        "spelling against the names in Results."),
    M_GIVE_A_VALUE:  ("error", "Give a value."),
    M_NEEDS_A_NUMBER: ("error",
                       "`%{text}` still contains %{unknown}. These tools need "
                       "a number \u2014 solve the circuit first, then name "
                       "one of its answers."),
    M_COMPLEX_NEEDS_AC: ("error",
                         "The value of '%{name}' works out complex, and "
                         "complex values only apply to AC analysis. Switch "
                         "the analysis to AC, or rewrite the value so it "
                         "stays real."),
    M_BAD_ELEMENT_NAME: ("note",
                         "`%{name}` cannot be used as an element name. Its "
                         "answers would be spelled `%{produces}`, and "
                         "%{produces} is already %{owner} \u2014 so it would "
                         "be read as that rather than as your circuit. "
                         "Rename the element: `%{suggestion}` works."),
    M_BAD_NODE_NAME: ("note",
                      "`%{name}` cannot be used as a node name. Its voltage "
                      "would be spelled `%{produces}`, and %{produces} is "
                      "already %{owner}. Rename the node: `%{suggestion}` "
                      "works."),
    M_LOOKS_LIKE_ANSWER: ("note",
                          "Is that what you meant by `%{name}`? This circuit "
                          "has `%{target}`, so `%{name}` is its %{what}, and "
                          "`%{element}` has been given that as its value "
                          "\u2014 an element whose value tracks another "
                          "answer. That is legal and sometimes deliberate, "
                          "but it is unusual. If you meant `%{name}` as an "
                          "unknown of your own, rename it: `%{suggestion}`, "
                          "or anything not spelled like an answer."),
    M_NO_SOLUTION_COND: ("note",
                         "No solution satisfies the given conditions / "
                         "constraints."),
    M_NO_REAL_SOLUTION: ("note",
                         "No real solution. Untick \u201creal solutions "
                         "only\u201d to search the complex plane as well."),
    M_APPROX_SWITCHED: ("note",
                        "A decimal or scientific-notation value (like 0.1 or "
                        "2e3) was found in the inputs, so the answers can't "
                        "be exact -- switched \"Rounding\" from exact to "
                        "approximate."),
}


def msg(code, **args):
    """One message, as {code, args, severity, text}.

    Same shape as eqsheet.py's, deliberately: the page has one renderer
    for both, and a second shape would have meant a second one.
    """
    severity, template = CATALOGUE[code]
    text = template
    for k, v in args.items():
        text = text.replace("%{" + k + "}", str(v))
    return {"code": code, "args": {k: str(v) for k, v in args.items()},
            "severity": severity, "text": text}


MAX_DESC_LEN = 2000
MAX_OMEGA_LEN = 80
MAX_VARIABLES = 40

# Everything a legitimate circuit description or omega value can contain:
# element names, node numbers, values like 1e-6 / 4.7u / 'k / 5/s / 2*v_2,
# the `[a,b,c]` parallel-impedance shortcut (expand_shorthand turns it
# into pr(a,b,c) before it ever reaches sympify), separators, and basic
# arithmetic, and the `{...}` shorthand that marks an FD source value as
# written in the time domain -- expand_time_domain_braces turns it into
# t2s(...) before sympify, so no brace survives to be read as a Python set.
# Deliberately excluded: = ; " \ ` @ # $ % & ! ? < > | ~
# and whitespace other than space.
# The Greek delta is allowed for the same reason the two micro signs
# are: it is a character people actually type. It spells the impulse,
# as it does on the calculator, and expand_shorthand turns a following
# "(" into DiracDelta(. ASCII "delta(" works too, for anyone without
# the character to hand.
# The angle sign and the two degree characters go with them: a polar
# phasor is written `(20\u222030\u00b0)` in every textbook and in versions 7 and 8,
# and expand_angle_notation turns it into a rectangular number before
# anything downstream sees it. The masculine ordinal \u00ba is accepted
# alongside the real degree sign because the two look identical and the
# 2023 documentation uses both. The minus and en dash are there so a
# negative angle copied from that documentation is read, not refused.
# Beta and gamma joined mu and delta on 27 Aug 2026, for the same
# reason those were let in: the 2023 documentation's symbolic circuits
# use them as values (\u03b2*irb, v\u03b3), and the engine reads them fine.
_ALLOWED = re.compile("^[A-Za-z0-9_,.:+\\-*/()\\[\\]{}'^ \u00b5\u03bc\u03b4"
                      "\u03b2\u03b3\u2220\u00b0\u00ba\u2212\u2013]*$")
# Expert-mode equations/conditions additionally need "=".
_ALLOWED_EQ = re.compile("^[A-Za-z0-9_,.=+\\-*/()\\[\\]{}'^ \u00b5\u03bc\u03b4"
                         "\u03b2\u03b3\u2220\u00b0\u00ba\u2212\u2013]*$")
# The Solve panel's "Conditions / constraints" (solveq_ui) also allow
# < and > (and, via >=/<=, both together) -- a post-solve filter, not a
# substitution, so an actual inequality is meaningful there.
_ALLOWED_COND = re.compile("^[A-Za-z0-9_,.=<>+\\-*/()\\[\\]'^ \u00b5\u03bc]*$")
_VARNAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")
MAX_EXTRA = 20
MAX_EXTRA_LEN = 300

# Expert-mode equations/conditions are parsed the same way circuit values
# are -- imaginary units (i/I/j/J) and the calculator's apostrophe SI-unit
# shorthand (4.7'k) both work, because the engine's extra-equation/condition
# parsing already runs the same expand_shorthand()+safe_sympify() a circuit
# value goes through. What it can't do is a *bare* engineering suffix with
# no apostrophe (4.7k) the way a lone circuit field value can: that bare
# form is only ever auto-resolved when it's the *entire* field (so it can't
# accidentally rewrite part of a longer expression), and an equation is
# never just one field. So a bare suffix inside an equation/condition is
# caught here and rejected with a message pointing at the two forms that
# *are* always unambiguous, rather than left to fail deep in the solver as
# a raw sympify SyntaxError.
_BARE_SI_HINT = re.compile(
    r"(?<![\w'])\d+\.?\d*[kKMGTPmuµμnpfa](?![A-Za-z0-9_])")


def _bare_si_suffix_error(label, items):
    """None, or an error message naming the first added equation/condition
    that uses a bare SI suffix (see _BARE_SI_HINT above)."""
    for it in items:
        m = _BARE_SI_HINT.search(it)
        if m:
            tok = m.group(0)
            return msg(M_BARE_SI_SUFFIX, label=label, item=repr(it),
                       token=repr(tok),
                       si=f"{tok[:-1]}'{tok[-1]}",
                       star=f"{tok[:-1]}*{tok[-1]}")
    return None


#: The engine's own word for what an answer name means, used in the
#: "did you mean" note (837). Module-level and named so tools/i18n.py's
#: SRV_SOURCES can read it: these are the engine's vocabulary, looked up
#: by their English selves through tSrv(), exactly like the element
#: kinds. Inline in the function it was invisible to `check`, and one
#: English noun would have sat inside a translated sentence.
# The closing brace sits at column 0 on purpose: tools/i18n.py reads
# these tables by finding the name and scanning to the next line that
# starts with }, so a brace tucked at the end of the last entry makes it
# swallow whatever table comes next.
_QUANTITY_WORDS = {
    "v": "voltage", "i": "current", "p": "power",
    "q": "reactive power", "s": "apparent power",
    "r": "equivalent resistance",
    "z": "equivalent impedance",
    "y": "admittance", "?": "answer",
}

VALID_DOMAINS = {"dc", "ac", "fd", "tr"}


def _validate(desc: str, domain: str, omega: str, variables) -> str | None:
    """Return an error message, or None if the input looks safe and sane."""
    if not desc or not desc.strip():
        return msg(M_NO_DESC)
    if len(desc) > MAX_DESC_LEN:
        return msg(M_DESC_TOO_LONG, max=MAX_DESC_LEN)
    if not _ALLOWED.match(desc):
        return msg(M_DESC_CHARS)
    if "__" in desc:
        return msg(M_DESC_TOKEN)
    if domain not in VALID_DOMAINS:
        return msg(M_BAD_DOMAIN)
    # `{...}` marks a source value as written in time rather than in s.
    # That only means something in FD, which is the one analysis whose
    # sources are read in the s-domain. Anywhere else the braces would
    # reach SymPy and come back as "contains a set", which tells nobody
    # anything.
    if "{" in desc and domain != "fd":
        return msg(M_BRACES_FD_ONLY)
    if domain == "ac":
        if not omega or not omega.strip():
            return msg(M_AC_NEEDS_OMEGA)
        if len(omega) > MAX_OMEGA_LEN or not _ALLOWED.match(omega) or "__" in omega:
            return msg(M_OMEGA_CHARS)
    if variables:
        if not isinstance(variables, list) or len(variables) > MAX_VARIABLES:
            return msg(M_VARS_LIST)
        for v in variables:
            if not isinstance(v, str) or not _VARNAME.match(v):
                return msg(M_VAR_NAME, name=repr(v))

    # Names whose answers would collide with a Python or SymPy name. Refused
    # rather than warned about: this is not a case where the user might have
    # meant either reading, it is a name that cannot work. Parsed leniently --
    # if the description does not parse yet, the errors below are not ours to
    # report and the real parser will say so.
    try:
        from symbulator.elements import parse_circuit
        banned = banned_name_errors(parse_circuit(desc, expand_si=False))
    except Exception:
        banned = []
    if banned:
        return banned[0] if len(banned) == 1 else " ".join(banned)
    return None


_AND_SPLIT = re.compile(r"(?i)\s+and\s+")


def _expand_and(items):
    """Expand 'a and b' lines into separate clauses, so one line of
    added conditions can list more than one -- 'vin = 12 and pr2 = 0'
    becomes two clauses, each validated and applied on its own, rather
    than being sympify'd as a single (invalid) expression."""
    if not items:
        return items
    out = []
    for raw in items:
        out.extend(p.strip() for p in _AND_SPLIT.split(raw) if p.strip())
    return out


# ---------------------------------------------------------------------------
# The Define field
# ---------------------------------------------------------------------------
#
# `Define vx=va-vb` on the calculator put vx in the machine's own variable
# space, and every later mention of vx -- including inside the string passed
# to s\th() -- resolved against it. Version 9 parses text, so there is no
# such space to put anything in; the equivalent is to expand the name before
# the text is read. That is all this is: a substitution pass that runs first,
# so everything downstream sees exactly what the reader would have typed by
# hand.
#
# It replaces whole identifiers, never substrings. Symbulator's namespace is
# thick with one-letter prefixes -- v, i, p, r, e, j, s -- so `Define x = 3`
# under a substring rule would quietly turn `rx` into `r3` and `vx` into
# `v3`. The calculator matched whole names too.

MAX_DEFINES = 20
_DEFINE_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(\S.*?)\s*$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_defines(lines):
    """{name: expression} in the order given, or ({}, "why not").

    Each line is `name = expression`. A name may be defined once, and a
    definition may use earlier names -- see `expand_defines` -- but not
    itself, directly or round a chain.
    """
    table = {}
    if not lines:
        return table, None
    if isinstance(lines, str):
        lines = [ln for ln in re.split(r"[\r\n]+", lines) if ln.strip()]
    if len(lines) > MAX_DEFINES:
        return {}, msg(M_TOO_MANY_DEFINES, max=MAX_DEFINES)
    for raw in lines:
        if not isinstance(raw, str) or not raw.strip():
            continue
        if len(raw) > MAX_EXTRA_LEN:
            return {}, msg(M_DEFINE_TOO_LONG, item=repr(raw[:40]))
        m = _DEFINE_LINE_RE.match(raw)
        if not m:
            return {}, msg(M_DEFINE_FORM, item=repr(raw.strip()[:60]))
        name, expr = m.group(1), m.group(2)
        if not _ALLOWED_EQ.match(expr) or "__" in expr:
            return {}, msg(M_DEFINE_CHARS, item=repr(raw.strip()[:60]))
        if name in table:
            return {}, msg(M_DEFINE_TWICE, name=name)
        table[name] = expr
    err = _defines_cycle(table)
    if err:
        return {}, err
    return table, None


def _defines_cycle(table):
    """Message naming a circular definition, or None.

    Refusing beats expanding to a depth limit: a cycle is always a mistake,
    and the reader is better told which name closes the loop than handed a
    half-expanded expression."""
    state = {}                      # name -> 1 visiting, 2 done

    def walk(name, trail):
        if state.get(name) == 2:
            return None
        if state.get(name) == 1:
            loop = " -> ".join(trail[trail.index(name):])
            return msg(M_CIRCULAR_DEFINE, loop=loop)
        state[name] = 1
        for ident in _IDENT_RE.findall(table.get(name, "")):
            if ident in table:
                found = walk(ident, trail + [ident])
                if found:
                    return found
        state[name] = 2
        return None

    for name in table:
        found = walk(name, [name])
        if found:
            return found
    return None


# A single token: a name, a number, an SI-prefixed value (10'k), or the
# bare engineering shorthand (1k). Anything carrying an operator is not.
_ATOM_RE = re.compile(r"^[A-Za-z0-9_.']+$")


def _wrapped(expr: str) -> str:
    """A definition as it goes into the text: bracketed unless it is a
    single token.

    The exception is not cosmetic. Bracketing `1k` gives `(1k)`, which the
    ambiguity check no longer recognises as the bare engineering shorthand
    it is -- so the question of whether the reader meant 1000 or 1*k would
    go unasked, and the parse would fail further downstream with a much
    worse message."""
    return expr if _ATOM_RE.match(expr.strip()) else f"({expr})"


def expand_defines(text, table):
    """Every defined name in `text` replaced by its expression.

    Bracketed, because a definition is a phrase and not a token: with
    `a = 1+2`, `3*a` has to become `3*(1+2)` and not `3*1+2`. A definition
    that is already a single number or name is dropped in bare, so chains
    do not pile up brackets around nothing.

    Repeated until nothing changes, so a definition may be written in terms
    of another whichever order the two were entered. parse_defines has
    already refused cycles, so this terminates; the counter is a belt on
    top of that brace."""
    if not text or not table:
        return text
    out = str(text)
    for _ in range(MAX_DEFINES + 1):
        new = _IDENT_RE.sub(
            lambda m: (_wrapped(table[m.group(0)]) if m.group(0) in table
                       else m.group(0)), out)
        if new == out:
            return out
        out = new
    return out


def expand_defines_in_desc(desc, table):
    """The same, but only in a circuit description's *values*.

    Names and nodes are left alone: `Define r1 = 5` must not rename the
    element r1, and a node called `a` must stay a node. _IDENTIFIER_FIELD_IDX
    is the same map prepare_inputs uses to draw that line."""
    if not desc or not table:
        return desc
    from symbulator.elements import (parse_circuit, _IDENTIFIER_FIELD_IDX,
                                     TWO_PORT_KINDS)
    try:
        elements = parse_circuit(desc, expand_si=False)
    except Exception:
        # Not parseable yet. Leave it be and let the real validation say so
        # -- expanding blind would have to treat names and nodes as values.
        return desc
    changed = False
    # A two-port without its parameter term has the tacit one,
    # [<name>11,<name>12,<name>21,<name>22] (#163). When Define supplies
    # any of those names, the term is materialised so the definitions
    # have somewhere to land -- Case B of the design. Untouched
    # otherwise, so a description that never mentions parameters is
    # echoed exactly as typed.
    for el in elements:
        if el.kind in TWO_PORT_KINDS and len(el.fields) == 2:
            names = [f"{el.name}{ij}" for ij in ("11", "12", "21", "22")]
            if any(n in table for n in names):
                el.fields.append("[" + ",".join(names) + "]")
                changed = True
    for el in elements:
        ident = _IDENTIFIER_FIELD_IDX.get(el.kind, ())
        for idx in range(len(el.fields)):
            if idx in ident:
                continue
            new = expand_defines(el.fields[idx], table)
            if new != el.fields[idx]:
                el.fields[idx] = new
                changed = True
    if not changed:
        return desc
    return ":".join(e.name + "," + ",".join(e.fields) for e in elements)


def define_shadow_notices(table, desc):
    """Warn where a definition takes over a name the circuit itself answers.

    `Define ir1 = 5` on a circuit containing r1 replaces every mention of
    that current with 5, including the ones meant as the real answer. That
    is legal and occasionally wanted, so it warns rather than refuses."""
    if not table or not desc:
        return []
    from symbulator.elements import parse_circuit
    try:
        elements = parse_circuit(desc, expand_si=False)
    except Exception:
        return []
    answers = set(answer_aliases(elements) or {})
    clash = [n for n in table if n in answers]
    return [msg(M_DEFINE_SHADOWS, name=n) for n in clash]


def _validate_extras(equations, unknowns, conditions) -> str | None:
    """Validate the expert-mode extras (lists of strings)."""
    # Conditions check against _ALLOWED_COND, which admits < and >:
    # solver 0.5.19 accepts inequality conditions (`is > 0`), and until
    # 28 Aug 2026 this line used _ALLOWED_EQ, which refused them at the
    # app door -- the one validator out of three that did. The Solve
    # and Evaluate cards' condition boxes always used _ALLOWED_COND.
    for label, items, rx in (("equation", equations, _ALLOWED_EQ),
                             ("condition", conditions, _ALLOWED_COND)):
        if not items:
            continue
        if len(items) > MAX_EXTRA:
            return msg(M_TOO_MANY_EXTRA, label=label, max=MAX_EXTRA)
        for it in items:
            if (not isinstance(it, str) or len(it) > MAX_EXTRA_LEN
                    or not rx.match(it) or "__" in it):
                return msg(M_EXTRA_CHARS, label=label, item=repr(it))
        bare_err = _bare_si_suffix_error(label, items)
        if bare_err:
            return bare_err
    if unknowns:
        if len(unknowns) > MAX_EXTRA:
            return msg(M_TOO_MANY_UNKNOWN, max=MAX_EXTRA)
        for u in unknowns:
            if not isinstance(u, str) or not _VARNAME.match(u):
                return msg(M_UNKNOWN_NAME, name=repr(u))
    return None


# ---------------------------------------------------------------------------
# Answer formatting: rounding, decimal and SI-prefix notation
# ---------------------------------------------------------------------------

MAX_DIGITS = 15


def _clean_digits(raw) -> int:
    """Significant digits requested for the answers; 0 means 'leave the
    exact symbolic form alone', which is the default and the whole point
    of a symbolic solver."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    if n < 1:
        return 0
    return min(n, MAX_DIGITS)


def _round_expr(expr, digits: int):
    """Round an answer to `digits` significant figures. Exact integers
    are left as they are -- a node sitting at exactly 36 V should read
    "36", not "36.00", even when four digits were asked for. Everything
    else (rationals, floats, and the numeric coefficients inside a
    symbolic expression) goes through sympy's N()."""
    if not digits:
        return expr
    import sympy as sp
    try:
        if expr.is_Integer:
            return expr
        return sp.N(expr, digits)
    except Exception:
        return expr


# Engineering (SI) prefixes, matching the set accepted on the input
# side so a formatted answer can be pasted straight back into a circuit.
_SI_PREFIXES = {
    18: "E", 15: "P", 12: "T", 9: "G", 6: "M", 3: "k", 0: "",
    -3: "m", -6: "u", -9: "n", -12: "p", -15: "f", -18: "a",
}
_SI_LATEX = {**_SI_PREFIXES, -6: r"\mu"}
_SI_DEFAULT_DIGITS = 4


def _approx_format(expr):
    """Force a numeric answer into decimal form: 15/2 -> 7.5, the TI's
    approximate mode. Unlike the rounding setting this imposes no digit
    count -- it uses the shortest decimal that round-trips, so 7.5 stays
    "7.5" instead of "7.50000000000000". Exact integers are left alone,
    and anything with a free symbol returns None so the caller can fall
    back to sympy's own numeric evaluation."""
    try:
        if expr.free_symbols or not expr.is_number or not expr.is_finite:
            return None
        if expr.is_Integer:
            text = str(expr)
            return text, text
        val = complex(expr)
    except Exception:
        return None

    if abs(val.imag) < 1e-30:
        text = repr(val.real)
        return text, text
    re_text, im_text = repr(val.real), repr(abs(val.imag))
    if abs(val.real) < 1e-30:
        lead = "-" if val.imag < 0 else ""
        return f"{lead}{im_text}j", f"{lead}{im_text}\\text{{j}}"
    sign = "-" if val.imag < 0 else "+"
    return (f"{re_text} {sign} {im_text}j",
            f"{re_text} {sign} {im_text}\\text{{j}}")


def _si_band(magnitude: float, digits: int):
    """Which power-of-1000 band a magnitude belongs in. Returns None if
    it falls outside the prefix range, where plain notation reads
    better."""
    import math

    if magnitude == 0 or not math.isfinite(magnitude):
        return 0
    exp3 = int(math.floor(math.log10(magnitude) / 3)) * 3
    # Rounding can tip a mantissa past 1000 (999.99 at 3 digits), which
    # belongs one band up: 1k reads better than 1000.
    if abs(float(f"%.{digits}g" % (magnitude / 10.0 ** exp3))) >= 1000:
        exp3 += 3
    if exp3 < -18 or exp3 > 18:
        return None
    return exp3


def _si_mantissa(x: float, exp3: int, digits: int):
    """Mantissa text for `x` in the given band, or None if it can't be
    written without falling back to exponent notation."""
    text = f"%.{digits}g" % (x / (10.0 ** exp3))
    return None if ("e" in text or "E" in text) else text


def _si_format(expr, digits: int, unit: str = ""):
    """SI-format a numeric answer: 0.002 -> "2m", 1234 -> "1.234k". When
    a unit is supplied the prefix attaches to it, so a current reads
    "6 mA" rather than "6m A". Complex answers (AC phasors) share one
    prefix across both parts -- "(50 - 50*I) mA" -- which is how the
    quantity would be written by hand. Returns None for anything with a
    free symbol in it: there's no meaningful prefix for an expression
    like r_b*vin/(r_a + r_b)."""
    digits = digits or _SI_DEFAULT_DIGITS
    try:
        if expr.free_symbols or not expr.is_number or not expr.is_finite:
            return None
        val = complex(expr)
    except Exception:
        return None

    is_complex = abs(val.imag) > 1e-30
    scale = max(abs(val.real), abs(val.imag)) if is_complex else abs(val.real)
    exp3 = _si_band(scale, digits)
    if exp3 is None:
        return None

    prefix, latex_prefix = _SI_PREFIXES[exp3], _SI_LATEX[exp3]
    unit_plain = f"{prefix}{_UNIT_PLAIN.get(unit, unit)}" if unit else prefix
    prefix_latex = f"\\mathrm{{{latex_prefix}}}" if prefix else ""
    unit_latex = prefix_latex + (_UNIT_LATEX.get(unit, unit) if unit else "")

    def join(body_plain, body_latex):
        """Attach the unit (plain text and LaTeX forms) to a formatted
        number, or return the number unchanged if there's no unit to
        attach -- factored out because both the real-only and complex
        branches below need to do this same last step."""
        if not unit_plain and not unit_latex:
            return body_plain, body_latex
        return (f"{body_plain} {unit_plain}".strip(),
                f"{body_latex}\\,{unit_latex}" if unit_latex else body_latex)

    if not is_complex:
        text = _si_mantissa(val.real, exp3, digits)
        return None if text is None else join(text, text)

    re_text = _si_mantissa(val.real, exp3, digits)
    im_text = _si_mantissa(abs(val.imag), exp3, digits)
    if re_text is None or im_text is None:
        return None
    if abs(val.real) < 1e-30:                       # purely imaginary
        lead = "-" if val.imag < 0 else ""
        return join(f"{lead}{im_text}j", f"{lead}{im_text}\\text{{j}}")
    sign = "-" if val.imag < 0 else "+"
    # Parenthesised so the shared prefix/unit clearly covers both parts.
    return join(f"({re_text} {sign} {im_text}j)",
                f"\\left({re_text} {sign} {im_text}\\text{{j}}\\right)")


# ---------------------------------------------------------------------------
# How the imaginary unit is shown
# ---------------------------------------------------------------------------
#
# Electrical engineering writes j, not i, because i is current. SymPy
# always prints I internally, so we convert at the display step only --
# the stored values keep SymPy's own form, which is what the evaluator
# re-reads, so a round trip can never corrupt them.
#
# The plain-text form deliberately emits the literal "5.0j" rather than
# "5.0*j": the first re-parses back to an imaginary number, the second
# comes back as a variable.

#: The quantities a phasor angle means something for. Average power is
#: deliberately absent: it is a real number, and dressing it up as
#: "1234 angle 0 degrees" would suggest a phase it does not have. Complex
#: power is present because its polar form is the apparent power and the
#: power-factor angle, which is how it is usually quoted.
_PHASOR_UNITS = frozenset({"V", "A", "ohm", "Ω", "VA"})


def _polar_format(expr, digits: int, unit: str, si: bool = False):
    """A phasor as magnitude and angle -- `5∠53.13°` -- or None if
    this value is not one.

    Returns None, leaving the caller's rectangular formatting to run, for
    an expression that still holds free symbols (the angle of
    `r_b*vin/(r_a + r_b)` is not a number) and for a quantity whose unit
    is not in `_PHASOR_UNITS`.

    A real phasor still gets an angle, of 0 or 180 degrees. That is what
    version 7's `aa` does -- Example 12.10's line current prints there as
    `56.78∠0.°` -- and it is what makes the setting visibly do
    something in a purely resistive circuit.
    """
    import sympy as sp

    if unit not in _PHASOR_UNITS:
        return None
    if getattr(expr, "free_symbols", None):
        return None
    def _num(x):
        """Always numeric, unlike _round_expr, which returns the
        expression untouched under "exact". sp.deg() builds the symbolic
        180*arg(z)/pi, so without this an exact-mode angle printed as
        "180(-1.249...)" with a stray pi in it. A phasor angle in degrees
        is a measurement rather than a closed form -- the same trade the
        SI-prefix setting makes, and what version 7's `aa` does."""
        return sp.N(x, digits) if digits else sp.N(x)

    try:
        z = sp.N(sp.simplify(expr))
        # A magnitude and an angle are both real, but evaluating them from
        # float inputs can leave a crumb of imaginary part behind --
        # "19.3649 + 0.e-13*I" -- which is arithmetically nothing and
        # visually a mess. Take the real part after evaluating, as the
        # `aa` mini-tool does.
        magnitude = sp.re(_num(sp.Abs(z)))
        # arg(0) has no value; a zero phasor is conventionally 0 at 0.
        angle = sp.Integer(0) if z == 0 else sp.re(_num(sp.deg(sp.arg(z))))
    except Exception:                                         # noqa: BLE001
        return None
    if (getattr(magnitude, "free_symbols", None)
            or getattr(angle, "free_symbols", None)):
        return None

    if si:
        # Prefix the magnitude only. An angle in degrees is already the
        # size it should be, and "3.26 m at -3.74 milli-degrees" would be
        # nonsense.
        shown = _si_format(magnitude, digits, "")
        mag_plain, mag_latex = (shown if shown
                                else (str(magnitude), sp.latex(magnitude)))
    else:
        mag_plain, mag_latex = str(magnitude), sp.latex(magnitude)

    return (f"{mag_plain}∠{angle}°",
            rf"{mag_latex} \angle {sp.latex(angle)}^\circ")


def _plain_with_j(expr) -> str:
    """str() with the imaginary unit written the engineering way."""
    import sympy as sp

    text = str(expr)
    if not expr.has(sp.I):
        return text
    # "5.0*I" -> "5.0j";  "I*x" or a lone "I" -> "1j..."
    text = re.sub(r"(?<![\w.])(\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\*I(?![\w])",
                  r"\1j", text)
    text = re.sub(r"(?<![\w.])I(?![\w])", "1j", text)
    return text


def _latex_with_j(expr) -> str:
    """LaTeX with an upright j, per IEEE house style."""
    import sympy as sp

    try:
        return sp.latex(expr, imaginary_unit="tj")
    except TypeError:            # very old sympy
        return sp.latex(expr)


# ---------------------------------------------------------------------------
# Content checks, explanatory notes, and error-message formatting
# ---------------------------------------------------------------------------

# A numeric literal written straight against the imaginary unit: 5i, 2.5J,
# .5i. The leading lookbehind keeps it off the tail of an identifier -- the
# `1i` inside `vr1i` is not a number followed by the unit -- and the trailing
# lookahead keeps it off the head of one, so `3irx` stays a name.
_IMPLICIT_IMAGINARY = re.compile(r"(?<![\w.])(\d+\.?\d*|\.\d+)([iIjJ])(?![\w])")


def normalise_imaginary(desc: str, domain: str = "ac"):
    """Rewrite every spelling of the imaginary unit into the canonical
    engineering form, so `3*i`, `3*I`, `3*J` and a bare `j` all become
    `3j` / `1j` in the description the user can see. Returns
    (new_desc, [notes]); the description is returned unchanged when
    nothing needed rewriting, so we never reformat what the user typed
    for no reason.

    i/I/j/J only mean the imaginary unit in AC (see
    `symbulator.si_prefix._allowed_namespace`), so outside AC this is a
    no-op: those letters are ordinary variable names there and nothing
    should be rewritten. `domain` defaults to "ac" so any caller that
    hasn't been updated to pass it keeps today's behaviour."""
    import sympy as sp
    from symbulator.elements import (parse_circuit, _IDENTIFIER_FIELD_IDX,
                                     TWO_PORT_KINDS)
    from symbulator.si_prefix import safe_sympify, expand_shorthand

    if domain != "ac":
        return desc, []

    try:
        # expand_si=False: keep SI-prefix shorthand (4.7'M) as typed in
        # fields that don't need touching -- only the field(s) actually
        # being rewritten below go through a real expansion (needed for
        # safe_sympify to parse them), so a circuit like "e1,1,0,10+5*i"
        # next to "r1,1,2,4.7'k" doesn't lose the resistor's SI notation
        # just because the source needed its imaginary unit normalised.
        elements = parse_circuit(desc, expand_si=False)
    except Exception:
        return desc, []

    notes, changed = [], False
    for el in elements:
        for idx in range(len(el.fields)):
            if idx in _IDENTIFIER_FIELD_IDX.get(el.kind, ()):
                continue                      # a node or element reference
            original = el.fields[idx]
            # A two-port's parameter term (#163) is a LIST, not a value:
            # sympifying it whole would evaluate the pr(...) encoding as
            # the parallel-combination function and collapse the four
            # entries into one number. Normalise each entry on its own
            # and reassemble in the bracket notation the user types.
            if idx == 2 and el.kind in TWO_PORT_KINDS:
                from symbulator.elements import two_port_param_texts
                try:
                    entries = two_port_param_texts(el)
                except Exception:
                    continue
                if not entries:
                    continue
                new_entries = []
                for entry in entries:
                    raw = _IMPLICIT_IMAGINARY.sub(r"\1*\2", entry)
                    if re.search(r"(?<![\w.])[iIJ](?![\w])", raw):
                        try:
                            expr = safe_sympify(expand_shorthand(raw, si=True))
                            if expr.has(sp.I):
                                entry_new = _plain_with_j(expr)
                                if entry_new != entry:
                                    notes.append(msg(
                                        M_NORMALISED, was=entry,
                                        now=entry_new, element=el.name))
                                    entry = entry_new
                                    changed = True
                        except Exception:
                            pass
                    new_entries.append(entry)
                el.fields[idx] = "[" + ",".join(new_entries) + "]"
                continue
            # `10+5i` the way it is written on paper: a number against the
            # imaginary unit with no operator between them. SymPy will not
            # parse that -- it reads as one malformed literal -- so put the
            # multiplication back before anything else looks at it.
            #
            # The number must not itself be the tail of a name, which is why
            # this matches the numeric literal rather than just looking at
            # the character before the letter: in `vr1i` the `1` is preceded
            # by `r`, the lookbehind fails, and the value is left alone. A
            # letter following the unit already disqualifies it, so `3irx`
            # and `2*i_r1` are untouched either way.
            raw = _IMPLICIT_IMAGINARY.sub(r"\1*\2", original)
            # Only a value that actually spells the imaginary unit as
            # `i`, `I` or `J` needs respelling. One already written in
            # j-form is left exactly as typed (#169): rewriting it
            # anyway produced a "normalised '8-6j' to '8 - 6j'" note on
            # nearly every AC example in the library -- a yellow box
            # for a spacing change -- and, worse, the sympify-and-
            # reprint step *evaluated* structure, collapsing a typed
            # `4+20j+pr(16,...)` into an opaque fraction. The same
            # guard skips any value containing a function call: calls
            # cannot be reprinted without evaluating them.
            if not re.search(r"(?<![\w.])[iIJ](?![\w])", raw):
                continue
            if re.search(r"[A-Za-z_]\w*\s*\(", raw):
                continue
            try:
                expr = safe_sympify(expand_shorthand(raw, si=True))
            except Exception:
                continue
            if not (getattr(expr, "has", None) and expr.has(sp.I)):
                continue
            canonical = _plain_with_j(expr)
            if canonical != original:
                notes.append(msg(M_NORMALISED, was=original,
                                 now=canonical, element=el.name))
                el.fields[idx] = canonical
                changed = True

    if not changed:
        return desc, []
    rebuilt = ":".join(e.name + "," + ",".join(e.fields) for e in elements)
    return rebuilt, notes


def _complex_value_error(elements, domain: str):
    """Complex component values only mean something in AC. In DC the
    values are real by definition, and FD/TR take their sources in the
    s-domain, where legitimate inputs have real coefficients and any
    complex behaviour comes from the poles of the solution. Returns an
    error message, or None.

    i/I/j/J are only reserved as the imaginary unit in AC (see
    `symbulator.si_prefix._allowed_namespace`), so outside AC they parse
    as ordinary variables and can no longer be the cause of a value
    working out complex here -- the only way to reach this now is
    genuine complex math (e.g. sqrt(-4)), so the message no longer needs
    to guess between two possible mistakes."""
    import sympy as sp
    from symbulator.si_prefix import safe_sympify, expand_value

    if domain == "ac":
        return None
    for el in elements:
        for idx in (2, 3):
            if idx >= len(el.fields) or el.kind not in ("r", "l", "c", "e", "j", "m", "t"):
                continue
            try:
                expr = safe_sympify(expand_value(el.fields[idx], "si"),
                                    reserve_imaginary=False)
            except Exception:
                continue
            if getattr(expr, "has", None) and expr.has(sp.I):
                return msg(M_COMPLEX_NEEDS_AC, name=el.name)
    return None


def _hijack_notes(elements, reserve_imaginary: bool = True):
    """One note per name that SymPy would have reinterpreted, so the user
    learns it was read as an ordinary variable instead. `reserve_imaginary`
    should match the domain the elements were parsed for (see
    `symbulator.si_prefix.hijacked_names`) so i/I/j/J aren't reported as
    "hijacked" when they were in fact read as ordinary variables."""
    from symbulator.si_prefix import hijacked_names

    # A name that belongs to the circuit is obviously the circuit's --
    # a feedback resistor named `rf` is the resistor, not SymPy's rising
    # factorial -- so no note for those (#169). The note keeps its job
    # for names that shadow SymPy and name nothing in the circuit.
    own = {el.name for el in elements}
    seen, notes = set(), []
    for el in elements:
        for idx in (2, 3):
            if idx >= len(el.fields):
                continue
            for name in hijacked_names(el.fields[idx], reserve_imaginary=reserve_imaginary):
                if name.lower() in own:
                    continue
                if name not in seen:
                    seen.add(name)
                    notes.append(msg(M_ORDINARY_VARIABLE, name=name))
    return notes


def _impulse_notes(elements, domain: str):
    """FD reads source values in the s-domain, so a plain number there is
    an impulse, not a steady level: `10` means 10·δ(t), whose value for
    every t > 0 is zero. That is correct, and it is also the single most
    confusing thing a newcomer can meet -- a 10 V source whose node
    reads 0 V. Say so rather than letting them find out.

    TR says the opposite, and used to say the same. Before issue #77 it
    read its sources in the s-domain too, so the impulse warning was true
    of both. tr() now moves each source into the s-domain itself -- a
    constant becomes value/s, a step -- so in TR a plain `5` is a 5 V
    step, and the old warning was contradicting the answer printed
    beneath it: the built-in "RC step response (TR)" example fired it
    while returning v_2 = 5 - 5*exp(-1000t), precisely a step response.

    Dropping it there left no note at all, which is not the same as an
    explanation -- the reader still has to learn that a bare number means
    a step in one analysis and an impulse in the next. So TR now states
    what the value became."""
    if domain not in ("fd", "tr"):
        return []
    from symbulator.si_prefix import safe_sympify

    import sympy as sp

    culprits = []
    for el in elements:
        if el.kind not in ("e", "j") or len(el.fields) < 3:
            continue
        try:
            # fd/tr are never AC, so i/I/j/J are ordinary variables here.
            expr = safe_sympify(el.fields[2], reserve_imaginary=False)
        except Exception:  # noqa: BLE001
            continue
        if expr.is_number and expr != 0:
            culprits.append((el.name, sp.sstr(expr)))
    if not culprits:
        return []
    names = ", ".join(f"'{n}'" for n, _ in culprits)
    val = culprits[0][1]
    plural = "s" if len(culprits) > 1 else ""

    if domain == "tr":
        # The helpful direction: tr() has already moved these into the
        # s-domain, so say which waveform each one became. Without this
        # the reader has to know that a bare number is a step here but an
        # impulse one analysis over.
        if len(culprits) == 1:
            name, value = culprits[0]
            return [msg(M_TR_STEP_ONE, name=name, value=value)]
        shown = ", ".join(f"'{n}' as {v}*u(t)" for n, v in culprits)
        return [msg(M_TR_STEP_MANY, names=names, shown=shown)]

    code = M_FD_IMPULSE_MANY if plural else M_FD_IMPULSE_ONE
    return [msg(code, names=names, value=val)]


# A decimal point, or genuine scientific notation (a digit glued
# directly to e/E glued to digits -- see the Circuit syntax reference's
# "Numbers, constants and the imaginary unit" section: anywhere else,
# e/E is an ordinary variable name, not scientific notation) is what
# makes SymPy read a value as an approximate float rather than an exact
# Integer/Rational the moment it's parsed.
_APPROX_NUMBER_RE = re.compile(r"\d+\.\d*|\.\d+|\d[eE][+-]?\d+")


def _has_approx_value(*texts) -> bool:
    """True if any of the given strings contains a decimal-point or
    scientific-notation numeric literal. Used to warn the user (and
    switch "exact" rounding to "approximate") when their inputs already
    contain an approximate value -- "exact" mode only skips the
    rounding step, so if the underlying number was never exact to begin
    with, "exact" mode just shows that same approximation completely
    unrounded, which looks more precise than it is rather than less."""
    for text in texts:
        if text and _APPROX_NUMBER_RE.search(text):
            return True
    return False


def _approx_value_notes(has_approx: bool) -> list:
    """One explanatory note for `_has_approx_value`, phrased for whoever
    is reading the results rather than as a bare flag -- callers that
    also auto-switch rounding to "approximate" say so in the same
    breath, so the note doubles as an explanation for why the answers
    changed shape."""
    if not has_approx:
        return []
    return [msg(M_APPROX_SWITCHED)]


def _exc_text(exc: Exception) -> str:
    """Human-readable text for any exception crossing the process pipe.
    Some exceptions (mpmath's ZeroDivisionError, for one) carry an empty
    message, which would otherwise reach the user as a blank error box."""
    msg = str(exc).strip()
    if not msg:
        msg = f"{type(exc).__name__} while solving (no further detail)."
    return msg[:400] + ("..." if len(msg) > 400 else "")


def _exc_msg(exc: Exception):
    """A coded message when the engine sent one; otherwise its text.

    Since #199 a CircuitError carries `code` and `args_map`, and
    forwarding those instead of `str(exc)` is the whole point: it is what
    lets the page put the *engine's* words into the reader's language
    rather than only this file's.

    Everything else -- a SymPy error, a ZeroDivisionError, an exception
    from a library we do not own -- has no code and keeps travelling as
    text. That is honest rather than lazy: those words are not ours to
    render, and `_err` takes a string exactly so they can pass through.
    """
    code = getattr(exc, "code", None)
    if code is None:
        return _exc_text(exc)
    return {"code": code,
            "args": {k: str(v)
                     for k, v in (getattr(exc, "args_map", None) or {}).items()},
            "severity": getattr(exc, "severity", "error"),
            "text": _exc_text(exc)}


# Display order for the element cards: sources first (voltage, then
# current), then the passives one type at a time, then everything else.
# Python's sort is stable, so within a type the elements keep the order
# they were written in the circuit description.
_KIND_ORDER = {
    "e": 0,   # voltage sources
    "j": 1,   # current sources
    "r": 2, "l": 3, "c": 4,          # passives
    "o": 5,   # op-amps
    "t": 6,   # transformers
    "s": 7,   # short circuits
    "z": 8, "y": 8, "h": 8, "g": 8, "a": 8, "b": 8,   # two-port blocks
    "m": 9,   # mutual inductance (folded into its inductors; no card)
}


def _natural_key(name: str):
    """Sort key that orders names the way a person reads them, so e2
    comes before e10 rather than after it (which is what a plain
    alphabetical sort would do once the numbering reaches double
    digits)."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


# What an element's *value* is measured in -- the unit an Expert Mode
# unknown inherits when it stands for one (see `_value_units`).
_VALUE_UNITS = {"r": "ohm", "l": "H", "c": "F", "e": "V", "j": "A",
                "m": "H"}

# Identifiers inside a value that carry no dimension of their own, so a
# value built from the unknown and these alone still measures whatever
# the element measures: the unit step and the impulse, their argument,
# the Laplace variable and pi. `a*u(t)` is a volt-valued step source;
# `gm*v_rg` is not a current -- it is a transconductance times a
# voltage, and that second dimensional name is what says so.
#
# Deliberately short. Every name added here is a name that can no longer
# block a wrong unit, so a symbol only belongs in it when it is
# genuinely dimensionless in every value it can appear in.
_DIMENSIONLESS = frozenset({"t", "u", "s", "pi",
                            "diracdelta", "heaviside"})

_VALUE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _norm_varname(name: str) -> str:
    """The solver's spelling-equivalence key (`engine._norm_name`):
    `i_r1`, `ir1` and `IR1` are one name to it."""
    return (name or "").replace("_", "").lower()


def _value_units(elements) -> dict:
    """Folded symbol name -> the unit it is measured in, for every free
    symbol that *is* an element's value.

    Expert Mode's unknowns are ordinary free symbols -- `vs` in
    `es,e,0,vs`, `a` in `e,1,0,a*u(t)`, `r2` in `r2,1,0,r2` -- so there
    is no prefix on the answer key to read a unit from, which is why
    they were the one part of the Results with no units on them. The
    circuit says what they measure: a voltage source's value is volts
    whatever the reader called it.

    The unknown has to be the *whole* of the value, up to things that
    carry no dimension. `a*u(t)` is still volts; `gm*v_rg` is not amps,
    and the presence of a second dimensional name is what says so. A
    symbol that is the value of two elements of different kinds gets no
    unit either -- there is no answer that is right for both."""
    out: dict = {}
    for el in elements:
        unit = _VALUE_UNITS.get(el.kind)
        raw = (getattr(el, "value", None) or "").replace("'", "")
        if not unit or not raw.strip():
            continue
        names = [_norm_varname(t) for t in _VALUE_IDENT.findall(raw)]
        for name in set(names):
            if name in _DIMENSIONLESS:
                continue
            if any(o != name and o not in _DIMENSIONLESS for o in names):
                continue
            out[name] = unit if out.get(name, unit) == unit else ""
    return {k: v for k, v in out.items() if v}


_KIND_LABEL = {
    "r": "resistor", "l": "inductor", "c": "capacitor",
    "e": "voltage source", "j": "current source", "o": "op-amp",
    "m": "mutual inductance", "s": "short circuit", "t": "transformer",
    "z": "two-port", "y": "two-port", "h": "two-port", "g": "two-port",
    "a": "two-port", "b": "two-port",
}

# Per-element derived keys, in display order, with human labels.
_ELEMENT_KEYS = [
    ("p_{n}", "p", "power consumed", "W"),
    ("ap_{n}", "p", "average power", "W"),
    # Complex power S = V*conj(I): its magnitude is apparent power in
    # volt-amperes, its real part watts, its imaginary part reactive var.
    ("s_{n}", "s", "complex power", "VA"),
    ("z_{n}", "z", "impedance seen", "ohm"),
    ("r_{n}", "r", "resistance seen", "ohm"),
]

# Units for the special tools' named answers.
_TOOL_UNITS = {"vth": "V", "ino": "A", "req": "ohm", "zeq": "ohm",
               "pmax": "W",
               # #292: the three load quantities. Symbolic in `load`, so
               # the unit is only ever appended once a value is put in.
               "irl": "A", "vrl": "V", "prl": "W", "aprl": "W"}
# Two-port parameter matrices carry mixed units by construction: z is
# all impedances, y all admittances, h and g are mixed, and a/b are
# dimensionless ratios mixed with the two. Rather than mislabel them,
# only the uniform ones get a unit.
_PORT_UNITS = {"z": "ohm", "y": "S"}

# Human-readable descriptions for the th/er tool's named answers, matching
# the labels the main circuit solve already gives every node voltage and
# element answer (see _ELEMENT_KEYS below) -- so the special tools stop
# being the only place that shows a bare variable name with no explanation.
# Lower case like those labels (#287, Roberto, 6 Sep 2026); Thevenin and
# Norton keep their capital because they are names.
_TOOL_LABELS = {
    "vth": "Thevenin voltage", "ino": "Norton current",
    "req": "equivalent resistance", "zeq": "equivalent impedance",
    "pmax": "maximum deliverable power",
    # #292: the calculator's answers for a load connected to the
    # equivalent (v8's th, after "Interested? [y/n]"), in its words.
    "irl": "current in load", "vrl": "voltage drop in load",
    "prl": "power consumed in load",
    "aprl": "average power consumed in load",
}

# Same idea for the two-port (port) tool: one textbook description per
# parameter position, shared across all six kinds since z11/y11/h11/g11/
# a11/b11 all play the same structural role (input, under whichever
# port-2 condition -- open or short -- defines that kind of parameter),
# just naming a different physical quantity each time.
_PORT_LABELS = {
    "z": {"11": "open-circuit input impedance",
          "12": "open-circuit reverse transfer impedance",
          "21": "open-circuit forward transfer impedance",
          "22": "open-circuit output impedance"},
    "y": {"11": "short-circuit input admittance",
          "12": "short-circuit reverse transfer admittance",
          "21": "short-circuit forward transfer admittance",
          "22": "short-circuit output admittance"},
    "h": {"11": "short-circuit input impedance",
          "12": "open-circuit reverse voltage ratio",
          "21": "short-circuit forward current gain",
          "22": "open-circuit output admittance"},
    "g": {"11": "open-circuit input admittance",
          "12": "short-circuit reverse current ratio",
          "21": "open-circuit forward voltage gain",
          "22": "short-circuit output impedance"},
    "a": {"11": "open-circuit voltage ratio",
          "12": "short-circuit transfer impedance",
          "21": "open-circuit transfer admittance",
          "22": "short-circuit current ratio"},
    "b": {"11": "open-circuit voltage ratio",
          "12": "short-circuit transfer impedance",
          "21": "open-circuit transfer admittance",
          "22": "short-circuit current ratio"},
}

_UNIT_LATEX = {"ohm": r"\Omega", "V": r"\mathrm{V}", "A": r"\mathrm{A}",
               "W": r"\mathrm{W}", "VA": r"\mathrm{VA}", "S": r"\mathrm{S}",
               "H": r"\mathrm{H}", "F": r"\mathrm{F}"}
_UNIT_PLAIN = {"ohm": "\u03a9"}   # plain text is UTF-8, so use the real symbol


#: Every unit _with_unit() can append. Written out rather than derived
#: from _UNIT_PLAIN, which only carries the ohm sign -- the rest of the
#: units pass through it unmapped. Longest first, so "VA" is matched
#: before "V" and no stray "A" is left behind.
_UNIT_SUFFIXES = ("VA", "ohm", "Ω", "Hz", "V", "A", "W", "S", "F", "H")


def _load_answers(ino, z, domain: str, use_rms: bool):
    """#292: what the th tool hands over for a load on its terminals.

    Version 8's th script, once it had the equivalent, asked *"If you are
    planning to analyze a load connected to this equivalent ... Interested?
    [y/n]"* and on yes defined three expressions in the variable `load`:

        irl = ino*req/(load+req)               current in the load
        vrl = load*ino*req/(load+req)          voltage drop in the load
        prl = load*ino^2*req^2/(load+req)^2    power consumed in the load

    and in AC and FD the same with zeq, the power taken as the real part
    of S = V I* -- called `prl` for RMS phasors and `aprl` (average) for
    peak ones, with the extra half. Those are the calculator's formulas
    as decoded from `v8_programs.txt`, not re-derived; the version 9 port
    had dropped the whole branch and the tutorial was made to work around
    it by typing `vth/(req+2)` by hand.

    Returns [(name, expr)], in the calculator's order. An unbounded Norton
    current (a source with nothing in series) has no load formulas: the
    current in the load is then vth/load, which the formulas above cannot
    express through ino, so the list is empty and the card shows nothing
    extra rather than three infinities."""
    import sympy as sp
    if ino in (sp.oo, -sp.oo, sp.zoo):
        return []
    load = sp.Symbol("load")
    if domain == "dc":
        irl = ino * z / (load + z)
        vrl = load * ino * z / (load + z)
        prl = load * ino**2 * z**2 / (load + z)**2
        return [("irl", sp.simplify(irl)), ("vrl", sp.simplify(vrl)),
                ("prl", sp.simplify(prl))]
    irl = ino * z / (load + z)
    vrl = ino * load * z / (load + z)
    s_load = (ino * sp.conjugate(ino) * load * z * sp.conjugate(z)
              / ((load + z) * (sp.conjugate(load) + sp.conjugate(z))))
    power = sp.re(s_load) if use_rms else sp.re(s_load) / 2
    return [("irl", sp.simplify(irl)), ("vrl", sp.simplify(vrl)),
            ("prl" if use_rms else "aprl", sp.simplify(power))]


def _without_unit(text: str) -> str:
    """An answer with its unit taken back off.

    Answers are stored formatted, so with "Show units" on, `vth` is the
    string "6 V" -- which sympify cannot read. That made a Thevenin result
    unusable in Evaluate and in Solve: `vth/(req+2)` failed with a syntax
    error unless the reader first went into Settings and turned units off.
    Rather than document that workaround in every Thevenin problem, take
    the unit off on the way back in.

    Only a trailing unit after a space is removed, so an expression that
    happens to end in a symbol called V is untouched.
    """
    stripped = (text or "").strip()
    for unit in _UNIT_SUFFIXES:
        if unit and stripped.endswith(" " + unit):
            return stripped[:-(len(unit) + 1)].strip()
    return stripped


def _with_unit(plain: str, latex: str, unit: str, show: bool):
    """Append a unit to a formatted answer. Skipped for expressions that
    still contain free symbols -- "r_b*vin/(r_a + r_b) V" would be
    wrong as often as right, since the symbols carry their own units."""
    if not show or not unit:
        return plain, latex
    return (f"{plain} {_UNIT_PLAIN.get(unit, unit)}",
            f"{latex}\\,{_UNIT_LATEX.get(unit, unit)}")


#: #175: the two halves of an "exact and approximate" answer, joined.
#: Exact first, the approximation after it in brackets -- Antony García's
#: suggestion, Roberto's choice of layout, 30 Aug 2026. An answer that
#: still has free symbols never gets here: there is nothing to
#: approximate, and the exact form is the whole answer.
def _sympify_row(sp, text):
    """An expert-mode equation or condition as SymPy, for typesetting.

    These arrive as the reader typed them ("re = 12'k", "is > 0"), so
    they go through the same shorthand expansion a circuit value gets.
    Anything that will not parse comes back as the original text, which
    `_tex` then declines to typeset -- the card falls back to plain.
    """
    try:
        from symbulator.si_prefix import expand_shorthand
        body = expand_shorthand(str(text))
    except Exception:
        body = str(text)
    for op, builder in (("<=", sp.Le), (">=", sp.Ge), ("=", sp.Eq),
                        ("<", sp.Lt), (">", sp.Gt)):
        if op in body:
            left, _, right = body.partition(op)
            try:
                return builder(sp.sympify(left.strip()),
                               sp.sympify(right.strip()), evaluate=False)
            except Exception:
                return text
    try:
        return sp.sympify(body)
    except Exception:
        return text


def _has_exact_form(sp, expr, polar: bool) -> bool:
    """Is there an exact rendering of `expr` worth showing beside the
    approximation? (#181)

    No, in two cases, and the mode then folds to plain approximate:

    - **Polar display.** `_polar_format` numericises. A phasor has no
      exact polar form, and asking for one with the rounding off yields
      2.00000000000000 angle 180.000000000000 -- which is *set* as the
      same 2.0 angle 180.0 the rounded half is. Two identical halves.
    - **The value already carries a Float.** An approximate input (2E3,
      or any answer built from one) was never exact to begin with; its
      "exact" half is only a longer decimal.
    """
    if polar:
        return False
    try:
        return not expr.atoms(sp.Float)
    except Exception:
        return True


def _is_whole(sp, expr) -> bool:
    """A whole number, real or complex -- nothing to round to. (#181)

    Distinct from `_has_exact_form`: here the exact form is real and is
    the *better* of the two, so the fold keeps it. A real whole number
    already folds on the string test, both halves printing "5"; a
    complex one does not, because the approximation writes 1.0j where
    the exact form writes 1j -- a bracket that tells the reader nothing,
    and the wrong half to keep if it were dropped the other way.
    """
    try:
        re_, im_ = expr.as_real_imag()
        return bool(re_.is_Integer and im_.is_Integer)
    except Exception:
        return False


def _join_dual(exact, approximate):
    """(plain, latex) for the pair, or one of them alone when the two
    would read the same.

    Both halves are compared, plain **and** LaTeX. The LaTeX is what the
    reader sees, and the two can disagree -- a polar phasor prints
    different plain text for the same rendered value, which is how #181
    reached Lesson 13 as "2.0 angle 180.0 A (= 2.0 angle 180.0 A)". An
    answer that is already 0.5 gains nothing from "0.5 (= 0.5)", and a
    whole number even less.
    """
    if approximate is None:
        return exact
    if approximate[0] == exact[0] or approximate[1] == exact[1]:
        return approximate
    return (f"{exact[0]}  (≈ {approximate[0]})",
            rf"{exact[1]}\;\;(\approx {approximate[1]})")


def _dualise(one, sp, expr, unit, digits, si, polar):
    """Format `expr` twice through `one` -- the caller's own formatter --
    once exactly and once to `digits` significant figures, and join them.

    `one` is fmt/fmt0 from solve_ui, whose flags are keyword parameters
    defaulting to the call's settings precisely so this can override
    them without a second copy of the formatting logic.
    """
    def exactly():
        return one(expr, unit, digits=0, si=False, approx=False, polar=polar)

    try:
        settled = sp.simplify(expr)
    except Exception:
        settled = expr
    if getattr(settled, "free_symbols", None):
        # Symbolic: the exact form is the whole answer, and there is
        # nothing to approximate.
        return exactly()
    try:
        approximate = one(expr, unit, digits=digits, si=si, approx=False,
                          polar=polar)
    except Exception:
        return exactly()
    if not _has_exact_form(sp, settled, polar):
        return approximate                                          # #181
    if _is_whole(sp, settled):
        return exactly()                                            # #181
    return _join_dual(exactly(), approximate)


# --------------------------------------------------------------------------
# Names that cannot be used, because their answers collide
# --------------------------------------------------------------------------
#
# An element called `s` produces the answer `is`, which is a Python keyword;
# such an input does not fail cleanly, it is read as something else.
#
# Derived, not listed. Until 26 Aug 2026 this was a hand-generated table of
# 129 element names and 6 node names, built by asking what Symbulator could
# produce as <quantity><element> for every name Python or SymPy owns. It was
# measured that day against the namespace the parser really has, and **none
# of the 129 was dangerous**:
#
#   * `_allowed_namespace()` exposes 35 curated names -- the trig set,
#     exp/log, Heaviside/DiracDelta, pr, re, im, a few constants -- and every
#     other identifier is already a plain Symbol. So `solve_poly_system`,
#     `prime_valuation` and 124 others were guarding a namespace that is not
#     there. SymPy's `sec` is not there either, which is why `ec` is a
#     perfectly good element name.
#   * Of the 35, the callable ones cannot be shadowed: `_alias_pattern` ends
#     with `(?!\s*\()`, so `re(x)` and `pr(6,3)` are never rewritten however
#     the circuit is named. Only a *bare* token is, and a bare function name
#     means nothing in an expression.
#
# That leaves names a keyword would produce. Deriving them keeps the guard
# honest: add a constant to the namespace and it is covered automatically,
# rather than by remembering to edit a list.
#
# The element name must also start with its kind letter, so most collisions
# cannot arise at all -- there is no element kind `i`, so `pi` is impossible
# and `i` is not banned.

_BANNED_CACHE = {}


def _collidable_names() -> dict:
    """{produced name: what owns it} -- names a bare token could shadow.

    A callable is safe: the alias pattern will not rewrite `name(`, and a
    bare function name is not an expression. A non-callable is not safe --
    `pi` used bare is a number, and silently meaning something else is
    exactly the failure this guards."""
    import keyword

    from symbulator.si_prefix import _allowed_namespace

    owned = {name: "a Python keyword" for name in keyword.kwlist}
    for name, obj in _allowed_namespace(True).items():
        if not callable(obj):
            owned.setdefault(name, "a name Symbulator reserves")
    return owned


def banned_element_names() -> dict:
    """{element name: (answer it would produce, what owns that)}."""
    if "elements" not in _BANNED_CACHE:
        out = {}
        for kind, quantities in _QUANTITIES_BY_KIND.items():
            # An empty tuple is a real answer: a mutual inductance reports
            # nothing, so it can produce no alias and ban no name. Do not
            # fall back to the defaults here.
            for quantity in quantities:
                for produced, owner in _collidable_names().items():
                    if not produced.startswith(quantity):
                        continue
                    name = produced[len(quantity):]
                    # The name has to be one a user could actually write:
                    # it starts with the kind letter.
                    if name and name.startswith(kind):
                        out[name] = (produced, owner)
        _BANNED_CACHE["elements"] = out
    return _BANNED_CACHE["elements"]


def banned_node_names() -> dict:
    """Same, for nodes, whose only answer is a voltage."""
    if "nodes" not in _BANNED_CACHE:
        out = {}
        for produced, owner in _collidable_names().items():
            for quantity in _NODE_QUANTITIES:
                if produced.startswith(quantity) and len(produced) > len(quantity):
                    out[produced[len(quantity):]] = (produced, owner)
        _BANNED_CACHE["nodes"] = out
    return _BANNED_CACHE["nodes"]


def banned_name_errors(elements) -> list:
    """Element or node names whose answers would collide with a Python or
    SymPy name. Returns a list of messages; empty means the circuit is fine."""
    from symbulator.elements import _IDENTIFIER_FIELD_IDX

    out, seen = [], set()
    for el in elements:
        banned = banned_element_names()
        if el.name in banned and el.name not in seen:
            seen.add(el.name)
            produces, owner = banned[el.name]
            out.append(msg(M_BAD_ELEMENT_NAME, name=el.name,
                           produces=produces, owner=owner,
                           suggestion=f"{el.name}1"))
        for idx in _IDENTIFIER_FIELD_IDX.get(el.kind, ()):
            if idx >= len(el.fields):
                continue
            node = el.fields[idx]
            banned_nodes = banned_node_names()
            if node in banned_nodes and node not in seen:
                seen.add(node)
                produces, owner = banned_nodes[node]
                out.append(msg(M_BAD_NODE_NAME, name=node,
                               produces=produces, owner=owner,
                               suggestion=f"{node}1"))
    return out


# --------------------------------------------------------------------------
# Answer names with and without the underscore
# --------------------------------------------------------------------------
#
# Symbulator 9 names its answers with an underscore between the quantity and
# the element: i_r1, v_2, p_e, r_e. Roberto's condition when that scheme was
# adopted was that the sans-underscore spelling a user naturally types --
# ir1, v2, pe, re -- must mean the same thing wherever it is given as input.
#
# It could not be done with a pattern, because the same token means different
# things in different circuits: `vx` is a free unknown unless the circuit has
# an element called x. So the alias list is built FROM the parsed circuit,
# and anything not on it is left alone. That is what keeps genuine unknowns
# working.
#
# Applied to values only -- never to a name or a node field. In `re,3,0,6`
# the name `re` must survive as the element's name; only what a value could
# refer to is rewritten.

# Every quantity the solver actually reports, per element kind. Measured by
# solving a probe circuit for each kind in both DC and AC and reading the
# keys back -- twice, because the first survey was wrong in both directions:
#
#   * it invented quantities (q, y) that do not exist, which would have put
#     phantom names like `ye` into the alias map and captured a user's own
#     unknown of that name;
#   * it dropped `ap` (apparent power) and `z` (impedance seen), which are
#     real -- they only appear in AC, and the first probe ran DC only;
#   * it assumed a mutual inductance reports something. It reports nothing.
#
# And two kinds report under a SUFFIXED name: a transformer called t answers
# as i_t2, a two-port called z1 as i_z12 and i_z13. Those are the element's
# ports, so the alias has to cover the suffixes as well as the bare name.
_NODE_QUANTITIES = ("v",)
_QUANTITIES_BY_KIND = {
    "r": ("ap", "i", "p", "s", "v"),
    "e": ("ap", "i", "p", "r", "s", "v", "z"),
    "j": ("ap", "i", "p", "r", "s", "v", "z"),
    "c": ("i", "p", "s", "v"),
    "l": ("i", "p", "s", "v"),
    "s": ("i",),                       # a short carries current, nothing else
    "o": ("ap", "i", "p", "s"),        # an op-amp reports no voltage
    "m": (),                           # mutual inductance reports nothing
    "t": ("i",),
    "z": ("i",), "y": ("i",), "h": ("i",),
    "g": ("i",), "a": ("i",), "b": ("i",),
}
# Kinds whose answers hang off numbered ports rather than the bare name.
_PORT_SUFFIXES = {"t": ("2",), "z": ("2", "3"), "y": ("2", "3"),
                  "h": ("2", "3"), "g": ("2", "3"), "a": ("2", "3"),
                  "b": ("2", "3")}
_DEFAULT_QUANTITIES = ("i", "p", "v")


def answer_aliases(elements) -> dict:
    """{sans-underscore name: underscored name} for one parsed circuit.

    Built from the circuit's own nodes and elements, so it contains exactly
    the names that could denote an answer here and nothing else."""
    from symbulator.elements import _IDENTIFIER_FIELD_IDX

    nodes, named = set(), []
    for el in elements:
        named.append((el.name, el.kind))
        for idx in _IDENTIFIER_FIELD_IDX.get(el.kind, ()):
            if idx < len(el.fields):
                nodes.add(el.fields[idx])

    alias = {}
    for node in nodes:
        for q in _NODE_QUANTITIES:
            alias[f"{q}{node}"] = f"{q}_{node}"
    for name, kind in named:
        targets = [name] + [name + sfx for sfx in _PORT_SUFFIXES.get(kind, ())]
        for q in _QUANTITIES_BY_KIND.get(kind, _DEFAULT_QUANTITIES):
            for target in targets:
                alias[f"{q}{target}"] = f"{q}_{target}"
    # A name that is already underscored is not an alias of anything.
    return {k: v for k, v in alias.items() if k != v}


def _alias_pattern(alias: dict):
    if not alias:
        return None
    # Longest first, so `ir10` wins over `ir1` when both exist.
    body = "|".join(re.escape(k) for k in sorted(alias, key=len, reverse=True))
    # Not preceded or followed by a name character, and not followed by "(" --
    # `pr(6,3)` is the parallel-resistor function, not the power in element r.
    #
    # The optional number in front is #94. Every calculator user writes
    # `.2v1` and `3ir1`, and the multiplication in those is implied: it is
    # made explicit further down the chain, in si_prefix. But this pattern
    # runs *before* that, so without the number here the lookbehind sees the
    # digit against the `v`, refuses to rewrite `v1` into `v_1`, and by the
    # time the `*` arrives `v1` is an ordinary symbol. The circuit then
    # solves -- in terms of a variable the reader thought was a node
    # voltage, with nothing anywhere saying so.
    #
    # Consumed rather than merely allowed, so the guard still holds inside a
    # name: in `x2v1` the number cannot start (there is an `x` before it) and
    # the name cannot start (there is a digit before it), so nothing matches,
    # which is right. Scientific notation is safe for a different reason --
    # an alias is a quantity letter plus an element name, and `e3` is not one.
    return re.compile(rf"(?<![\w.]) (\d*\.?\d*) ({body}) (?![\w])(?!\s*\()"
                      .replace(" ", ""))


def apply_answer_aliases(text: str, alias: dict) -> tuple:
    """Rewrite sans-underscore answer names in one value or expression.

    Returns (new_text, [names that were rewritten])."""
    pat = _alias_pattern(alias)
    if not pat or not text:
        return text, []
    used = []

    def sub(m):
        number, name = m.group(1), m.group(2)
        used.append(name)
        # Put the implied multiplication in while we are here. Leaving it to
        # si_prefix would work too, but only by accident of ordering, and
        # this way the rewritten text says what it means.
        return f"{number}*{alias[name]}" if number else alias[name]

    return pat.sub(sub, text), used


# A dependent source is *supposed* to be driven by another answer, so
# `jd,0,2,.2*v1` and `ed,2,3,2*ir1` are the expected thing and say nothing.
# What is worth a word is an answer used where one is not expected -- a
# resistor whose value is `re`, the equivalent resistance seen by source e.
# That is not wrong: it describes a resistor whose value tracks that
# equivalent. It is just unusual enough to be worth asking about.
_CONTROL_KINDS = ("e", "j")          # sources may be dependent
_CONTROL_QUANTITIES = ("v", "i")     # on a voltage or a current


def ambiguous_answer_names(elements, alias: dict) -> list:
    """Answer names used as values where that is *unexpected*.

    Roberto's rule, 24 Aug 2026: a source driven by a voltage or a current
    is an ordinary dependent source and needs no comment. Anything else --
    a passive element valued by an answer, or a source driven by a power or
    an equivalent resistance -- is unusual but legal, so warn and accept.

    The message says which reading was taken, names the circuit feature
    that caused it, and gives the escape."""
    from symbulator.elements import _IDENTIFIER_FIELD_IDX
    from symbulator.si_prefix import safe_sympify, expand_shorthand

    seen, out = set(), []
    for el in elements:
        ident = _IDENTIFIER_FIELD_IDX.get(el.kind, ())
        for idx in range(len(el.fields)):
            if idx in ident:
                continue
            raw = el.fields[idx]
            # Identifiers are read from the text, not from free_symbols.
            # SymPy owns some of these names as functions -- `re` is its
            # real-part function, so safe_sympify("re") comes back with no
            # free symbols at all and the clash Roberto described would go
            # unreported. The text is the honest source for "what did the
            # user write here".
            #
            # A name followed by "(" is a function CALL, never an answer
            # reference, and must not warn (#167). Three shapes reach
            # here that way: a typed pr(6,3), a resistor's [6,3] (which
            # the shorthand has already rewritten to pr(6,3)), and a
            # two-port's [p11,...] parameter term (same rewrite). On any
            # circuit with an element named `r`, all three used to trip
            # the warning -- `pr` spells p_r there -- which is how
            # Lesson 13's Example 19.2 earned a note about a `pr` the
            # user never wrote.
            symbols = set()
            for m in re.finditer(r"[A-Za-z_]\w*", raw):
                if raw[m.end():].lstrip().startswith("("):
                    continue
                symbols.add(m.group(0))
            for s in sorted(symbols & set(alias)):
                # An element valued by its own name is the documented
                # idiom ("it is not a problem that the symbolic value is
                # the same as the name" -- Lesson 2), not an unusual
                # answer reference: no note (#170, the transistor bias
                # model's emitter resistor `re1,e,0,re1`).
                if s.lower() == el.name:
                    continue
                quantity = s[0]
                if (el.kind in _CONTROL_KINDS
                        and quantity in _CONTROL_QUANTITIES):
                    continue          # an ordinary dependent source
                if s in seen:
                    continue
                seen.add(s)
                target = alias[s].split("_", 1)[1]
                what = _QUANTITY_WORDS.get(quantity, "answer")
                out.append(msg(M_LOOKS_LIKE_ANSWER, name=s, target=target,
                               what=what, element=el.name,
                               suggestion=f"{s}x"))
    return out


def prepare_inputs(desc: str, extra_equations=None, extra_unknowns=None,
                   extra_conditions=None, evaluate=None):
    """Translate every sans-underscore answer name in the user's inputs.

    Returns (desc, equations, unknowns, conditions, evaluate, notices).
    The description comes back with its values rewritten and its names and
    nodes untouched; the notices are the ambiguity warnings, if any."""
    from symbulator.elements import parse_circuit, _IDENTIFIER_FIELD_IDX

    try:
        elements = parse_circuit(desc, expand_si=False)
    except Exception:
        # Not parseable yet -- leave everything alone and let the real
        # validation report it.
        return (desc, extra_equations, extra_unknowns, extra_conditions,
                evaluate, [])

    alias = answer_aliases(elements)
    notices = ambiguous_answer_names(elements, alias)

    changed = False
    for el in elements:
        ident = _IDENTIFIER_FIELD_IDX.get(el.kind, ())
        for idx in range(len(el.fields)):
            if idx in ident:
                continue                        # a node or an element name
            new, used = apply_answer_aliases(el.fields[idx], alias)
            if used:
                el.fields[idx] = new
                changed = True
    if changed:
        desc = ":".join(e.name + "," + ",".join(e.fields) for e in elements)

    def each(items):
        if not items:
            return items
        if isinstance(items, str):
            return apply_answer_aliases(items, alias)[0]
        return [apply_answer_aliases(str(i), alias)[0] for i in items]

    return (desc, each(extra_equations), each(extra_unknowns),
            each(extra_conditions), each(evaluate), notices)


def solve_ui(desc: str, domain: str, omega: str, variables,
                  tool: str, n1: str, n2: str, kind: str,
                  extra_equations, extra_unknowns, extra_conditions,
                  digits: int = 0, si: bool = False,
                  units: bool = False, use_rms: bool = False,
                  approx: bool = False, polar: bool = False,
                  dual: bool = False):
    """Solve a full circuit, or run the th/er/port tools. Returns
    {"ok": True, ...} with the payload grouping answers into node voltages
    and per-element results (or, for the special tools, one block of named
    answers), or {"ok": False, "error": message} on failure. Every value
    in the payload is a plain string, so it can cross a subprocess pipe
    (as app.py does) or a Pyodide/JS boundary unchanged."""
    try:
        import sympy as sp
        from symbulator import ex, tr, th, er, port
        from symbulator.elements import parse_circuit

        # A phasor angle is an AC idea. In DC every answer is real,
        # and in FD/TR the answers are functions of s or t, so there
        # is nothing to take an angle of.
        polar = polar and domain == "ac"

        def fmt0(expr, unit="", *, digits=digits, si=si, approx=approx,
                 polar=polar):
            """Format one answer from a special tool (th/er/port) as a
            (plain-text, LaTeX) pair: SI-prefix notation first (if `si`),
            then forced-decimal (if `approx`), else the exact symbolic
            form -- with `unit` attached only when the answer is a pure
            number. Local to solve_ui() because it closes over this
            call's digits/si/approx/units flags; duplicated as `fmt`
            below (same job, different call site) rather than factored
            out, since the two evolved separately."""
            try:
                expr = sp.simplify(expr)
            except Exception:
                pass
            # A unit is only meaningful once the value is a pure number;
            # symbols in the expression carry their own units.
            has_syms = bool(getattr(expr, "free_symbols", None))
            show_unit = units and not has_syms
            # SymPy writes infinity "oo", which is a Python spelling
            # and not something to put in front of a reader. It
            # arrives here when th() finds a short-circuit current
            # without bound; every formatter below would pass it
            # through untouched.
            if expr in (sp.oo, -sp.oo, sp.zoo):
                sign = "-" if expr == -sp.oo else ""
                return _with_unit(sign + "∞", sign + r"\infty",
                                  unit, show_unit)
            if polar:
                shown = _polar_format(expr, digits, unit, si)
                if shown is not None:
                    return _with_unit(shown[0], shown[1], unit, show_unit)
            if si:
                shown = _si_format(expr, digits, unit if show_unit else "")
                if shown is not None:
                    return shown
            if approx and not digits:
                shown = _approx_format(expr)
                if shown is not None:
                    return _with_unit(shown[0], shown[1], unit, show_unit)
                expr = sp.N(expr)
            expr = _round_expr(expr, digits)
            return _with_unit(_plain_with_j(expr), _latex_with_j(expr),
                              unit, show_unit)

        # Sans-underscore answer names become the underscored ones the
        # package expects: `.2*v1` is node 1's voltage, not a free symbol
        # called v1, and without this it solved to a symbolic answer rather
        # than a number. Applied to values and to the expert-mode inputs;
        # names and nodes are read but never rewritten. Anything not an
        # answer in THIS circuit is left alone, which is what keeps a
        # genuine unknown like `vx` working.
        desc, extra_equations, extra_unknowns, extra_conditions, _, _alias_notes = \
            prepare_inputs(desc, extra_equations, extra_unknowns,
                           extra_conditions)

        # Complex values are meaningful only in AC; catch them before
        # solving so the message names the element rather than surfacing
        # as a strange answer.
        _guard_elements = parse_circuit(desc)
        _bad = _complex_value_error(_guard_elements, domain)
        if _bad:
            return _err(_bad)
        _notes = _hijack_notes(_guard_elements, reserve_imaginary=(domain == "ac"))
        _notes += _impulse_notes(_guard_elements, domain)
        _notes += _alias_notes          # "Is that what you meant by `re`?"

        # "Exact" rounding only skips the rounding step -- it can't make
        # an already-approximate input exact. If a decimal or
        # scientific-notation value is anywhere in the inputs (circuit
        # description, omega, or an expert-mode equation/condition),
        # switch exact to approximate so the answers get sensibly
        # formatted instead of dumped as raw, falsely-precise floats.
        approx_forced = False
        if digits == 0 and not approx:
            omega_text = omega if domain == "ac" else ""
            if _has_approx_value(desc, omega_text,
                                  *(extra_equations or ()),
                                  *(extra_conditions or ())):
                approx = True
                approx_forced = True
                _notes += _approx_value_notes(True)

        # Expert mode: let "ir5" mean "i_r5" the same way Evaluate and
        # the Solve panel already do, by rewriting equations/conditions
        # against this circuit's real symbol names before they're parsed.
        if extra_equations or extra_conditions:
            _canon = _circuit_canonical_names(_guard_elements)
            if extra_equations:
                extra_equations = [_normalize_underscore_names(e, _canon)
                                    for e in extra_equations]
            if extra_conditions:
                extra_conditions = [_normalize_underscore_names(c, _canon)
                                     for c in extra_conditions]

        # ---- Special tools: th / er / port ------------------------------
        if tool != "solve":
            tkw = {"domain": domain}
            if domain == "ac":
                tkw["omega"] = sp.sympify(omega)
                if use_rms:
                    tkw["use_rms"] = True
            # Expert mode applies here too. These tools run one or two
            # solves of their own, and the extras go into each of them --
            # the original barred expert mode from the equivalents, but
            # nothing in the physics asked for that. See th()'s docstring
            # for the one case where per-round is the wrong reading.
            if extra_equations:
                tkw["equations"] = extra_equations
            if extra_unknowns:
                tkw["unknowns"] = extra_unknowns
            if extra_conditions:
                tkw["conditions"] = extra_conditions
            named = []          # [(display key, expr)]
            load_named = []     # #292: the same, for a load on the port
            if tool == "th":
                eq = th(desc, n1, n2, **tkw)
                # th() sets this when the short-circuit round had to
                # be reasoned about rather than solved. Without it a
                # reader sees an equivalent resistance of 0 with
                # nothing to say where it came from. getattr because
                # the server variant takes symbulator from PyPI and
                # may briefly be a release behind this file.
                if getattr(eq, "note", ""):
                    _notes.append(eq.note)
                z_label = "req" if domain == "dc" else "zeq"
                named = [("vth", eq.vth), ("ino", eq.ino),
                         (z_label, eq.z), ("pmax", eq.pmax)]
                load_named = _load_answers(eq.ino, eq.z, domain, use_rms)
            elif tool == "er":
                z_label = "req" if domain == "dc" else "zeq"
                named = [(z_label, er(desc, n1, n2, **tkw))]
            elif tool == "port":
                pp = port(desc, n1, n2, kind, **tkw)
                named = [(f"{kind}{ij}", pp[ij])
                         for ij in ("11", "12", "21", "22")]
            answers, load_answers, flat = [], [], {}
            load_keys = {key for key, _ in load_named}
            for key, expr in named + load_named:
                unit = _TOOL_UNITS.get(key, _PORT_UNITS.get(kind, "")
                                       if tool == "port" else "")
                if tool == "port":
                    label = _PORT_LABELS.get(kind, {}).get(key[len(kind):], "")
                else:
                    label = _TOOL_LABELS.get(key, "")
                plain, latex = fmt0(expr, unit)
                shown = {"name": key, "label": label,
                         "plain": plain, "latex": latex}
                # #292: the load answers travel apart from the four, so
                # the page can show them only when the reader has said a
                # load is connected -- but they are in `values` either way,
                # so `irl` with `load = 2` works in Evaluate regardless.
                (load_answers if key in load_keys else answers).append(shown)
                # The raw expression, not the formatted string. `values` is
                # what the Evaluate and Solve cards substitute into and what
                # a downloaded file records, and the normal solve path below
                # fills it with str(expr) for exactly that reason. Storing
                # the display text here meant `req` arrived as "4.0 Ohm",
                # which the evaluator drops as unparseable -- so the
                # documented `vth/(req+6)` came back as `30.0/(req + 6)`
                # with units on, and carried the rounding error of the
                # display with units off.
                flat[key] = str(expr)
            return _ok({"nodes": [], "elements": [], "extras": answers,
                        "load_extras": load_answers,
                        "values": flat, "equations": [], "notes": _notes,
                        "approx": approx, "approx_forced": approx_forced})

        # ---- Normal circuit solve (dc/ac/fd/tr) -------------------------
        kwargs = {}
        if domain == "ac":
            kwargs["omega"] = sp.sympify(omega)
            if use_rms:
                kwargs["use_rms"] = True
        if domain == "tr" and variables:
            # Accept the same casual, underscore/case-insensitive typing
            # ("v2", "V_2", "IR1") that Evaluate, Solve and the Plot key
            # already do, and turn a request for an element's voltage drop
            # into the node voltages it is built from -- see
            # _wanted_solver_keys.
            #
            # Anything left over is refused here. tr() skips a key it does
            # not recognise without a word, so a name it cannot provide
            # used to produce an empty page: no answers, no error, nothing
            # to say why. That is what #110 was.
            wanted, unknown = _wanted_solver_keys(variables, _guard_elements)
            if unknown:
                shown = ", ".join(sorted(set(unknown)))
                return _err(msg(M_TR_CANNOT_LIMIT, names=shown))
            kwargs["variables"] = wanted
        if extra_equations:
            kwargs["equations"] = extra_equations
        if extra_unknowns:
            kwargs["unknowns"] = extra_unknowns
        if extra_conditions:
            kwargs["conditions"] = extra_conditions

        # ex() covers dc/ac/fd only -- the calculator's own prompt reads
        # "1:DC 2:AC 3:FD". Transient has always been a separate verb, so
        # call it directly.
        if domain == "tr":
            res = tr(desc, **kwargs)
        else:
            res = ex(desc, domain, **kwargs)
        values = res.values
        # 0.4.6 exposes every root; older solvers have only the one.
        solutions = list(getattr(res, "solutions", None) or [values])

        def fmt(expr, unit="", *, digits=digits, si=si, approx=approx,
                polar=polar):
            """Same formatting logic as `fmt0` above, for a normal
            dc/ac/fd/tr solve's node-voltage and element answers -- see
            `fmt0`'s docstring for why these two aren't merged into one
            function."""
            try:
                expr = sp.simplify(expr)
            except Exception:
                pass
            # A unit is only meaningful once the value is a pure number;
            # symbols in the expression carry their own units.
            has_syms = bool(getattr(expr, "free_symbols", None))
            show_unit = units and not has_syms
            # SymPy writes infinity "oo", which is a Python spelling
            # and not something to put in front of a reader. It
            # arrives here when th() finds a short-circuit current
            # without bound; every formatter below would pass it
            # through untouched.
            if expr in (sp.oo, -sp.oo, sp.zoo):
                sign = "-" if expr == -sp.oo else ""
                return _with_unit(sign + "∞", sign + r"\infty",
                                  unit, show_unit)
            if polar:
                shown = _polar_format(expr, digits, unit, si)
                if shown is not None:
                    return _with_unit(shown[0], shown[1], unit, show_unit)
            if si:
                shown = _si_format(expr, digits, unit if show_unit else "")
                if shown is not None:
                    return shown
            if approx and not digits:
                shown = _approx_format(expr)
                if shown is not None:
                    return _with_unit(shown[0], shown[1], unit, show_unit)
                expr = sp.N(expr)
            expr = _round_expr(expr, digits)
            return _with_unit(_plain_with_j(expr), _latex_with_j(expr),
                              unit, show_unit)

        # #175: "exact and approximate". Both formatters are wrapped
        # rather than rewritten -- each answer goes through the very same
        # code twice, once with the rounding off and once with it on, so
        # the two halves cannot drift apart in units, polar form or the
        # infinity spelling.
        if dual and digits:
            _fmt0_once, _fmt_once = fmt0, fmt

            def fmt0(expr, unit=""):                       # noqa: F811
                return _dualise(_fmt0_once, sp, expr, unit, digits, si, polar)

            def fmt(expr, unit=""):                        # noqa: F811
                return _dualise(_fmt_once, sp, expr, unit, digits, si, polar)

        elements = parse_circuit(desc)
        # Formatting one solution. An expert-mode equation on a power is
        # quadratic in its unknown, so a circuit can have more than one
        # answer -- both real, both satisfying every constraint. Rather than
        # pick one and present it as the answer, every solution is rendered
        # and the caller is told there is a choice. Only the values differ
        # between them; the equation system and the notes are shared.
        def render_solution(values):
            used = set()

            # ---- Tranche 1: node voltages, in order of first appearance ----
            node_order = []
            for el in elements:
                if el.kind == "m":
                    continue  # references inductor names, not nodes
                cand = [el.n1, el.n2]
                if el.kind == "o":
                    cand.append(el.fields[2])  # op-amp output node
                for n in cand:
                    if n != "0" and n not in node_order:
                        node_order.append(n)
            nodes = []
            for n in node_order:
                key = f"v_{n}"
                if key in values:
                    plain, latex = fmt(values[key], "V")
                    nodes.append({"node": n, "plain": plain, "latex": latex})
                    used.add(key)

            # ---- Tranche 2: one entry per element, in circuit order ----
            def node_v(n):
                """Node n's solved voltage, or the literal 0 for ground
                (which never gets its own v_0 entry in `values`). Used below
                to derive an element's branch voltage (v1 - v2) on the fly
                for older symbulator versions that didn't stamp a v_<name>
                answer directly."""
                if n == "0":
                    return sp.Integer(0)
                return values.get(f"v_{n}")

            element_cards = []
            from symbulator.elements import TWO_PORT_KINDS as _TP_KINDS
            for el in sorted(elements, key=lambda e: (_KIND_ORDER.get(e.kind, 99),
                                                      _natural_key(e.name))):
                items = []
                ikey = f"i_{el.name}"
                if ikey in values:
                    plain, latex = fmt(values[ikey], "A")
                    items.append({"sym": "i", "label": "current through",
                                  "plain": plain, "latex": latex})
                    used.add(ikey)
                # A two-port has no single branch current: it has one per
                # port, i_<name><node>, the current *entering the two-port*
                # at that node (the engine stamps it as leaving the node
                # into the block). They used to fall through to the
                # catch-all section and show under "Expert mode unknowns",
                # which they are not -- Roberto, 29 Aug 2026 (#168).
                if el.kind in _TP_KINDS:
                    for node in (el.n1, el.n2):
                        key = f"i_{el.name}{node}"
                        if key in values:
                            plain, latex = fmt(values[key], "A")
                            items.append({"sym": "i",
                                          "sub": f"{el.name}{node}",
                                          "label": f"current into port at node {node}",
                                          "plain": plain, "latex": latex})
                            used.add(key)
                # Voltage drop across the element: stored as v_<name> by
                # symbulator >= 0.2, else derived from the node voltages.
                if el.kind in "rlcejs":
                    vkey = f"v_{el.name}"
                    drop = values.get(vkey)
                    if drop is not None:
                        used.add(vkey)
                    else:
                        v1, v2 = node_v(el.n1), node_v(el.n2)
                        if v1 is not None and v2 is not None:
                            drop = v1 - v2
                    if drop is not None:
                        plain, latex = fmt(drop, "V")
                        items.append({"sym": "v", "label": "voltage drop",
                                      "plain": plain, "latex": latex})
                for pattern, symbol, label, unit in _ELEMENT_KEYS:
                    key = pattern.format(n=el.name)
                    if key in values:
                        plain, latex = fmt(values[key], unit)
                        items.append({"sym": symbol, "label": label,
                                      "plain": plain, "latex": latex})
                        used.add(key)
                if items:
                    element_cards.append({"name": el.name,
                                          "kind": _KIND_LABEL.get(el.kind, el.kind),
                                          "items": items})

            # ---- Safety net: anything solved but not claimed above ----
            # This is where Expert Mode's own unknowns come out, and they
            # used to come out bare: `v_r1` has a prefix to read a unit
            # from, but `vs` -- the unknown standing for a source's value
            # -- has none, so the whole Expert Mode block was the one
            # place in the Results that ignored "Show units" (Roberto,
            # 1 Sep 2026). `_value_units` reads the unit off the circuit
            # instead of off the name; the prefix rule stays for the
            # underscored keys it was written for.
            _EXTRA_UNITS = {"v": "V", "i": "A", "p": "W", "ap": "W",
                            "s": "VA", "z": "ohm", "r": "ohm"}
            by_value = _value_units(elements)
            extras = []
            for key in sorted(values.keys()):
                if key not in used:
                    prefix = key.split("_", 1)[0] if "_" in key else ""
                    unit = (_EXTRA_UNITS.get(prefix, "")
                            or by_value.get(_norm_varname(key), ""))
                    plain, latex = fmt(values[key], unit)
                    extras.append({"name": key, "plain": plain, "latex": latex})

            # ---- Flat name->expression map (for the evaluator + download).
            # Computed branch voltages are added under v_<element> (the TI
            # kept these as v<name>), unless that key already exists.
            flat = {k: str(v) for k, v in values.items()}
            for el in elements:
                if el.kind in "rlcejs":
                    key = f"v_{el.name}"
                    if key not in flat:
                        v1, v2 = node_v(el.n1), node_v(el.n2)
                        if v1 is not None and v2 is not None:
                            try:
                                flat[key] = str(sp.simplify(v1 - v2))
                            except Exception:
                                flat[key] = str(v1 - v2)
            return {"nodes": nodes, "elements": element_cards,
                    "extras": extras, "values": flat}


        # ---- The equation system the solver assembled.
        #
        # Two shapes of the same thing. `equations` is the flat list of
        # strings the Export Output card has always downloaded, unchanged.
        # `system` (#176) is the same content grouped and carrying a LaTeX
        # rendering of each line, for the Equations card to set with
        # MathJax -- Antony García's suggestion, 30 Aug 2026.
        #
        # A TR solve is transformed into the s-domain, solved there and
        # inverted, so what there is to show for it is that system, and
        # `domain_note` says so rather than letting it pass for the
        # time-domain equations a reader would assume -- Roberto's call.
        equations = []
        system = None
        try:
            from symbulator.engine import Circuit
            eq_domain = "fd" if domain == "tr" else domain
            # TR is solved as FD over a description whose sources have
            # already been moved into s -- see laplace.tr(), which stamps
            # _sources_to_s(desc), not desc. Stamping the raw description
            # here instead produced a system nobody solves: reactive
            # elements in s (s*v_2/1000000) beside a source still in t
            # (v_1 = 12*u(t)). The source's line is its own defining
            # equation, "the drop across it is its value", so it is
            # exactly the line that shows which domain the value is in.
            stamp_elements, stamp_extra_eqs, stamp_extra_conds = (
                elements, extra_equations, extra_conditions)
            if domain == "tr":
                from symbulator.laplace import (_sources_to_s,
                                                _relations_to_s)
                _s_desc = _sources_to_s(desc)
                stamp_elements = parse_circuit(_s_desc)
                # The extras cross with them: tr() transforms the added
                # equations and conditions too, so a listing that left
                # them in t would disagree with the system around them.
                stamp_extra_eqs = _relations_to_s(extra_equations, desc)
                stamp_extra_conds = _relations_to_s(extra_conditions, desc)
            circ = Circuit(stamp_elements, eq_domain,
                           omega=sp.sympify(omega) if domain == "ac" else None)
            circ.stamp_all()

            # SymPy sets Heaviside as theta(t). The app's own input
            # language, and the whole tutorial, call the unit step u(t),
            # and the EqSheet export already rewrites it that way (see
            # the `_u` substitution below) -- so the card does too rather
            # than showing the reader a symbol they were never taught.
            _u = sp.Function("u")

            def _as_u(obj):
                try:
                    return obj.replace(sp.Heaviside, lambda *a: _u(a[0]))
                except Exception:
                    return obj

            def _tex(obj):
                """LaTeX for one line, falling back to its plain text --
                the card renders whatever it is given, and a fallback that
                reads as text beats a card that fails to typeset."""
                try:
                    return sp.latex(_as_u(obj))
                except Exception:
                    return None

            eq_rows, known_rows, added_rows, cond_rows = [], [], [], []
            for eq in circ.equations:
                equations.append(f"{eq.lhs} = {eq.rhs}")
                shown = sp.Eq(_as_u(eq.lhs), _as_u(eq.rhs), evaluate=False)
                eq_rows.append({"plain": f"{shown.lhs} = {shown.rhs}",
                                "latex": _tex(shown)})
            for kname, kexpr in circ.known.items():
                equations.append(f"{kname} = {kexpr}")
                shown = sp.Eq(sp.Symbol(str(kname)), _as_u(kexpr),
                              evaluate=False)
                known_rows.append({"plain": f"{kname} = {shown.rhs}",
                                   "latex": _tex(shown)})
            for extra in (stamp_extra_eqs or []):
                equations.append(f"{extra}   (added)")
                added_rows.append({"plain": str(extra),
                                   "latex": _tex(_sympify_row(sp, extra))})
            for cond in (stamp_extra_conds or []):
                equations.append(f"{cond}   (condition)")
                cond_rows.append({"plain": str(cond),
                                  "latex": _tex(_sympify_row(sp, cond))})
            unk = [str(u) for u in circ.unknowns] + list(extra_unknowns or [])
            equations.append("unknowns: " + ", ".join(unk))

            note = ""
            if domain == "tr":
                note = ("Shown in the s-domain, which is where a transient "
                        "is actually solved: Symbulator transforms the "
                        "sources into s, solves the system below, and "
                        "inverts the answers back into time. A step typed "
                        "as 12*u(t) therefore appears here as 12/s.")
            system = {"equations": eq_rows, "known": known_rows,
                      "added": added_rows, "conditions": cond_rows,
                      "unknowns": unk, "domain": domain,
                      "domain_note": note}
        except Exception:
            pass  # equations are a bonus; never fail the solve over them

        # ---- The EqSheet import payload (the "What if..." button).
        # Same contract as tools/eqsheet_export.py, the reference
        # implementation: mode + equations rendered with sp.sstr + every
        # numeric result (real mode as a number, complex as [re, im]);
        # symbolic results are skipped. All four domains cross now
        # (#124), each in the shape that survives the trip:
        #
        # - dc, and ac with a numeric omega: the stamped system, as
        #   before. (A symbolic omega would import equations with no
        #   values -- handover caveat 3 -- so that one still stays home.)
        # - fd: the stamped system too -- it is algebraic in s -- in
        #   complex mode, with `s` crossing as a Known complex variable
        #   (j by default) the reader moves around the plane.
        # - tr: the system is differential and cannot cross, so the
        #   *answers* do instead, one equation per solved expression,
        #   with `t` as a Known real variable starting at 0. Flip an
        #   answer Known and t Unknown and the sheet finds *when* the
        #   waveform gets there. An answer containing delta(t) has no
        #   numeric value at all and is left out by name, in a comment
        #   the sheet displays but does not parse.
        #
        # Absent, the interface hides the button.
        #
        # The Circuit stamped above has no expert-mode extras in it (they
        # are joined inside the solve), so extras and conditions are
        # appended by hand -- through the solver's own shorthand
        # expansion, since the reader may have typed 2'k or ^ in them and
        # EqSheet's parser reads plain SymPy.
        eqsheet = None
        try:
            if tool == "solve" and (
                    domain != "ac" or sp.sympify(omega).is_number):
                from symbulator.si_prefix import expand_shorthand
                # The Numerical Solver shows its variables sans
                # underscore -- Roberto's call, 27 Aug 2026 -- so the
                # payload strips them from every Symbulator-defined name:
                # v_1 becomes v1, i_r1 becomes ir1, in the equations and
                # in the result keys alike. The rename map is collected
                # from the exported expressions' own symbols, then applied
                # to the expert extras as plain text with the longest
                # names first, so i_r12 cannot be half-eaten by i_r1.
                if domain in ("dc", "ac", "fd"):
                    _rename = {}
                    for _eq in circ.equations:
                        for _sym in (_eq.lhs.free_symbols
                                     | _eq.rhs.free_symbols):
                            _n = str(_sym)
                            if "_" in _n:
                                _rename[_sym] = sp.Symbol(_n.replace("_", ""))
                    eq_strings = [
                        f"{sp.sstr(_eq.lhs.subs(_rename))} = "
                        f"{sp.sstr(_eq.rhs.subs(_rename))}"
                        for _eq in circ.equations]
                    _text_renames = sorted(
                        ((str(k), str(v)) for k, v in _rename.items()),
                        key=lambda kv: -len(kv[0]))
                    # Expert equations only. Conditions used to ride along
                    # here and arrived in the sheet's List of Equations as
                    # parse errors -- `is > 0` is not an equation, and the
                    # sheet demands exactly one `=` per line. Roberto
                    # caught it on the monograph's showcase circuit,
                    # 28 Aug 2026: conditions are a filter on the solve
                    # that produced this payload, not part of the system.
                    #
                    # And the equations cross *rewritten*: a third-degree
                    # name in them (a power p_jd1, a branch voltage, a
                    # source's r_e) is expanded into the system's own
                    # first/second-degree variables, the way the original
                    # calculator evaluated expert extras in CAS space
                    # (Roberto, same day, same circuit). Without this,
                    # `p_jd1 = -80` lands on the sheet as a brand-new free
                    # variable pinned to -80 by a trivial equation --
                    # nothing ties it to the circuit. The expansion is the
                    # solver's own: _derived_definition, the same formulas
                    # the expert solve stamps in, applied recursively
                    # because a power's defining v and i may themselves be
                    # derived names.
                    from symbulator.engine import (
                        _parse_extra_equation, _canonicalize,
                        _derived_definition)

                    def _expand_derived(_expr):
                        for _ in range(8):
                            _subs = {}
                            for _s in _expr.free_symbols:
                                try:
                                    _d = _derived_definition(
                                        circ, str(_s), domain)
                                except Exception:
                                    _d = None
                                if _d is None:
                                    continue
                                _deq, _dsym = _d
                                if _deq.lhs == _dsym:
                                    _subs[_s] = _deq.rhs
                                else:
                                    # the r_/z_ form: sym * (-i) = vdiff
                                    _sol = sp.solve(_deq, _dsym)
                                    if _sol:
                                        _subs[_s] = _sol[0]
                            if not _subs:
                                return _expr
                            _expr = _expr.subs(_subs)
                        return _expr

                    for extra in list(extra_equations or []):
                        try:
                            _eq = _parse_extra_equation(
                                extra, reserve_imaginary=(domain == "ac"))
                            _eq = _canonicalize(_eq, circ.alias_map)
                            _lhs = _expand_derived(_eq.lhs).subs(_rename)
                            _rhs = _expand_derived(_eq.rhs).subs(_rename)
                            # any surviving underscored name (a new expert
                            # unknown, say) still drops its underscore
                            for _s in (_lhs.free_symbols
                                       | _rhs.free_symbols):
                                _n = str(_s)
                                if "_" in _n:
                                    _m = {_s: sp.Symbol(_n.replace("_", ""))}
                                    _lhs = _lhs.subs(_m)
                                    _rhs = _rhs.subs(_m)
                            eq_strings.append(
                                f"{sp.sstr(_lhs)} = {sp.sstr(_rhs)}")
                        except Exception:
                            # fall back to the old textual rendering
                            try:
                                _txt = expand_shorthand(extra, si=True)
                            except Exception:
                                _txt = extra
                            for _old, _new in _text_renames:
                                _txt = re.sub(
                                    rf"\b{re.escape(_old)}\b", _new, _txt)
                            eq_strings.append(_txt)
                    _complex_mode = domain in ("ac", "fd")
                    results = {}
                    for _name, _value in values.items():
                        try:
                            z = complex(_value)
                        except (TypeError, ValueError):
                            continue      # symbolic -- not a what-if Known
                        results[_name.replace("_", "")] = \
                            [z.real, z.imag] if _complex_mode else z.real
                    eqsheet = {"mode": "ac" if _complex_mode else "dc",
                               "equations": eq_strings, "results": results}
                    if domain == "fd":
                        eqsheet["known"] = {"s": [0.0, 1.0]}
                else:                     # tr -- the answers cross instead
                    _u = sp.Function("u")
                    _rename = {}
                    for _name, _expr in values.items():
                        for _sym in getattr(_expr, "free_symbols", ()):
                            _n = str(_sym)
                            if "_" in _n:
                                _rename[_sym] = sp.Symbol(_n.replace("_", ""))
                    eq_strings, _skipped = [], []
                    for _name, _expr in sorted(values.items()):
                        _e = sp.sympify(_expr)
                        if _e.has(sp.DiracDelta):
                            _skipped.append(_name.replace("_", ""))
                            continue
                        _e = _e.subs(_rename).replace(
                            sp.Heaviside, lambda *a: _u(a[0]))
                        eq_strings.append(
                            f"{_name.replace('_', '')} = {sp.sstr(_e)}")
                    if _skipped:
                        eq_strings.append(
                            "# left out (their answers contain delta(t), "
                            "which has no numeric value): "
                            + ", ".join(_skipped))
                    _exported = len(eq_strings) - (1 if _skipped else 0)
                    if _exported > 0:
                        eqsheet = {"mode": "dc", "equations": eq_strings,
                                   "results": {}, "known": {"t": 0.0}}
        except Exception:
            pass  # the payload is a bonus too; never fail the solve

        # Every solution is rendered, ranked as the solver ranked them --
        # the first is the one to show by default. The top-level nodes /
        # elements / extras / values stay as that first solution so callers
        # that predate this, and everything downstream that reads a single
        # answer, are unaffected.
        rendered = [render_solution(v) for v in solutions]
        first = rendered[0]
        return _ok({"nodes": first["nodes"], "elements": first["elements"],
                    "extras": first["extras"], "values": first["values"],
                    "solutions": rendered,
                    "equations": equations, "eqsheet": eqsheet,
                    "system": system,
                    "notes": _notes,
                    "approx": approx, "approx_forced": approx_forced})
    except Exception as exc:  # noqa: BLE001 -- anything goes back as text
        return _err(_exc_msg(exc))


def _norm_name(name: str) -> str:
    """Key used to match a name the user typed against a solved answer:
    case-insensitive and underscore-optional, so `i_r1`, `i_R1`, `iR1`
    and `IR1` all collapse to the same key."""
    return name.replace("_", "").lower()


_IDENT_TOKEN = re.compile(r"[A-Za-z_]\w*")


def _circuit_canonical_names(elements):
    """Every name a solved circuit can produce: `i_<name>` for each
    element's current (every kind gets one), `v_<name>` for the branch
    voltage of "rlcejs"-kind elements (matching the same test used when
    building the flat results map), and `v_<node>` for every non-ground
    node. Used to translate an
    expert-mode equation/condition written the calculator's casual way
    ("ir5") back to the real symbol ("i_r5") before it reaches the
    solver -- see _normalize_underscore_names below."""
    names = set()
    for el in elements:
        names.add(f"i_{el.name}")
        if el.kind in "rlcejs":
            names.add(f"v_{el.name}")
        for n in (getattr(el, "n1", None), getattr(el, "n2", None)):
            if n and n != "0":
                names.add(f"v_{n}")
    return names


def _normalize_underscore_names(text, canonical):
    """Rewrite identifiers in `text` that match a canonical circuit
    symbol once case/underscores are ignored (_norm_name) to that
    symbol's real, underscored spelling -- e.g. "ir5" -> "i_r5" -- so an
    expert-mode "Add equations"/"Add conditions" line can refer to a
    circuit quantity the same casual way Evaluate and the Solve panel
    already accept (both already alias-match through _alias_mapping;
    this is the equivalent for text that gets parsed *before* any
    circuit values exist to alias against). A name with no canonical
    match -- a genuinely new symbol like an unknown resistor's value --
    passes through untouched."""
    by_norm = {}
    for n in canonical:
        by_norm.setdefault(_norm_name(n), n)

    def repl(m):
        tok = m.group(0)
        return by_norm.get(_norm_name(tok), tok)

    return _IDENT_TOKEN.sub(repl, text)


MAX_PLOT_POINTS = 2000


def _wanted_solver_keys(names, elements):
    """(keys to ask tr() for, names it cannot provide).

    A transient solve answers in element currents and node voltages, and
    nothing else -- an element's voltage drop is derived from its two node
    voltages once the transforms are done, and there are no powers in TR at
    all. So a reader asking to be shown `v_r3` is really asking for the two
    nodes r3 spans, and this says so: inverse Laplace is linear, so
    transforming those and subtracting afterwards gives the same answer for
    the same work.

    Ground is dropped rather than requested. `v_0` is the constant zero and
    the solver never carries a symbol for it, so asking would put this
    straight back into the business of requesting names that do not exist.
    """
    solver_names = set()
    drop_for = {}
    for el in elements:
        solver_names.add(f"i_{el.name}")
        for n in (getattr(el, "n1", None), getattr(el, "n2", None)):
            if n and n != "0":
                solver_names.add(f"v_{n}")
        if el.kind in "rlcejs":
            nodes = [n for n in (getattr(el, "n1", None),
                                 getattr(el, "n2", None))
                     if n and n != "0"]
            drop_for[f"v_{el.name}"] = [f"v_{n}" for n in nodes]

    keys, unknown = [], []
    for raw in names:
        name = _resolve_name(raw, elements)
        if name in solver_names:
            wanted = [name]
        elif name in drop_for:
            wanted = drop_for[name]
        else:
            unknown.append(raw)
            continue
        for k in wanted:
            if k not in keys:
                keys.append(k)
    return keys, unknown


def _resolve_name(key: str, elements) -> str:
    """Match a casually-typed circuit quantity ("vx", "ir5") to its real
    solved name ("v_x", "i_r5"), the same underscore/case-insensitive way
    expert-mode equations do (see _normalize_underscore_names) -- used for
    a plot's variable key and for the "limit results to..." variables
    list. Falls back to the typed name unchanged if nothing matches, so
    the caller still gets a clear "not found" error instead of a silent
    substitution."""
    canonical = _circuit_canonical_names(elements)
    by_norm = {_norm_name(n): n for n in canonical}
    return by_norm.get(_norm_name(key), key)


def _voltage_drop_nodes(name: str, elements):
    """The two node-voltage keys an element's voltage drop is built from,
    or None if `name` is not one.

    Ground is returned as None rather than as `v_0`: the solver carries no
    symbol for it, and its voltage is nothing.
    """
    for el in elements:
        if name != f"v_{el.name}" or el.kind not in "rlcejs":
            continue
        n1, n2 = getattr(el, "n1", None), getattr(el, "n2", None)
        return (f"v_{n1}" if n1 and n1 != "0" else None,
                f"v_{n2}" if n2 and n2 != "0" else None)
    return None


def _drop_expression(values: dict, pair, name: str, free_of):
    """values[n1] - values[n2] for a derived voltage drop, checked.

    Raises the same PlotError the solver would, so a missing transform or a
    leftover unknown reads the same way whichever route the plot took.
    """
    from symbulator.plotting import PlotError
    import sympy as sp

    parts = []
    for key in pair:
        if key is None:
            parts.append(sp.Integer(0))
            continue
        if key not in values:
            raise PlotError(
                f"'{name}' needs {key}, which could not be transformed -- "
                f"its inverse Laplace transform may have no closed form.")
        parts.append(values[key])
    expr = sp.simplify(parts[0] - parts[1])
    free = expr.free_symbols - {free_of}
    if free:
        names = ", ".join(sorted(str(x) for x in free))
        raise PlotError(
            f"'{name}' still depends on {names}, which has no numeric value "
            f"-- pin it with a condition before plotting.")
    return expr


def plot_time_ui(desc: str, key: str, t_min: float, t_max: float, n: int,
                 extra_equations, extra_unknowns, extra_conditions):
    """Sample a circuit's transient (tr()) response for `key` over
    `[t_min, t_max]`, for the "Plot vs time" tool. Returns
    {"ok": True, "t": [...], "y": [...], "key": "<resolved name>"} --
    plain lists of floats, ready for a chart -- or {"ok": False,
    "error": ...}. Every value in the payload crosses a subprocess pipe
    (app.py) or a Pyodide/JS boundary unchanged, same contract as
    solve_ui."""
    try:
        from symbulator.elements import parse_circuit
        from symbulator.plotting import time_samples, PlotError

        elements = parse_circuit(desc)
        # Plot vs time runs tr() under the hood, which is never AC.
        _notes = _hijack_notes(elements, reserve_imaginary=False)

        if extra_equations or extra_conditions:
            _canon = _circuit_canonical_names(elements)
            if extra_equations:
                extra_equations = [_normalize_underscore_names(e, _canon)
                                    for e in extra_equations]
            if extra_conditions:
                extra_conditions = [_normalize_underscore_names(c, _canon)
                                     for c in extra_conditions]
        resolved = _resolve_name(key, elements)

        pair = _voltage_drop_nodes(resolved, elements)
        if pair is None:
            t_values, y_values = time_samples(
                desc, resolved, t_max=t_max, t_min=t_min, n=n,
                equations=extra_equations or None,
                unknowns=extra_unknowns or None,
                conditions=extra_conditions or None)
        else:
            # An element's voltage drop: transform the nodes it spans and
            # subtract. See _drop_expression.
            import numpy as np
            import sympy as sp
            from symbulator import tr as _tr
            from symbulator.laplace import T as _T

            wanted = [k for k in pair if k]
            got = _tr(desc, variables=wanted,
                      equations=extra_equations or None,
                      unknowns=extra_unknowns or None,
                      conditions=extra_conditions or None)
            expr = _drop_expression(got.values, pair, resolved, _T)
            fn = sp.lambdify(_T, expr, modules=["numpy"])
            t_arr = np.linspace(t_min, t_max, n)
            y_raw = np.asarray(fn(t_arr), dtype=complex)
            y_arr = np.real(np.broadcast_to(y_raw, t_arr.shape))
            t_values, y_values = t_arr.tolist(), y_arr.tolist()
        return _ok({"t": t_values, "y": y_values, "key": resolved, "notes": _notes})
    except PlotError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))


def bode_ui(desc: str, key: str, f_min: float, f_max: float, n: int,
           extra_equations, extra_unknowns, extra_conditions):
    """Sample a circuit's s-domain (fd()) response for `key` across a
    frequency sweep from `f_min` to `f_max` Hz, for the "Bode plot"
    tool. Returns {"ok": True, "freq": [...], "mag_db": [...],
    "phase_deg": [...], "key": "<resolved name>"}, or {"ok": False,
    "error": ...} -- same plain-list, cross-boundary contract as
    plot_time_ui."""
    try:
        from symbulator.elements import parse_circuit
        from symbulator.plotting import bode_samples, PlotError

        elements = parse_circuit(desc)
        # Bode plot runs fd() under the hood, which is never AC.
        _notes = _hijack_notes(elements, reserve_imaginary=False)

        if extra_equations or extra_conditions:
            _canon = _circuit_canonical_names(elements)
            if extra_equations:
                extra_equations = [_normalize_underscore_names(e, _canon)
                                    for e in extra_equations]
            if extra_conditions:
                extra_conditions = [_normalize_underscore_names(c, _canon)
                                     for c in extra_conditions]
        resolved = _resolve_name(key, elements)

        pair = _voltage_drop_nodes(resolved, elements)
        if pair is None:
            freq_values, mag_db, phase_deg = bode_samples(
                desc, resolved, f_min=f_min, f_max=f_max, n=n,
                equations=extra_equations or None,
                unknowns=extra_unknowns or None,
                conditions=extra_conditions or None)
        else:
            # The same expansion as the time plot, one domain over.
            import numpy as np
            import sympy as sp
            from symbulator import fd as _fd
            from symbulator.laplace import S as _S

            got = _fd(desc, equations=extra_equations or None,
                      unknowns=extra_unknowns or None,
                      conditions=extra_conditions or None)
            expr = _drop_expression(got.values, pair, resolved, _S)
            fn = sp.lambdify(_S, expr, modules=["numpy"])
            freq_arr = np.logspace(np.log10(f_min), np.log10(f_max), n)
            h_raw = fn(1j * 2 * np.pi * freq_arr)
            h = np.broadcast_to(np.asarray(h_raw, dtype=complex),
                                freq_arr.shape)
            with np.errstate(divide="ignore"):
                mag_db = (20 * np.log10(np.abs(h))).tolist()
            phase_deg = np.angle(h, deg=True).tolist()
            freq_values = freq_arr.tolist()
        return _ok({"freq": freq_values, "mag_db": mag_db, "phase_deg": phase_deg,
                    "key": resolved, "notes": _notes})
    except PlotError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))


def _chart_safe(values) -> list:
    """Floats for a chart payload, with anything non-finite as None.

    A sweep can cross a pole and a magnitude can hit true zero, and both
    produce values (inf, nan) that are not JSON -- Flask would emit
    `Infinity` and the page's JSON.parse would throw, reading as "could
    not reach the solver". The chart renderer skips a null point, which
    is the honest picture: there is no value there to draw."""
    import math
    return [v if math.isfinite(v) else None for v in values]


def bode_tf_ui(expr_str: str, f_min: float, f_max: float, n: int):
    """Sample a transfer function typed directly -- H(s), no circuit --
    across a frequency sweep from `f_min` to `f_max` Hz (s = j*2*pi*f),
    for the Bode tool's transfer-function mode (#123). Lesson 11's
    practice problems hand the reader H(s) with no circuit behind it,
    which is exactly the case the circuit-variable Bode cannot serve.

    The expression goes through the same shorthand a circuit value gets
    (`^`, implied multiplication, `2'k`), must be numeric apart from
    `s`, and returns the same payload shape as bode_ui, with "H(s)" as
    the chart key."""
    try:
        import numpy as np
        import sympy as sp

        parsed = _parse_with_rearrangers(expand_value_for_ui(expr_str))
        strays = sorted(str(x) for x in parsed.free_symbols if str(x) != "s")
        if strays:
            return _err(msg(M_TF_NOT_NUMERIC, strays=", ".join(strays)))
        s_syms = [x for x in parsed.free_symbols if str(x) == "s"]
        s_sym = s_syms[0] if s_syms else sp.Symbol("s")
        fn = sp.lambdify(s_sym, parsed, modules=["numpy"])
        freq_arr = np.logspace(np.log10(f_min), np.log10(f_max), n)
        h_raw = fn(1j * 2 * np.pi * freq_arr)
        h = np.broadcast_to(np.asarray(h_raw, dtype=complex), freq_arr.shape)
        with np.errstate(divide="ignore", invalid="ignore"):
            mag_db = _chart_safe((20 * np.log10(np.abs(h))).tolist())
        phase_deg = _chart_safe(np.angle(h, deg=True).tolist())
        return _ok({"freq": freq_arr.tolist(), "mag_db": mag_db,
                    "phase_deg": phase_deg, "key": "H(s)", "notes": []})
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))


def sweep_ui(desc: str, key: str, xname: str, x_min: float, x_max: float,
             n: int, extra_equations, extra_unknowns, extra_conditions):
    """Sample one solved answer against one symbolic value -- `v1` as
    `rx` runs from 1 to 10k -- for the "Plot against a variable" tool
    (#123). The circuit is solved **once, in DC**, with `xname` left
    symbolic; the answer is lambdified over it and sampled linearly.
    Returns {"ok": True, "x": [...], "y": [...], "key": ..., "xname":
    ...} -- same cross-boundary contract as the other plot tools."""
    try:
        import numpy as np
        import sympy as sp
        from symbulator import dc as _dc
        from symbulator.elements import parse_circuit
        from symbulator.plotting import PlotError

        if x_max <= x_min:
            return _err(msg(M_SWEEP_RANGE))
        elements = parse_circuit(desc)
        # DC under the hood, so i/j are ordinary names here, as in tr().
        _notes = _hijack_notes(elements, reserve_imaginary=False)

        if extra_equations or extra_conditions:
            _canon = _circuit_canonical_names(elements)
            if extra_equations:
                extra_equations = [_normalize_underscore_names(e, _canon)
                                    for e in extra_equations]
            if extra_conditions:
                extra_conditions = [_normalize_underscore_names(c, _canon)
                                     for c in extra_conditions]
        resolved = _resolve_name(key, elements)

        got = _dc(desc, equations=extra_equations or None,
                  unknowns=extra_unknowns or None,
                  conditions=extra_conditions or None)
        pair = _voltage_drop_nodes(resolved, elements)
        if pair is None:
            if resolved not in got.values:
                return _err(msg(M_NOT_IN_DC, name=resolved))
            expr = got.values[resolved]
        else:
            # An element's voltage drop: subtract the node voltages it
            # spans, same expansion as the other plot tools -- but the
            # leftover-symbol check is ours, against the sweep variable.
            parts = []
            for k in pair:
                if k is None:
                    parts.append(sp.Integer(0))
                elif k not in got.values:
                    return _err(msg(M_NEEDS_MISSING, name=resolved, missing=k))
                else:
                    parts.append(got.values[k])
            expr = sp.simplify(parts[0] - parts[1])

        xsyms = [x for x in expr.free_symbols if str(x) == xname]
        strays = sorted(str(x) for x in expr.free_symbols if str(x) != xname)
        if strays:
            return _err(msg(M_STILL_DEPENDS, name=resolved,
                            strays=", ".join(strays),
                            example=f"{strays[0]} = 1'k"))
        if not xsyms:
            _notes = list(_notes) + [
                f"'{resolved}' does not depend on '{xname}', so the line is "
                f"flat. If {xname} names an element, give that element a "
                f"symbolic value and sweep that value instead."]
        x_sym = xsyms[0] if xsyms else sp.Symbol(xname)
        fn = sp.lambdify(x_sym, expr, modules=["numpy"])
        x_arr = np.linspace(x_min, x_max, n)
        with np.errstate(divide="ignore", invalid="ignore"):
            y_raw = fn(x_arr)
            y_arr = np.real(np.broadcast_to(
                np.asarray(y_raw, dtype=complex), x_arr.shape))
        return _ok({"x": x_arr.tolist(), "y": _chart_safe(y_arr.tolist()),
                    "key": resolved, "xname": xname, "notes": _notes})
    except PlotError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))


def _aliases_from_values(values: dict) -> dict:
    """{sans-underscore spelling: the name the answers actually use}.

    Expert mode builds this from the circuit's elements, before anything is
    solved. The Solve card is handed the answers instead and never had one,
    so its equations went to SymPy as typed -- and `re` and `im` are SymPy's
    real- and imaginary-part functions, so `re = 12000` died with
    "SympifyError: re" while the identical line worked in expert mode. The
    documentation instructs exactly that line, which is how it was found.

    A spelling that two different answers would share is dropped rather than
    guessed at."""
    out, ambiguous = {}, set()
    for key in values:
        short = key.replace("_", "")
        if short == key:
            continue
        if short in out and out[short] != key:
            ambiguous.add(short)
        out[short] = key
    for short in ambiguous:
        out.pop(short, None)
    return out


#: Functions that rearrange an expression rather than compute a new
#: quantity from it. They have to be applied *after* the circuit's answers
#: are substituted in, or they act on a bare symbol and vanish -- see
#: _parse_with_rearrangers. `integrate` is deliberately not here: it can
#: run unboundedly long on an expression a solver produced.
_REARRANGERS = ("expand", "factor", "simplify", "cancel", "together",
                "apart", "collect", "powsimp", "radsimp", "trigsimp",
                "logcombine", "diff")


def _parse_with_rearrangers(text: str, reserve_imaginary: bool = True):
    """safe_sympify, but with the rearranging functions left unapplied.

    Each is bound to an undefined function of the same name, so
    `expand(vo)` parses as a call carrying `vo` rather than collapsing to
    `vo` on the spot. `_apply_rearrangers` turns them into the real thing
    once the answers are in.
    """
    import sympy as sp
    from symbulator.si_prefix import (_allowed_namespace, _IDENT_RE,
                                      check_expression_syntax)

    check_expression_syntax(text)
    ns = _allowed_namespace(reserve_imaginary)
    used = set(_IDENT_RE.findall(text))
    for name in _REARRANGERS:
        if name in used:
            ns[name] = sp.Function(name)
    for name in used:
        ns.setdefault(name, sp.Symbol(name))
    return sp.sympify(text, locals=ns)


def _apply_rearrangers(expr):
    """(expression, whether one of them actually rearranged anything).

    A name that is not a real SymPy function is left as it is rather than
    raising: it will surface as an unresolved symbol in the answer, which
    is a clearer thing for a reader to see than a traceback.

    The flag is about the *arrangement*, not about whether the call was
    resolved. Getting that wrong made a rearranger that does nothing --
    `powsimp` on an answer with no powers to gather -- look actively
    harmful: it counted as "ran", the display's simplify was skipped on
    its account, and the reader saw the raw stored form. `vo` showed
    -r2/r1 while `powsimp(vo)` showed (-r2*r3 - r2*r4)/(r1*(r3 + r4)),
    which is the same number and a worse answer.
    """
    import sympy as sp
    if not getattr(expr, "replace", None):
        return expr, False
    changed = False

    def bind(name, fn):
        def run(*args):
            nonlocal changed
            try:
                out = fn(*args)
            except Exception as exc:
                # Say which function declined and why, rather than leaving
                # the call unevaluated -- `apart` on an expression with
                # several symbols echoed back "apart(-r2/r1)", which reads
                # like the card ignored what was typed.
                raise ValueError(
                    f"{name}() could not be applied to this answer: {exc}"
                ) from exc
            if args and out != args[0]:
                changed = True
            return out
        return run

    for name in _REARRANGERS:
        fn = getattr(sp, name, None)
        if fn is not None:
            expr = expr.replace(sp.Function(name), bind(name, fn))
    return expr, changed


def _parse_answer(vstr: str):
    """Read a solved answer back from its printed form.

    Bare `sp.sympify` reads it against the whole of SymPy, which means any
    answer carrying a symbol that shares a name with a SymPy function comes
    back as that function. `rf` is the one that found this -- a natural
    name for a feedback resistor, and also the rising factorial -- so
    Lesson 5's op-amp answers turned into "bad operand type for unary -:
    'FunctionClass'" the moment Evaluate touched them. `im`, `beta`,
    `gamma`, `zeta`, `N`, `S`, `O` and `E` are all in the same trap. The
    solve itself was never affected; only reading its answers back was.

    `safe_sympify` reads against the small allowed namespace instead, so
    every other identifier stays an ordinary symbol -- the same fix as the
    one `re = 12000` needed in the Solve card, one layer further in.

    The imaginary unit is handled by hand rather than by the flag, because
    these strings are SymPy's *own output* rather than anything a reader
    typed: SymPy always prints the unit as a capital `I`, so a lowercase
    `i` or `j` here is a circuit symbol and nothing else. Lesson 6's
    impulse problem, whose source amplitude is called `i`, is exactly the
    case that the domain-sensitive flag would have got wrong.
    """
    from symbulator.si_prefix import safe_sympify
    import sympy as sp
    return safe_sympify(_without_unit(vstr),
                        reserve_imaginary=False).subs(sp.Symbol("I"), sp.I)


def _alias_mapping(values: dict, exclude=(), expr=None):
    """Map the symbols in `expr` onto the circuit's solved answers,
    ignoring case and underscores so every spelling of a name finds its
    answer. Names in `exclude` are skipped: a variable the user is
    solving *for* must stay unknown rather than being substituted away."""
    import sympy as sp

    skip = {_norm_name(str(e)) for e in exclude}

    # Normalised key -> value, but only where the key is unambiguous.
    by_norm, clashes = {}, set()
    for k, vstr in values.items():
        key = _norm_name(k)
        if key in by_norm:
            clashes.add(key)
        # Canonicalised on the way in: sympify does not know the
        # namespace, so a time-domain answer comes back carrying a plain
        # `t` that matches nothing the user can type. See _canonical_time.
        by_norm[key] = _canonical_time(_parse_answer(vstr))

    mapping = {}
    symbols = expr.free_symbols if expr is not None else set()
    for sym in symbols:
        key = _norm_name(str(sym))
        if key in skip or key in clashes or key not in by_norm:
            continue
        mapping[sym] = by_norm[key]
    return mapping


#: The comparisons, as the predicate `refine()` can actually act on. A bare
#: relational is not one: `refine(sqrt(x**2), x > 0)` hands the expression
#: straight back, while `refine(sqrt(x**2), Q.positive(x))` gives `x`. So a
#: condition is translated rather than passed through. `!=` is absent on
#: purpose -- `_parse_condition` splits on the `=` inside it and would make
#: nonsense of the left side, on the Solve card as much as here.
_ASSUMPTION_FOR = {">": "positive", ">=": "nonnegative",
                   "<": "negative", "<=": "nonpositive"}


def _answers_time_symbol():
    """The `t` everything Symbulator reads and writes is expressed in.

    `Symbol("t", nonnegative=True)` -- see symbulator.laplace, where the
    assumption is load-bearing and the docstring says in as many words not
    to re-create it. `_allowed_namespace` already binds `t` to this one, so
    every box on the page parses `t` correctly and always did."""
    from symbulator.laplace import t as time_symbol

    return time_symbol


def _canonical_time(expr):
    """A plain `Symbol("t")` rewritten as the one everything else uses.

    This is #95, and it is the **answers** that need it, not the input.
    A solved answer crosses back to the browser as a string and is read
    again by `_alias_mapping` -- with bare `sp.sympify`, which knows
    nothing of the namespace and so makes a plain `Symbol("t")`. A `t`
    typed into any box is the nonnegative one. The two are different
    symbols, subs() between them does nothing, and nothing says so. That
    is why `t = to` in the Solve card looked exactly like it should pin
    the time and never did, while `V = 10` in the same box worked.

    Only `t` needs it: `s` is a plain symbol on both sides already.

    Safe for impulses. DiracDelta collapses to 0 under `positive`, which
    is why laplace chose `nonnegative`; under `nonnegative` both it and
    Heaviside survive."""
    import sympy as sp

    if expr is None or not hasattr(expr, "subs"):
        return expr
    plain = sp.Symbol("t")
    if plain in getattr(expr, "free_symbols", set()):
        return expr.subs(plain, _answers_time_symbol())
    return expr


def _evaluate_conditions(conditions, values):
    """Evaluate's Conditions box, split into the two things it can mean.

    An equality whose left-hand side is a single name is a **substitution**:
    `t = to` is the calculator's `vc|t=to`, which is what the box is mostly
    for. A comparison is an **assumption**, translated into the predicate
    refine() wants. An equality with anything else on the left would be a
    little equation to solve, which is the Solve card's job, and is refused
    here rather than quietly ignored.

    Returns (substitutions, assumptions), or an error payload."""
    import sympy as sp

    lines = [ln.strip() for ln in (conditions or []) if str(ln).strip()]
    subs_map, assumptions = {}, []
    for line in lines:
        try:
            parsed = _parse_condition(line)
        except Exception as exc:                              # noqa: BLE001
            return _err(msg(M_BAD_CONDITION, line=line,
                            error=_exc_text(exc)))
        if isinstance(parsed, sp.Equality):
            left = _canonical_time(parsed.lhs)
            if not isinstance(left, sp.Symbol):
                return _err(msg(M_CONDITION_SOLVES, line=line))
            right = _canonical_time(parsed.rhs)
            right = right.subs(_alias_mapping(values, expr=right))
            subs_map[left] = right
            continue
        op = getattr(parsed, "rel_op", None)
        name = _ASSUMPTION_FOR.get(op)
        if name is None:
            return _err(msg(M_CONDITION_SHAPE, line=line))
        side = _canonical_time(parsed.lhs - parsed.rhs)
        side = side.subs(_alias_mapping(values, expr=side))
        assumptions.append(getattr(sp.Q, name)(side))
    return subs_map, assumptions


def _apply_conditions(expr, subs_map, assumptions):
    """The Conditions box, applied to one expression."""
    import sympy as sp

    if not subs_map and not assumptions:
        return expr
    expr = _canonical_time(expr)
    if subs_map:
        expr = expr.subs(subs_map)
    if assumptions:
        expr = sp.refine(expr, sp.And(*assumptions))
    return expr


def _unbrace_for(text: str, domain: str) -> str:
    """`{...}` in an input, when the analysis is the one it belongs to.

    The brackets convert from time into s, so they mean something only
    where the convention being escaped is the s-domain one -- FD. In TR
    every input is already in time and there is nothing to escape, so
    they are left alone rather than silently transforming an expression
    into the wrong domain.
    """
    if "{" not in (text or ""):
        return text
    if domain != "fd":
        # Saying so beats letting sympify report "contains a set", which
        # is true and tells the reader nothing about what they did.
        from symbulator.elements import CircuitError

        where = {"tr": "TR", "dc": "DC", "ac": "AC"}.get(domain, "this analysis")
        raise CircuitError(
            f"Brackets convert an expression from the time domain into s, "
            f"which only applies in FD. In {where} every input is already "
            f"read in the domain the answers are in, so there is nothing "
            f"to convert -- write the expression without the brackets.")
    from symbulator.si_prefix import expand_time_domain_braces

    return expand_time_domain_braces(text)


def _sympify_input(text: str):
    """One side of an equation or condition, read the way every other
    input is read.

    The Solve card used to call sp.sympify directly, which meant it alone
    understood none of Symbulator's notation -- `t2s(...)` came back
    echoed rather than computed, because sympify has no such name and
    quietly made an undefined function of it, and `u(t)`, `delta(t)`,
    `2'k`, `e^2` and `(5\u222030\u00b0)` were all unavailable."""
    from symbulator.si_prefix import safe_sympify

    return safe_sympify(expand_value_for_ui(text))


def _parse_equation(text: str):
    """"lhs = rhs" -> Eq(lhs, rhs); a bare expression -> Eq(expr, 0)."""
    import sympy as sp

    if "=" in text:
        lhs, rhs = text.split("=", 1)
        return sp.Eq(_sympify_input(lhs), _sympify_input(rhs))
    return sp.Eq(_sympify_input(text), 0)


def _parse_condition(text: str):
    """Parse one "Conditions / constraints" clause into a sympy relational
    -- '=' becomes an equality, the four comparisons become the matching
    sympy relational. Used to filter multiple solve() branches down to
    the physically sensible one(s), e.g. "pr1 > 0". Checked
    longest-operator-first, so ">=" and "<=" aren't misread as a bare
    ">"/"<" followed by a stray "="."""
    import sympy as sp

    ops = (
        (">=", lambda l, r: l >= r),
        ("<=", lambda l, r: l <= r),
        (">", lambda l, r: l > r),
        ("<", lambda l, r: l < r),
        ("=", sp.Eq),
    )
    for op, make in ops:
        if op in text:
            lhs, rhs = text.split(op, 1)
            return make(_sympify_input(lhs), _sympify_input(rhs))
    return _sympify_input(text)


def _conditions_hold(sol, conditions, values, wanted) -> bool:
    """True if every parsed condition holds once the solved unknowns and
    the circuit's known answers are substituted in. A condition that
    still can't be reduced to a concrete True/False (it has free symbols
    left over) is treated as satisfied -- there's nothing concrete to
    judge it against, so it isn't grounds to discard an otherwise valid
    solution."""
    import sympy as sp

    if not conditions:
        return True
    alias = _alias_mapping(values, exclude=[str(w) for w in wanted])
    for cond in conditions:
        try:
            c = cond.subs(sol).subs(alias)
            c = sp.simplify(c)
        except Exception:
            continue  # a condition that fails to evaluate isn't grounds to reject
        if c in (sp.true, True):
            continue
        if c in (sp.false, False):
            return False
    return True


def schematic_ui(desc: str):
    """Draw a circuit description as an SVG. Returns {"ok": True, "svg":
    ...} or {"ok": False, "error": message}.

    Deliberately separate from solve_ui rather than folded into it: the
    drawing is most useful on a circuit that does *not* solve yet, and
    `to_svg` parses with expand_si=False, so a bare `1k` draws where the
    solver would stop and ask which it meant. Folding it into the solve
    would mean you only ever saw a picture after a successful run, which
    is when you least need one.

    The description arrives in whichever form the page holds it -- the
    textarea uses newlines, the file format uses colons -- and both mean
    the same thing to the parser, so neither is normalised away here."""
    try:
        from symbulator.schematic import to_svg
    except ImportError:
        return _err(msg(M_NO_DRAWER))
    if not (desc or "").strip():
        return _err(msg(M_NEED_CIRCUIT))
    try:
        return _ok({"svg": to_svg(desc)})
    except Exception as exc:
        return _err(_exc_msg(exc))


# --------------------------------------------------------------------------
# Mini-tools
# --------------------------------------------------------------------------
#
# A handful of version 7's helpers answer with a *presentation* rather than
# an expression: `aa` shows a complex number as magnitude and angle, `pf`
# reads a power factor as a number and a direction. They cannot live in the
# parsing namespace, because sympify would call them with symbols still
# unsubstituted and because what they hand back is a sentence, not
# something the formatters downstream can round or prefix.
#
# So they get their own small surface, chosen by name, with their arguments
# evaluated against the solved answers first -- which is what lets a user
# write `i_r1` instead of copying a phasor out of the results by hand.
#
# `aa` is the one that mattered: it is the most-used tool in the whole
# version 7 documentation (40 calls) and version 9 had no equivalent at
# all -- its name was reserved and nothing implemented it.

MINI_TOOLS = {
    "aa": {"args": 1, "label": "aa -- amplitude and angle",
           "hint": "a complex value, as in i_r1"},
    "pf": {"args": 2, "label": "pf -- power factor",
           "hint": "a voltage and a current, as in v_1 and i_r1"},
    "gain": {"args": 4, "label": "gain -- voltage, current and power gain",
             "hint": "an input pair and an output pair: v1, i1, v2, i2"},
}


def _as_number(text: str, values: dict):
    """One mini-tool argument, resolved against the solved answers.
    Returns (value, None) or (None, error message)."""
    import sympy as sp
    from symbulator.si_prefix import safe_sympify

    if not (text or "").strip():
        return None, msg(M_GIVE_A_VALUE)
    try:
        parsed = safe_sympify(expand_value_for_ui(text))
    except Exception as exc:                                  # noqa: BLE001
        return None, _exc_text(exc)
    got = sp.simplify(parsed.subs(_alias_mapping(values, expr=parsed)))
    if got.free_symbols:
        unknown = ", ".join(sorted(str(s) for s in got.free_symbols))
        return None, msg(M_NEEDS_A_NUMBER, text=text.strip(),
                         unknown=unknown)
    return got, None


def expand_value_for_ui(text: str) -> str:
    """The same shorthand a circuit value gets, so `2'k` and `10e^2` work
    here too rather than only inside a circuit description."""
    try:
        from symbulator.si_prefix import expand_value
        return expand_value(text)
    except Exception:                                         # noqa: BLE001
        return text


def mini_tool_ui(tool: str, args, values: dict, digits: int = 4):
    """Run one mini-tool and return its answer as text."""
    try:
        import sympy as sp

        spec = MINI_TOOLS.get(tool)
        if spec is None:
            return _err(msg(M_UNKNOWN_TOOL, tool=tool))
        args = [a for a in (args or [])]
        if len(args) < spec["args"]:
            # Two codes rather than one with a pluralised argument: the
            # plural rule is the translator's, not this file's, and a
            # language with three of them needs its own sentence anyway.
            code = M_TOOL_NEEDS_N if spec["args"] > 1 else M_TOOL_NEEDS_1
            return _err(msg(code, tool=tool, n=spec["args"],
                            hint=spec["hint"]))

        numbers = []
        for a in args[:spec["args"]]:
            got, bad = _as_number(a, values)
            if bad:
                return _err(bad)
            numbers.append(got)

        def rounded(x):
            return sp.N(x, digits + 2) if digits else sp.N(x)

        if tool == "aa":
            # Amplitude and angle, the way version 7 printed it:
            # 1.789 <26.57 degrees.
            z = sp.simplify(numbers[0])
            # A magnitude and an angle are both real. Evaluating them from
            # float inputs can leave a crumb of imaginary part behind --
            # "19.3649 + 0.e-13*I" -- which is arithmetically nothing and
            # visually a mess, so take the real part after evaluating.
            magnitude = sp.re(rounded(sp.Abs(z)))
            angle = sp.re(rounded(sp.deg(sp.arg(z))))
            plain = f"{magnitude}\u2220{angle}\u00b0"
            latex = (rf"{sp.latex(magnitude)} \angle "
                     rf"{sp.latex(angle)}^\circ")
            return _ok({"plain": plain, "latex": latex,
                        "magnitude": str(magnitude), "angle": str(angle)})

        if tool == "gain":
            # Four in, four out -- the only mini-tool that answers with a
            # table rather than a single value, which is why it belongs
            # here and not in Evaluate: Evaluate can show one thing.
            from symbulator.utils import gain as _gain
            got = _gain(*numbers)
            rows = [("Av", "voltage gain", got["Av"]),
                    ("Ai", "current gain", got["Ai"]),
                    ("Ap", "power gain", got["Ap"]),
                    ("Zi", "input impedance", got["Zi"])]
            return _ok({
                "plain": "  ".join(f"{k} = {_plain_with_j(rounded(v))}"
                                   for k, _, v in rows),
                "latex": r" \quad ".join(
                    rf"{k} = {_latex_with_j(rounded(v))}"
                    for k, _, v in rows),
                "rows": [{"key": k, "label": label,
                          "plain": _plain_with_j(rounded(v)),
                          "latex": _latex_with_j(rounded(v))}
                         for k, label, v in rows]})

        if tool == "pf":
            from symbulator.utils import pf as _pf
            text = _pf(*numbers)
            body = text.split(":", 1)[1].strip() if ":" in text else text
            magnitude, _, direction = body.partition(" ")
            return _ok({"plain": body, "latex": rf"\text{{{body}}}",
                        "magnitude": magnitude,
                        "direction": direction.strip()})

        return _err(msg(M_UNKNOWN_TOOL, tool=tool))
    except Exception as exc:                                  # noqa: BLE001
        return _err(_exc_msg(exc))


# --------------------------------------------------------------------------
# Power factor, in Evaluate
# --------------------------------------------------------------------------
#
# `pf` is a different creature from everything else Evaluate takes. It
# answers with a sentence -- "pf: 0.6 lagging" -- rather than an
# expression, and it needs its two arguments as actual numbers: given
# symbols it raises "Cannot convert expression to float", because it has
# to compare an angle against zero to decide leading from lagging.
#
# So it cannot live in the parsing namespace, where sympify would call it
# with the symbols still unsubstituted. It is handled here instead, as a
# form Evaluate recognises: each argument is evaluated against the solved
# answers first -- which is the whole reason Evaluate is the right home,
# since it is the only place a user can refer to a phasor as `v_1` rather
# than retyping it -- and only then does pf see two numbers.

_PF_CALL = re.compile(r"^\s*pf\s*\((.*)\)\s*$", re.S)

#: `s2t(v_o)` and `t2s(...)` have the same trouble as pf, for the same
#: reason: sympify calls the function while its argument is still the
#: symbol `v_o`, so the transform is taken of a bare symbol and the answer
#: comes back 0 -- silently, and 0 is a plausible-looking voltage. The
#: answer has to be substituted in first, and only then transformed.
_TRANSFORM_CALL = re.compile(r"^\s*(s2t|t2s)\s*\((.*)\)\s*$", re.S)


def _domain_transform(expr_str: str, values: dict, subs_map=None,
                      assumptions=None):
    """`s2t(...)` / `t2s(...)` against the solved answers, or None."""
    m = _TRANSFORM_CALL.match(expr_str)
    if not m:
        return None
    name, inner = m.group(1), m.group(2)

    import sympy as sp
    from symbulator.laplace import s2t, t2s
    from symbulator.si_prefix import safe_sympify

    try:
        parsed = safe_sympify(expand_value_for_ui(inner))
        substituted = parsed.subs(_alias_mapping(values, expr=parsed))
        substituted = _apply_conditions(substituted, subs_map, assumptions)
        got = (s2t if name == "s2t" else t2s)(sp.simplify(substituted))
    except Exception as exc:                                  # noqa: BLE001
        return _err(_exc_msg(exc))
    return got


def _split_two_args(inside: str):
    """The two arguments of a pf(...) call, split on the comma that
    separates them rather than on any comma inside a nested call."""
    depth, split_at = 0, None
    for i, ch in enumerate(inside):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            if split_at is not None:
                return None            # more than two: not a pf call
            split_at = i
    if split_at is None:
        return None
    return inside[:split_at], inside[split_at + 1:]


def _power_factor(expr_str: str, values: dict, subs_map=None,
                  assumptions=None):
    """`pf(v, i)` against the solved answers, or None if this is not one."""
    m = _PF_CALL.match(expr_str)
    if not m:
        return None
    args = _split_two_args(m.group(1))
    if not args:
        return _err(msg(M_PF_TWO_VALUES))

    import sympy as sp
    from symbulator.si_prefix import safe_sympify
    from symbulator.utils import pf as _pf

    numbers = []
    for arg in args:
        parsed = safe_sympify(expand_value_for_ui(arg))
        got = parsed.subs(_alias_mapping(values, expr=parsed))
        got = sp.simplify(_apply_conditions(got, subs_map, assumptions))
        if got.free_symbols:
            unknown = ", ".join(sorted(str(s) for s in got.free_symbols))
            return _err(msg(M_PF_NEEDS_NUMBERS, arg=arg.strip(),
                            unknown=unknown))
        numbers.append(got)

    text = _pf(*numbers)
    # pf() answers "pf: 0.6 lagging". Split it so the interface can show
    # the number and the direction as the two things they are.
    body = text.split(":", 1)[1].strip() if ":" in text else text
    magnitude, _, direction = body.partition(" ")
    return _ok({"plain": body, "latex": rf"\text{{{body}}}",
                "magnitude": magnitude, "direction": direction.strip(),
                "text_only": True})


def evaluate_ui(expr_str: str, values: dict, digits: int = 0,
                 si: bool = False, approx: bool = False,
                 domain: str = "", conditions=None, dual: bool = False):
    """Evaluate a user expression against the solved values. Names match
    however they are spelled: `i_r1`, `i_R1`, `iR1` and `IR1` all find
    the same answer, since element names are lowercase by this point."""
    try:
        import sympy as sp
        from symbulator.si_prefix import safe_sympify

        # `{...}` converts from time into s, so it means something only in
        # FD. Done first, before anything else reads the expression.
        expr_str = _unbrace_for(expr_str, domain)

        # The Conditions box (#96). Read once, up front, so every form
        # below gets the same substitutions and assumptions -- including
        # pf(), whose arguments have to come out as numbers, and which is
        # exactly where "at t = to" earns its keep.
        conds = _evaluate_conditions(conditions, values)
        if isinstance(conds, dict):
            return conds                     # an error reading the box
        subs_map, assumptions = conds

        # The one formatting recipe this function uses, in one place --
        # it appeared twice, and #175 would have made it twice again.
        def shown_pair(result, *, digits=digits, si=si, approx=approx):
            if si:
                got = _si_format(result, digits)
                if got is not None:
                    return got
            if approx and not digits:
                got = _approx_format(result)
                if got is not None:
                    return got
                result = sp.N(result)
            result = _round_expr(result, digits)
            return _plain_with_j(result), _latex_with_j(result)

        def shown(result):
            """#175: exact, then the approximation in brackets, when the
            mode asks for both and the answer is a pure number."""
            if not (dual and digits):
                return shown_pair(result)
            exact = shown_pair(result, digits=0, si=False, approx=False)
            if getattr(result, "free_symbols", None):
                return exact
            try:
                approximate = shown_pair(result, digits=digits, si=si,
                                         approx=False)
            except Exception:
                return exact
            if not _has_exact_form(sp, result, False):            # #181
                return approximate
            if _is_whole(sp, result):
                return exact                                         # #181
            return _join_dual(exact, approximate)

        # pf() is answered before the ordinary path, because it wants its
        # arguments as numbers and gives back a sentence.
        power_factor = _power_factor(expr_str, values, subs_map, assumptions)
        if power_factor is not None:
            return power_factor

        # A domain transform is answered with the ordinary formatting, so
        # it is folded back into `result` rather than returned whole.
        transformed = _domain_transform(expr_str, values, subs_map, assumptions)
        if isinstance(transformed, dict):
            return transformed          # an error from the transform
        if transformed is not None:
            result = sp.simplify(transformed)
            plain, latex = shown(result)
            return _ok({"plain": plain, "latex": latex})

        # Through the same shorthand a circuit value gets, so `^`, an
        # implied multiplication and `2'k` mean here what they mean in the
        # Circuit Description box. Without this, `vth^2*2/(req+2)^2` was
        # refused in Evaluate while the identical text was accepted as an
        # element's value -- the sort of inconsistency a reader reads as
        # the tool being broken.
        parsed = _parse_with_rearrangers(expand_value_for_ui(expr_str))
        result = parsed.subs(_alias_mapping(values, expr=parsed))
        result = _apply_conditions(result, subs_map, assumptions)
        # After the substitution, not before: expand() of a bare `vo` is
        # `vo`, and the rearrangement would be thrown away by the very
        # substitution that gives it something to work on.
        result, rearranged = _apply_rearrangers(result)
        # And no simplify() over the top of one. Asking to expand an answer
        # is asking for a particular arrangement of it; simplify would
        # gather it straight back up, so the card would appear to ignore
        # what was typed. Everything else still gets tidied as before.
        if not rearranged:
            result = sp.simplify(result)
        plain, latex = shown(result)
        return _ok({"plain": plain, "latex": latex})
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))


# Units inferred from an answer's name prefix, for labelling solved
# unknowns like "p_out" or "i_x".
_PREFIX_UNITS = {"v": "V", "i": "A", "p": "W", "ap": "W", "s": "VA",
                 "z": "ohm", "r": "ohm"}


def solveq_ui(equations, unknowns, values: dict, digits: int = 0,
                   si: bool = False, approx: bool = False,
                   units: bool = False, real_only: bool = False,
                   conditions=None, domain: str = "", dual: bool = False):
    """Solve user equations against the circuit's answers -- the web
    counterpart of the calculator's solve()/cSolve(). Known answers are
    substituted in first, so an equation can be written directly in
    terms of v2, i_r1 and friends; anything left over is solved for.

    `real_only` is the difference between the calculator's two verbs:
    off is cSolve() (every root, complex ones included), on is solve()
    (the unknowns are declared real, so complex roots never appear).

    `conditions`: constraints ("pr1 > 0", "v1 = 12") that filter down
    which of possibly several solve() branches to keep -- sp.solve()
    happily returns every algebraic root (e.g. both signs of a squared
    term) with no way on its own to prefer the physically sensible one."""
    try:
        import sympy as sp

        # Canonicalised like everything else: an unknown named `t` has to
        # be the same symbol the equations and the answers carry, or solve()
        # is asked for a symbol that does not appear in them and hands the
        # system back unsolved. The other half of #95.
        wanted = ([_canonical_time(sp.Symbol(u)) for u in unknowns]
                  if unknowns else [])
        # Same convention as everywhere else: brackets are FD's escape
        # from its own s-domain rule, and mean nothing in TR.
        equations = [_unbrace_for(e, domain) for e in equations]
        if conditions:
            conditions = [_unbrace_for(c, domain) for c in conditions]
        # Answer names, spelled the way the results show them, rewritten to
        # the names the values are keyed by -- the same courtesy expert mode
        # has always had. Unknowns are deliberately left alone: they are
        # names the reader is inventing, not answers, and _alias_mapping
        # already matches them either way round.
        alias = _aliases_from_values(values)
        if alias:
            equations = [apply_answer_aliases(e, alias)[0] for e in equations]
            if conditions:
                conditions = [apply_answer_aliases(c, alias)[0]
                              for c in conditions]
        parsed_eqs = [_parse_equation(e) for e in equations]
        eqs = []
        for eq in parsed_eqs:
            eqs.append(eq.subs(_alias_mapping(
                values, exclude=[str(w) for w in wanted], expr=eq)))

        if not wanted:
            # Nothing named: solve for whatever symbols remain.
            free = set()
            for eq in eqs:
                free |= eq.free_symbols
            wanted = sorted(free, key=str)
        if not wanted:
            return _err(msg(M_NOTHING_TO_SOLVE))

        if real_only:
            # Re-declare the unknowns as real. SymPy then solves over
            # the reals: x**2 = -1 simply has no solution, rather than
            # returning +/-j. The names are unchanged, so everything
            # downstream (units, labels) still works.
            # A symbol that already carries an assumption implying real --
            # `t` is nonnegative -- is left alone: re-declaring it would
            # throw that away and put the mismatch back.
            real_map = {s: sp.Symbol(str(s), real=True)
                        for s in wanted if not s.is_real}
            eqs = [eq.xreplace(real_map) for eq in eqs]
            wanted = [real_map.get(s, s) for s in wanted]

        sols = sp.solve(eqs, wanted, dict=True)
        if real_only:
            # sp.solve honours the assumption for most systems, but not
            # all; drop anything that still came back complex.
            def _is_real(v):
                """True unless `v` is a concrete number whose imaginary
                part is provably nonzero. A symbolic value is always kept
                (there's nothing to judge without a number in hand); a
                numeric one is checked by simplifying its imaginary part
                to see if it's exactly 0, falling back to sympy's own
                is_real flag if that check is inconclusive."""
                if getattr(v, "free_symbols", None):
                    return True          # symbolic -- can't judge, keep it
                return sp.im(sp.nsimplify(v)) == 0 or v.is_real is not False
            sols = [s for s in sols if all(_is_real(v) for v in s.values())]

        had_sols = bool(sols)
        if conditions:
            parsed_conds = [_parse_condition(c) for c in conditions]
            if real_only and real_map:
                # The unknowns were re-declared as real above, so a
                # solution is keyed by Symbol("w", real=True) while the
                # condition was parsed against a bare Symbol("w"). Those
                # are different symbols and subs() silently does nothing,
                # which left the condition unevaluated and every root
                # kept -- `w > 0` quietly filtering nothing at all.
                parsed_conds = [c.xreplace(real_map) for c in parsed_conds]
            sols = [s for s in sols
                    if _conditions_hold(s, parsed_conds, values, wanted)]

        if not sols:
            if conditions and had_sols:
                return _ok({"solutions": [],
                            "unknowns": [str(w) for w in wanted],
                            "notes": [msg(M_NO_SOLUTION_COND)]})
            if real_only:
                return _ok({"solutions": [],
                            "unknowns": [str(w) for w in wanted],
                            "notes": [msg(M_NO_REAL_SOLUTION)]})
            return _ok({"solutions": [], "unknowns": [str(w) for w in wanted]})

        def render(expr, unit):
            """The same SI/approx/rounded formatting as `fmt`/`fmt0`
            above (see `fmt0`'s docstring), for one solved value of an
            equation system -- yet another copy of that same small
            formatting recipe, here because solveq_ui doesn't share a
            call frame with solve_ui."""
            def once(expr, unit="", *, digits=digits, si=si, approx=approx,
                     polar=False):
                try:
                    expr = sp.simplify(expr)
                except Exception:
                    pass
                has_syms = bool(getattr(expr, "free_symbols", None))
                show_unit = units and not has_syms
                if si:
                    got = _si_format(expr, digits, unit if show_unit else "")
                    if got is not None:
                        return got
                if approx and not digits:
                    got = _approx_format(expr)
                    if got is not None:
                        return _with_unit(got[0], got[1], unit, show_unit)
                    expr = sp.N(expr)
                expr = _round_expr(expr, digits)
                return _with_unit(_plain_with_j(expr), _latex_with_j(expr),
                                  unit, show_unit)

            if dual and digits:                                     # #175
                return _dualise(once, sp, expr, unit, digits, si, False)
            return once(expr, unit)

        out = []
        for sol in sols:
            entry = []
            for sym in wanted:
                if sym not in sol:
                    continue
                name = str(sym)
                prefix = name.split("_", 1)[0] if "_" in name else ""
                plain, latex = render(sol[sym], _PREFIX_UNITS.get(prefix, ""))
                entry.append({"name": name, "plain": plain, "latex": latex})
            if entry:
                out.append(entry)
        return _ok({"solutions": out,
                          "unknowns": [str(w) for w in wanted]})
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))




def spice_ui(direction: str, text: str):
    """Translate between Symbulator notation and a SPICE netlist (the
    SPICE Translator card, #160). `direction` is "to_spice" or
    "from_spice"; the answer carries the translated text and the
    translator's warnings -- elements or values the destination cannot
    express are reported there, never silently mistranslated."""
    try:
        from symbulator.spice import to_spice, from_spice

        text = (text or "").strip()
        if not text:
            return _err(msg(M_NEED_CIRCUIT))
        if direction == "to_spice":
            out, warnings = to_spice(text)
        elif direction == "from_spice":
            out, warnings = from_spice(text)
        else:
            return _err(msg(M_BAD_DIRECTION, direction=direction))
        return _ok({"output": out, "warnings": warnings})
    except Exception as exc:  # noqa: BLE001
        return _err(_exc_msg(exc))
