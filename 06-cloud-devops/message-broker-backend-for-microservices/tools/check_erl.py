#!/usr/bin/env python3
"""Structural Erlang checker (stdlib only, no Erlang toolchain needed).

For every src/*.erl and test/*.erl file it validates:

  * `-module(<name>.)` matches the file name
  * printable ASCII only: no tabs, no CR, no stray high-bit characters
  * parentheses/brackets/braces (and << >> binaries) are balanced
  * every form is terminated with `.` and every clause group parses as
    function clauses with a consistent name/arity (catches missing dots)
  * every entry of every `-export([...]).` has a matching function head
  * every `-spec name/arity` refers to an actually defined function
  * every exported function carries a `-spec` (mirrors warn_missing_spec)
  * every module has at least one spec

Term-level files (rebar.config, *.app.src, sys.config) get the ASCII,
balance and termination checks; the .app.src additionally gets application
name, {applications, ...} and {modules, ...} consistency checks.

Exit code 0 = no errors (warnings allowed), 1 otherwise.
"""
import os
import re
import sys

ERRORS = []
WARNINGS = []


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def err(path, text, pos, msg):
    ERRORS.append("%s:%d: error: %s" % (path, line_of(text, pos), msg))


def err_line(path, line, msg):
    ERRORS.append("%s:%d: error: %s" % (path, line, msg))


def warn(path, text, pos, msg):
    WARNINGS.append("%s:%d: warning: %s" % (path, line_of(text, pos), msg))


def check_ascii(path, raw):
    """No tabs, no CRLF, printable ASCII + newline only."""
    ok = True
    for i, ch in enumerate(raw):
        if ch == "\t":
            err(path, raw, i, "tab character (parse-hostile, use spaces)")
            ok = False
        elif ch == "\r":
            err(path, raw, i, "carriage return (CRLF line ending)")
            ok = False
        elif ord(ch) > 126:
            err(path, raw, i, "non-ASCII character U+%04X" % ord(ch))
            ok = False
        if not ok:
            # one detailed report per file is enough for these classes
            break
    return ok


