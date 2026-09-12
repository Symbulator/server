#!/usr/bin/env python
"""The Solve card's conditions and extra equations behave like Expert
Mode's (#433).

Roberto solved the Wheatstone bridge of NR12's Example 3.10 with the
Solve card -- the galvanometer a short `sg`, `isg = 0` the equation,
`R_x` the unknown, `R_3 = 10` the condition -- and got `R_x = 4 R_3`
where Expert Mode, fed the same three things, gives 40 Ω. Writing
`R_3 = 10` as a second equation instead did no better. Both are the
calculator's `|` operator, and the solver has always read them that way
for Expert Mode; the card read a condition only as a filter, and dropped
an equation that named none of its unknowns.

Runs the REAL app -- `solve_ui` for the circuit's answers, then
`solveq_ui` on the values the page holds -- and asserts:

  * the condition `R_3 = 10` substitutes: `R_x` = 40;
  * the equation `R_3 = 10` beside `isg = 0` is solved, not dropped:
    `R_x` = 40 and `R_3` = 10 come back together;
  * with neither, the answer is still the symbolic `4 R_3` -- nothing is
    invented for a symbol nothing pins;
  * an inequality still filters: `x**2 = 4` with `x > 0` keeps 2 alone;
  * an equality on the unknown itself is a filter, not a substitution:
    `x**2 = 4` with `x = -2` keeps -2 alone;
  * the Solve card and Expert Mode agree on Roberto's file, to the digit.

Run from repos/server:

    py tools/check_solveq_conditions.py
    py tools/check_solveq_conditions.py --prove-red   # disables both halves

`--prove-red` makes every equality a filter again and stops the extra
unknowns being picked up, and expects the check to FAIL: a guard nobody
has watched fail is not a guard.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import symbulator_ui as ui                                   # noqa: E402

BRIDGE = "e,1,0,V_s:r1,1,a,1'k:r2,1,b,4'k:sg,a,b:r3,a,0,R_3:rx,b,0,R_x"

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'BAD'} {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        failures.append(label)


def circuit_values(desc):
    r = ui.solve_ui(desc, "dc", "", [], "solve", "", "", "z", [], [], [],
                    digits=4, approx=True, units=True)
    assert r.get("ok"), r
    return r["values"]


def solveq(values, equations, unknowns, conditions=(), real_only=True):
    r = ui.solveq_ui(list(equations), list(unknowns), values, digits=4,
                     approx=True, units=True, real_only=real_only,
                     conditions=list(conditions), domain="dc")
    assert r.get("ok"), r
    return {v["name"]: v["plain"] for sol in r["solutions"] for v in sol}, r


def number(got, name):
    """The leading number of a shown value, or the whole value when it
    is symbolic or missing -- so a red check prints what came back."""
    txt = got.get(name, "(missing)")
    return txt.split()[0] if txt.split() else txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prove-red", action="store_true")
    args = ap.parse_args()
    if args.prove_red:
        # Both halves of #433 off: no equality ever binds, and no symbol
        # is picked up as an unknown from an equation that names none.
        ui._equality_binding = lambda cond, wanted: (None, None)
        import sympy as sp
        _real_solve = sp.solve

        def only_named(eqs, wanted, **kw):
            if isinstance(wanted, list) and len(wanted) > 1:
                wanted = wanted[:1]
            return _real_solve(eqs, wanted, **kw)
        sp.solve = only_named
        print("prove-red: equality conditions are filters again, extra unknowns dropped")

    vals = circuit_values(BRIDGE)
    print("Roberto's file, the Solve card:")
    got, _ = solveq(vals, ["isg=0"], ["R_x"], ["R_3=10"])
    check("condition R_3=10 substitutes", number(got, "R_x"), "40")
    got, _ = solveq(vals, ["isg=0", "R_3=10"], ["R_x"])
    check("equation R_3=10 is solved, not dropped: R_x", number(got, "R_x"), "40")
    check("equation R_3=10 is solved, not dropped: R_3", number(got, "R_3"), "10")
    got, _ = solveq(vals, ["isg=0"], ["R_x"])
    check("with neither, R_3 is given no value and the answer stays symbolic",
          number(got, "R_x"), "4.0*R_3")

    print("Filters still filter:")
    got, r = solveq({}, ["x**2 = 4"], ["x"], ["x > 0"])
    check("x**2 = 4 with x > 0 keeps one root", [s[0]["plain"] for s in r["solutions"]], ["2"])
    got, r = solveq({}, ["x**2 = 4"], ["x"], ["x = -2"])
    check("x**2 = 4 with x = -2 keeps the named root", [s[0]["plain"] for s in r["solutions"]], ["-2"])

    print("Solve card and Expert Mode agree on the file:")
    ex = ui.solve_ui(BRIDGE, "dc", "", [], "solve", "", "", "z",
                     ["i_sg=0"], ["R_x"], ["R_3=10"], digits=4, approx=True, units=True)
    assert ex.get("ok"), ex
    expert = {x["name"]: x["plain"] for x in ex["extras"]}
    got, _ = solveq(vals, ["isg=0"], ["R_x"], ["R_3=10"])
    check("Expert Mode R_x", number(expert, "R_x"), "40")
    check("the two cards give the same number", number(got, "R_x"), number(expert, "R_x"))

    # The offline bridge is the other front end, and it is in the sibling
    # repo; when that repo is beside this one, both of Roberto's forms go
    # through it too. It had never expanded the ` and ` form of an
    # equation, so `isg=0 and R_3=10` was refused offline while the
    # hosted app solved it.
    local = HERE.parent.parent / "local"
    if (local / "bridge.py").exists():
        print("The offline bridge (repos/local/bridge.py):")
        import json
        sys.path.insert(0, str(local))
        import bridge
        for label, eqs in (("and form", "isg=0 and R_3=10"), ("two lines", "isg=0\nR_3=10")):
            j = json.loads(bridge.solve_equations(json.dumps(dict(
                values=vals, equations=eqs, unknowns="R_x", conditions="",
                real_only=True, digits=4, approx=True, units=True, domain="dc"))))
            got = {v["name"]: v["plain"] for s in (j.get("solutions") or []) for v in s}
            check(f"bridge, {label}: R_x", number(got, "R_x"), "40")
            check(f"bridge, {label}: R_3", number(got, "R_3"), "10")
    else:
        print("(repos/local not beside this repo; the bridge is not checked)")

    if failures:
        print(f"\n{len(failures)} check(s) failed: " + ", ".join(failures))
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
