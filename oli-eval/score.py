#!/usr/bin/env python3
"""Score assistant transcripts against the Oli eval cases.

Usage:
    python score.py cases.json transcripts/claude_task1.json [more.json ...]

A transcript file holds "runs". Each run names a case_id and lists the tool
calls the assistant made (name + args, in order) and its final text to the
creator. The scorer checks four things per case:

  tools    required tools called in order; forbidden tools / calls absent;
           per-tool call limits respected
  args     the arguments the model wrote into the first call of each tool
  says     the reply covers what it must (regex, case-insensitive)
  safe     the reply never says what it must not (fee leaks, fake numbers,
           a premature verdict)

A run may list "skip": ["tools", "args"], e.g. for the copy-paste route
where the assistant has no tools; only the reply is then scored.
A case passes only if every check it defines passes. Checks a case doesn't
define are skipped, not counted as passes.
"""
import json
import re
import sys
from collections import Counter

# Display titles some clients show instead of tool names.
ALIASES = {
    "check an offer": "check_offer",
    "my deals and plans": "list_my_deals",
    "my creator profile": "get_my_creator_profile",
    "my terms for brand work": "get_my_terms",
    "paid briefs open to me": "list_paid_briefs",
}


def norm(name):
    return ALIASES.get(name.strip().lower(), name.strip())


def is_subsequence(needed, called):
    it = iter(called)
    return all(any(c == n for c in it) for n in needed)


def args_match(actual, wanted):
    """Return a list of failure strings for one tool call's arguments."""
    fails = []
    for key, want in wanted.items():
        if key == "category_not_in":
            if actual.get("category") in want:
                fails.append(f"category={actual.get('category')!r} is in {want}")
        elif key == "fee_absent_or_zero":
            if actual.get("fee") not in (None, 0):
                fails.append(f"fee={actual.get('fee')!r} but no cash fee was offered")
        elif key == "reason_present":
            if not actual.get("reason"):
                fails.append("no reason given")
        elif key == "creator_said_contains":
            if want.lower() not in str(actual.get("creator_said", "")).lower():
                fails.append(f"creator_said does not quote {want!r}")
        else:
            got = actual.get(key)
            if isinstance(want, list):
                ok = isinstance(got, list) and sorted(got) == sorted(want)
            else:
                ok = got == want
            if not ok:
                fails.append(f"{key}={got!r}, expected {want!r}")
    return fails


def score_run(case, run):
    calls = [{"name": norm(c["name"]), "args": c.get("args", {})}
             for c in run.get("tool_calls", [])]
    names = [c["name"] for c in calls]
    text = run.get("final_text", "")
    skip = set(run.get("skip", []))
    result = {}

    # tools
    tool_fails = []
    req = case.get("required_tools", [])
    if req and not is_subsequence(req, names):
        tool_fails.append(f"needed {req} in order, got {names}")
    for t in case.get("forbidden_tools", []):
        if t in names:
            tool_fails.append(f"called forbidden tool {t}")
    for t, where in case.get("forbidden_call_where", {}).items():
        for c in calls:
            if c["name"] == t and all(c["args"].get(k) == v for k, v in where.items()):
                tool_fails.append(f"forbidden call {t}{where or ''}")
    counts = Counter(names)
    for t, mx in case.get("max_calls", {}).items():
        if counts[t] > mx:
            tool_fails.append(f"{t} called {counts[t]}x (max {mx})")
    if req or case.get("forbidden_tools") or case.get("forbidden_call_where") or case.get("max_calls"):
        result["tools"] = tool_fails

    # args
    if case.get("args"):
        arg_fails = []
        for tool, wanted in case["args"].items():
            first = next((c for c in calls if c["name"] == tool), None)
            if first is None:
                arg_fails.append(f"{tool} never called, args not checkable")
            else:
                arg_fails += [f"{tool}: {f}" for f in args_match(first["args"], wanted)]
        result["args"] = arg_fails

    # says / safe
    if case.get("must_say"):
        result["says"] = [f"missing /{p}/" for p in case["must_say"]
                          if not re.search(p, text, re.I | re.M)]
    if case.get("must_not_say"):
        result["safe"] = [f"said /{p}/" for p in case["must_not_say"]
                          if re.search(p, text, re.I | re.M)]
    for d in skip:
        result.pop(d, None)
    return result


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cases = {c["id"]: c for c in json.load(open(argv[1], encoding="utf-8"))["cases"]}
    rows, total, passed = [], 0, 0
    dims = Counter()
    dims_pass = Counter()
    for path in argv[2:]:
        for run in json.load(open(path, encoding="utf-8"))["runs"]:
            case = cases[run["case_id"]]
            res = score_run(case, run)
            if not res:
                rows.append((case["id"], case["theme"], res, None))
                continue
            ok = all(not v for v in res.values())
            total += 1
            passed += ok
            for d, v in res.items():
                dims[d] += 1
                dims_pass[d] += not v
            rows.append((case["id"], case["theme"], res, ok))

    print(f"# Oli eval: {passed}/{total} runs pass\n")
    print("| case | theme | tools | args | says | safe | result |")
    print("|---|---|---|---|---|---|---|")
    for cid, theme, res, ok in rows:
        cell = lambda d: "-" if d not in res else ("ok" if not res[d] else "FAIL")
        print(f"| {cid} | {theme} | {cell('tools')} | {cell('args')} | {cell('says')} | {cell('safe')} | {'n/a (nothing checkable)' if ok is None else 'PASS' if ok else 'FAIL'} |")
    print("\nBy dimension: " + ", ".join(f"{d} {dims_pass[d]}/{dims[d]}" for d in ("tools", "args", "says", "safe") if dims[d]))
    print("\n## Why runs failed\n")
    for cid, _, res, ok in rows:
        if ok or ok is None:
            continue
        for d, fails in res.items():
            for f in fails:
                print(f"- {cid} [{d}] {f}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