def blank(raw):
    """Replace comments/strings with spaces and char literals with 'x',
    so downstream scans never see dots, commas or brackets that belong to
    string literals or comments, while arity counting still sees that a
    character-literal argument exists."""
    out = list(raw)
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "%":
            while i < n and raw[i] != "\n":
                out[i] = " "
                i += 1
        elif ch == '"':
            # string literal -> spaces (commas inside strings must not
            # be seen by arity counting)
            out[i] = " "
            i += 1
            while i < n and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < n:
                    out[i] = " "
                    if raw[i + 1] != "\n":
                        out[i + 1] = " "
                    i += 2
                else:
                    if raw[i] != "\n":
                        out[i] = " "
                    i += 1
            if i < n:
                out[i] = " "
                i += 1
        elif ch == "$":
            # char literal ($x, $\x, $\' etc) -> a single visible token
            start = i
            i += 1
            if i < n:
                if raw[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            for j in range(start, i):
                out[j] = "x"
        else:
            i += 1
    return "".join(out)


def check_balance(path, text):
    """Stack-based balance for () [] {} << >> with position reporting."""
    pairs = {")": "(", "]": "[", "}": "{", ">>": "<<"}
    stack = []
    i = 0
    n = len(text)
    ok = True
    while i < n:
        two = text[i:i + 2]
        if two == "<<":
            stack.append(("<", i))
            i += 2
            continue
        if two == ">>":
            if stack and stack[-1][0] == "<":
                stack.pop()
            else:
                err(path, text, i, "unmatched '>>'")
                ok = False
            i += 2
            continue
        ch = text[i]
        if ch in "([{":
            stack.append((ch, i))
        elif ch in ")]}":
            if not stack or stack[-1][0] != pairs[ch]:
                err(path, text, i, "unmatched '%s'" % ch)
                ok = False
            else:
                stack.pop()
        i += 1
    for ch, pos in stack:
        err(path, text, pos, "unclosed '%s'" % ("<" if ch == "<" else ch))
        ok = False
    return ok


def form_end(text, i):
    """A depth-0 '.' terminates a form when the next character cannot be
    part of a float or a record-access field (i.e. not alphanumeric/'_')."""
    if text[i] != ".":
        return False
    nxt = text[i + 1] if i + 1 < len(text) else ""
    return nxt == "" or not (nxt.isalnum() or nxt == "_")


def split_forms(path, text):
    """Split into top-level forms on depth-0 form-ending dots."""
    forms = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    ok = True
    while i < n:
        two = text[i:i + 2]
        if two == "<<" or text[i] in "([{":
            depth += 1
            i += 2 if two == "<<" else 1
            continue
        if two == ">>" or text[i] in ")]}":
            depth -= 1
            i += 2 if two == ">>" else 1
            continue
        if text[i] == "." and depth == 0 and form_end(text, i):
            chunk = text[start:i + 1]
            if chunk.strip():
                forms.append((chunk, line_of(text, start)))
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        err(path, text, start, "trailing text with no terminating '.'")
        ok = False
    return forms, ok


def paren_match(text, open_pos):
    """Index just past the ')' matching the '(' at open_pos (blanked text)."""
    depth = 0
    i = open_pos
    n = len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "<<" or text[i] in "([{":
            depth += 1
        elif two == ">>" or text[i] in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def count_arity(args):
    if not args.strip():
        return 0
    depth = 0
    commas = 0
    i = 0
    n = len(args)
    while i < n:
        two = args[i:i + 2]
        if two == "<<" or args[i] in "([{":
            depth += 1
        elif two == ">>" or args[i] in ")]}":
            depth -= 1
        elif args[i] == "," and depth == 0:
            commas += 1
        i += 1
    return commas + 1


BLOCK_OPEN = {"case", "if", "try", "receive", "begin", "cond"}


def split_clauses(form):
    """Split a function form into clause texts.

    A ';' only separates clauses at bracket depth 0, outside of
    case/if/try/receive/begin/fun...end blocks, and only once the
    current clause has passed its own '->' (so guard alternatives such as
    `when a; b ->` are not mistaken for clause boundaries)."""
    clauses = []
    depth = 0       # () [] {} << >> nesting
    block = 0       # case/if/try/receive/begin/fun(...) nesting
    seen_arrow = False
    start = 0
    i = 0
    n = len(form)
    while i < n:
        ch = form[i]
        if ch.isalpha():
            j = i
            while j < n and (form[j].isalnum() or form[j] == "_"):
                j += 1
            word = form[i:j]
            if word in BLOCK_OPEN:
                block += 1
            elif word == "fun":
                k = j
                while k < n and form[k] in " \t\n":
                    k += 1
                if k < n and form[k] == "(":
                    block += 1
                elif k < n and (form[k].isalpha()):
                    m = k
                    while m < n and (form[m].isalnum() or form[m] == "_"):
                        m += 1
                    p = m
                    while p < n and form[p] in " \t\n":
                        p += 1
                    if p < n and form[p] == "(":
                        block += 1
            elif word == "end" and block > 0:
                block -= 1
            i = j
            continue
        two = form[i:i + 2]
        if two == "->" and depth == 0 and block == 0:
            seen_arrow = True
            i += 2
            continue
        if two == "<<":
            depth += 1
            i += 2
            continue
        if two == ">>":
            depth -= 1
            i += 2
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0 and block == 0 and seen_arrow:
            clauses.append(form[start:i])
            start = i + 1
            seen_arrow = False
        i += 1
    clauses.append(form[start:])
    return clauses


HEAD_RE = re.compile(r"^\s*([a-z][A-Za-z0-9_]*)\s*\(")


def function_heads(path, form, form_line):
    """Parse one function form -> (list_of (name, arity, line), ok)."""
    heads = []
    ok = True
    for clause in split_clauses(form):
        stripped = clause.strip()
        if not stripped:
            err_line(path, form_line, "empty clause (stray ';')")
            ok = False
            continue
        m = HEAD_RE.match(clause)
        if not m:
            tail = stripped.splitlines()[0][:50]
            err_line(path, form_line,
                     "clause does not start with name(...): %r" % tail)
            ok = False
            continue
        name = m.group(1)
        open_pos = clause.index("(", m.end(1))
        close = paren_match(clause, open_pos)
        if close < 0:
            err_line(path, form_line,
                     "unterminated parameter list in %s" % name)
            ok = False
            continue
        arity = count_arity(clause[open_pos + 1:close - 1])
        rest = clause[close:].lstrip()
        if not (rest.startswith("->") or rest.startswith("when")):
            err_line(path, form_line,
                     "clause head for %s/%d not followed by '->' or 'when'"
                     % (name, arity))
            ok = False
            continue
        heads.append((name, arity, form_line))
    if len(heads) > 1:
        first = (heads[0][0], heads[0][1])
        for name, arity, _line in heads[1:]:
            if (name, arity) != first:
                err_line(path, form_line,
                         "clause for %s/%d mixed into form of %s/%d "
                         "(missing '.' between functions?)"
                         % (name, arity, first[0], first[1]))
                ok = False
    return heads, ok


EXPORT_RE = re.compile(r"^-export\(\s*\[(.*?)\]\s*\)\s*\.", re.S)
SPEC_RE = re.compile(r"^-spec\s+([a-z][A-Za-z0-9_]*)\s*\(")
MODULE_RE = re.compile(r"^-module\(\s*([a-z][A-Za-z0-9_]*)\s*\)\s*\.")
ATTR_RE = re.compile(
    r"^-(module|export|export_type|spec|type|opaque|callback|record|define|"
    r"ifdef|ifndef|else|endif|include|include_lib|behaviour|behavior|compile|"
    r"vsn|dialyzer|file|on_load|nifs|import|opaque)\b")
ENTRY_RE = re.compile(r"([a-z][A-Za-z0-9_]*)\s*/\s*(\d+)")


def check_erl_module(path, raw):
    if not check_ascii(path, raw):
        return
    text = blank(raw)
    if not check_balance(path, text):
        return
    forms, ok = split_forms(path, text)
    if not ok:
        return

    defined = {}       # (name, arity) -> line
    exported = []      # (name, arity, line)
    specs = []         # (name, arity, line)
    module_name = None
    eunit_module = 'eunit/include/eunit.hrl' in raw

    for raw_form, raw_fline in forms:
        # leading comment/blank space was blanked to spaces; re-anchor
        # both the classifier and the line number to the real content
        stripped = raw_form.lstrip()
        lead = raw_form[:len(raw_form) - len(stripped)].count("\n")
        form = stripped
        fline = raw_fline + lead
        if not form:
            continue
        if form.startswith("-"):
            m = MODULE_RE.match(form)
            if m:
                module_name = m.group(1)
                continue
            m = EXPORT_RE.match(form)
            if m:
                for name, arity in ENTRY_RE.findall(m.group(1)):
                    exported.append((name, int(arity), fline))
                continue
            m = SPEC_RE.match(form)
            if m:
                name = m.group(1)
                open_pos = form.index("(", m.start(1) + len(name) - 1)
                close = paren_match(form, open_pos)
                if close < 0:
                    err_line(path, fline, "unterminated -spec for %s" % name)
                    continue
                arity = count_arity(form[open_pos + 1:close - 1])
                specs.append((name, arity, fline))
                continue
            if not ATTR_RE.match(form):
                first = form.strip().splitlines()[0][:60]
                err_line(path, fline, "unrecognised attribute/form: %r" % first)
            continue

        heads, _ok = function_heads(path, form, fline)
        # clauses inside one form legitimately share name/arity; only a
        # repeat across *separate* forms is an error (Erlang: clauses of
        # one function must be consecutive in a single form)
        seen_in_form = {}
        for name, arity, line in heads:
            seen_in_form.setdefault((name, arity), line)
        for key, line in seen_in_form.items():
            if key in defined:
                err_line(path, line,
                         "duplicate definition of %s/%d (first at line %d)"
                         % (key[0], key[1], defined[key]))
            defined.setdefault(key, line)

    base = os.path.basename(path)[:-4]
    if module_name is None:
        err_line(path, 1, "missing -module attribute")
    elif module_name != base:
        err_line(path, 1,
                 "-module(%s) does not match file name %s.erl"
                 % (module_name, base))

    defined_set = set(defined)
    spec_set = set((n, a) for n, a, _ in specs)

    for name, arity, line in exported:
        if (name, arity) not in defined_set:
            err_line(path, line,
                     "exported function %s/%d has no matching function head"
                     % (name, arity))
        if (name, arity) not in spec_set:
            err_line(path, line,
                     "exported function %s/%d has no -spec" % (name, arity))
    for name, arity, line in specs:
        if (name, arity) not in defined_set:
            # The EUnit parse transform generates test/0 in every module
            # that includes eunit.hrl, so the head is never in the source.
            if name == "test" and arity == 0 and eunit_module:
                continue
            err_line(path, line,
                     "-spec for %s/%d has no matching function" % (name, arity))
    if not specs:
        warn(path, text, 0, "no -spec found in this module")

    export_set = set((n, a) for n, a, _ in exported)

    return {"module": module_name, "defined": defined_set,
            "exported": export_set, "specs": spec_set}


def check_term_file(path, raw):
    """Erlang terms file: ascii, balance, terminated, non-empty."""
    if not check_ascii(path, raw):
        return
    text = blank(raw)
    check_balance(path, text)
    if not text.strip():
        err(path, raw, 0, "file is empty")
        return
    if not text.rstrip().endswith("."):
        err(path, raw, 0, "file does not end with '.'")


def parse_list_of_atoms(raw, key):
    m = re.search(r"\{\s*" + key + r"\s*,\s*\[(.*?)\]\s*\}", raw, re.S)
    if not m:
        return None
    return re.findall(r"[a-z][A-Za-z0-9_]*", m.group(1))


def check_app_src(path, raw, src_dir):
    check_term_file(path, raw)
    base = os.path.basename(path)[: -len(".app.src")]
    m = re.search(r"\{\s*application\s*,\s*([a-z][A-Za-z0-9_]*)", raw)
    if not m:
        err(path, raw, 0, "missing {application, Name, ...}")
    elif m.group(1) != base:
        err(path, raw, 0,
            "application name %s does not match file name %s.app.src"
            % (m.group(1), base))

    required = {"kernel", "stdlib", "sasl", "amqp_client", "rabbit_common",
                "prometheus"}
    apps = parse_list_of_atoms(raw, "applications")
    if apps is None:
        err(path, raw, 0, "missing {applications, [...]}")
    else:
        missing = required - set(apps)
        if missing:
            err(path, raw, 0,
                "{applications, ...} missing: %s" % ", ".join(sorted(missing)))

    modules = parse_list_of_atoms(raw, "modules")
    on_disk = set()
    if os.path.isdir(src_dir):
        for fn in sorted(os.listdir(src_dir)):
            if fn.endswith(".erl"):
                on_disk.add(fn[:-4])
    if modules is None:
        err(path, raw, 0, "missing {modules, [...]}")
    else:
        mod_set = set(modules)
        if mod_set != on_disk:
            only_disk = on_disk - mod_set
            only_app = mod_set - on_disk
            detail = []
            if only_disk:
                detail.append("on disk but not listed: %s"
                              % ", ".join(sorted(only_disk)))
            if only_app:
                detail.append("listed but not on disk: %s"
                              % ", ".join(sorted(only_app)))
            err(path, raw, 0, "{modules, ...} out of sync - " + "; ".join(detail))

    registered = parse_list_of_atoms(raw, "registered")
    if not registered:
        err(path, raw, 0, "missing or empty {registered, [...]}")


def main(argv):
    root = argv[1] if len(argv) > 1 else "."
    src_dir = os.path.join(root, "src")
    test_dir = os.path.join(root, "test")

    erl_files = []
    for d in (src_dir, test_dir):
        if os.path.isdir(d):
            erl_files += [os.path.join(d, f) for f in sorted(os.listdir(d))
                          if f.endswith(".erl")]
    if not erl_files:
        print("check_erl.py: no .erl files found under %s" % root)
        return 1

    modules = 0
    for path in sorted(erl_files):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        check_erl_module(path, raw)
        modules += 1

    for rel in ("rebar.config", os.path.join("config", "sys.config")):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                check_term_file(path, fh.read())
        else:
            err(path, "", 0, "required file missing")

    app_src = os.path.join(src_dir, "message_broker_backend.app.src")
    if os.path.exists(app_src):
        with open(app_src, "r", encoding="utf-8", errors="replace") as fh:
            check_app_src(app_src, fh.read(), src_dir)
    else:
        err(app_src, "", 0, "required file missing")

    for message in ERRORS:
        print(message)
    for message in WARNINGS:
        print(message)
    print("check_erl.py: %d .erl files, %d app.src, %d errors, %d warnings"
          % (modules, 1 if os.path.exists(app_src) else 0,
             len(ERRORS), len(WARNINGS)))
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
