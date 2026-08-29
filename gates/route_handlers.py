"""Emit a vulture whitelist of every HTTP route handler in a repo.

Why this exists
---------------
vulture is scope-blind name matching, which is exactly why it catches L003:
a function nobody calls by name is unwired code. On a web application that
same property inverts. Every route handler is registered by decorator and
invoked by the framework, never by name, so vulture reports the entire API
as dead.

The same inversion happens without a decorator when a stdlib framework
dispatches by name on a subclass. FinDigger's factcheck.py subclasses
html.parser.HTMLParser, whose handle_starttag/handle_endtag/handle_data are
called by feed(), invisibly to vulture; its webapp.py subclasses
http.server.BaseHTTPRequestHandler, which dispatches do_GET/do_POST off the
request verb. Before 2026-08-10 those names could only land by growing the
vulture BASELINE under a logged ACCEPT_DEBT - the wrong bucket, since they
are framework-reached, exactly what this whitelist exists for (WP-450).
FRAMEWORK_OVERRIDES below fixes that, keyed by base class so an unrelated
method name never rides along.

Measured on 2026-08-04, before installing anything:

    Reachly     152 vulture findings, 80 of them route handlers
    Tad          12+ findings, all of them route handlers
    WineCellar   16 findings, the majority route handlers

So L003 is not installable on any web repo she owns until those names are
whitelisted. Grandfathering them into the baseline instead would be worse:
the baseline ratchet only prevents growth, so every NEW endpoint would block
its own commit as unwired code, and the gate would be silenced weekly until
the kill rule deleted it. That is the failure this file exists to prevent.

Deliberately static
-------------------
This reads source with ast and never imports the target repo. Importing would
need each repo's virtualenv, its environment variables and a working database
URL, and would run application code as a side effect of a lint step. Parsing
needs none of that, so one command works against any repo on this machine
whether or not it has been set up.

The cost of that choice is honest: a route registered dynamically, by calling
add_api_route or add_url_rule in a loop rather than with a decorator, is
invisible here. Nothing in her repos does that today. If one starts, this file
under-reports and the missing names show up as vulture findings, which is the
safe direction to fail - noisy, not silent.

Usage
-----
    python gates/route_handlers.py <repo-path> [<source-subdir> ...]
    python gates/route_handlers.py C:/Files/Code/Tad backend

Prints the whitelist to stdout. Redirect it into the target repo's
gates/vulture_whitelist.py, or use --check to verify an existing one is current.
"""

import ast
import sys
from pathlib import Path

# Decorator attributes that register an HTTP handler. `route` covers Flask and
# Blueprint; the verbs cover FastAPI, APIRouter and Starlette. Matching on the
# attribute name rather than the object it hangs off is what makes this work
# for `@app.get`, `@router.get` and `@bp.route` without being told which is
# which.
ROUTE_ATTRS = {
    # Routes
    "route", "get", "post", "put", "patch", "delete", "head", "options",
    "websocket",
    # Lifecycle and error hooks. These are registered exactly like routes and
    # called exactly like routes - by the framework, never by name - so leaving
    # them out is not a smaller whitelist, it is a wrong one. Found by running
    # this file against WineCellar on 2026-08-04: `same_origin_guard`, a live
    # CSRF control on @app.before_request, was reported as unwired code. Acting
    # on that finding would have deleted her cross-site POST protection.
    "before_request", "after_request", "teardown_request",
    "before_first_request", "errorhandler", "context_processor",
    "template_filter", "template_global", "middleware", "exception_handler",
    "on_event",
}

SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git",
             ".pytest_cache", ".ruff_cache", "site-packages"}

# Enum bases. A member is reached as Colour("red") or Colour.red.value, so the
# name on the left of the assignment is frequently never written anywhere else
# and vulture calls it an unused variable. Same inversion as a route handler:
# something other than your code does the lookup.
#
# Added 2026-08-04 after measuring Reachly's baseline: 23 of its 49 grandfathered
# `variable` findings were enum members, which meant adding one new FactKind
# would have blocked its own commit. That is precisely the death spiral this
# file's docstring describes for endpoints.
ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}

# NOT extended to ORM columns or pydantic settings fields, and the line is
# deliberate. An enum member is unreachable by name by construction. A column
# that appears nowhere else really is an unused column - Reachly has eight of
# them, including vault.note_count and vault.last_indexed_at, which are written
# by a script and read by nothing. Whitelisting that family would suppress
# exactly the findings L003 exists to surface, which is the same mistake as
# reporting a live CSRF guard as dead, pointed the other way.

# Stdlib frameworks that dispatch by name on a subclass. The key is the base
# class name a subclass writes (matched terminal-name style, same tradeoff as
# ENUM_BASES); the value is the set of override methods that framework
# documents as dispatch targets. Keyed by base so an unrelated method name
# never rides along: `handle_data` on a class that does not subclass
# HTMLParser stays visible to vulture, and a private helper on a class that
# does is not whitelisted either.
#
# Deliberately short. Extend it when a real repo hits a real framework, the
# way HTMLParser and BaseHTTPRequestHandler arrived here from FinDigger
# (WP-450, 2026-08-10) - not speculatively. A framework missing from this
# table under-reports and the names show up as vulture findings, the same
# noisy-not-silent failure direction the docstring promises for dynamic
# routes.
FRAMEWORK_OVERRIDES: dict[str, frozenset[str]] = {
    # html.parser - feed() calls these per token.
    "HTMLParser": frozenset({
        "handle_starttag", "handle_endtag", "handle_startendtag",
        "handle_data", "handle_comment", "handle_decl", "handle_pi",
        "handle_entityref", "handle_charref", "unknown_decl",
    }),
    # http.server - the verb methods are matched by _is_do_verb below, since
    # the dispatch target is do_<COMMAND>, not a fixed name. The log_* hooks
    # are called by the base class on every request.
    "BaseHTTPRequestHandler": frozenset({"log_message", "log_request",
                                         "log_error"}),
    "SimpleHTTPRequestHandler": frozenset({"log_message", "log_request",
                                           "log_error"}),
    "CGIHTTPRequestHandler": frozenset({"log_message", "log_request",
                                        "log_error"}),
    # socketserver - the server instantiates the handler and calls these.
    "BaseRequestHandler": frozenset({"setup", "handle", "finish"}),
    "StreamRequestHandler": frozenset({"setup", "handle", "finish"}),
    "DatagramRequestHandler": frozenset({"setup", "handle", "finish"}),
    # unittest - the runner calls the fixture hooks, never the test author.
    "TestCase": frozenset({"setUp", "tearDown", "setUpClass",
                           "tearDownClass"}),
}

# Bases whose subclasses are dispatched via do_<COMMAND> off the request verb.
DO_VERB_BASES = {"BaseHTTPRequestHandler", "SimpleHTTPRequestHandler",
                 "CGIHTTPRequestHandler"}

# A repo's OWN registration decorator, listed by the repo rather than by this
# file. Same inversion as a route: the class is put in a registry by the
# decorator and looked up by key, so nothing ever calls it by name and vulture
# reports the whole plugin family as unwired code.
#
# Why a per-repo file rather than a constant here. ROUTE_ATTRS can be a fixed
# set because `get` and `route` are framework vocabulary shared across repos.
# `register_source` is PolicyPulse's own word; hard-coding one project's
# private name into ring canonical would be wrong, and hard-coding a generic
# name like `register` would whitelist anything anywhere that borrowed it.
#
# Arrived from PolicyPulse on 2026-08-28: all 24 of its structured source
# classes sat in the vulture BASELINE as accepted debt, and every new source
# needed PROOFMARK_ACCEPT_DEBT to land - the same death spiral this file's
# docstring describes for endpoints, and the wrong bucket for the same reason
# (WP-450). The baseline is for debt; this is a registry lookup.
#
# Absent file means absent section: a repo that does not list a registrar gets
# byte-identical output to before this existed, so no ring install's whitelist
# moves without that repo asking for it.
REGISTRARS_FILE = Path("gates") / "registrars.txt"


def _is_do_verb(name: str) -> bool:
    """do_GET yes, do_something no. The suffix is an HTTP verb, and verbs are
    uppercase; requiring that keeps a helper that merely starts with do_ from
    riding into the whitelist."""
    return name.startswith("do_") and name[3:].isupper()


def _decorator_attr(node: ast.AST) -> str | None:
    """The attribute name of a decorator, whether or not it is called."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _parsed_files(root: Path, subdirs: list[str]):
    """Yield (tree, path-relative-to-root) for every parseable .py file.

    A source root may be a single FILE as well as a directory - FinDigger
    declares `run.py` as a root, and rglob on a file yields nothing, so every
    scan here silently dropped it. Same defect gate.py fixed for SRC_DIRS
    (`exists()`, not `is_dir()`); fixed once here so all three scans agree.
    """
    roots = [root / s for s in subdirs] if subdirs else [root]
    for base in roots:
        if not base.exists():
            continue
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            if path.suffix != ".py":
                continue
            if SKIP_DIRS & set(path.relative_to(root).parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                # A file that cannot be parsed is not a file with no routes.
                # Say so and keep going rather than silently contributing zero.
                print(f"# WARNING unparseable, not scanned: {path} ({exc})",
                      file=sys.stderr)
                continue
            yield tree, str(path.relative_to(root))


def route_handlers(root: Path, subdirs: list[str]) -> list[tuple[str, str, int]]:
    """(function name, file relative to root, line) for every routed function."""
    found = []
    for tree, rel in _parsed_files(root, subdirs):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(_decorator_attr(d) in ROUTE_ATTRS for d in node.decorator_list):
                found.append((node.name, rel, node.lineno))
    return sorted(set(found))


def _is_enum(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if getattr(base, "id", None) in ENUM_BASES:
            return True
        if getattr(base, "attr", None) in ENUM_BASES:  # enum.Enum
            return True
    return False


def enum_members(root: Path, subdirs: list[str]) -> list[tuple[str, str, int]]:
    """(member name, file relative to root, line) for every enum member."""
    found = []
    for tree, rel in _parsed_files(root, subdirs):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_enum(node):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                    continue
                target = stmt.targets[0]
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    found.append((target.id, rel, stmt.lineno))
    return sorted(set(found))


def _framework_bases(node: ast.ClassDef) -> list[str]:
    """The FRAMEWORK_OVERRIDES keys this class directly subclasses.
    Terminal-name matching, like _is_enum: `HTMLParser` and
    `html.parser.HTMLParser` are both how real code writes it."""
    names = []
    for base in node.bases:
        name = getattr(base, "id", None) or getattr(base, "attr", None)
        if name in FRAMEWORK_OVERRIDES:
            names.append(name)
    return names


def framework_overrides(root: Path,
                        subdirs: list[str]) -> list[tuple[str, str, int, str]]:
    """(method name, file relative to root, line, base class) for every
    override method a known stdlib framework dispatches by name.

    Direct bases only, deliberately: resolving a repo's own subclass-of-a-
    subclass chains would need import-order knowledge this file refuses to
    have. If a repo grows such a chain, the grandchild's overrides surface as
    vulture findings - noisy, not silent, the documented failure direction.
    """
    found = []
    for tree, rel in _parsed_files(root, subdirs):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base_name in _framework_bases(node):
                allowed = FRAMEWORK_OVERRIDES[base_name]
                do_verbs = base_name in DO_VERB_BASES
                for stmt in node.body:
                    if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if stmt.name in allowed or (do_verbs and _is_do_verb(stmt.name)):
                        found.append((stmt.name, rel, stmt.lineno, base_name))
    return sorted(set(found))


def _registrars(root: Path) -> set[str]:
    """Decorator names this repo declares as registrars, or an empty set.

    One name per line, `#` comments and blanks ignored. An empty set means
    the registered-class scan contributes nothing at all, which is what keeps
    this change invisible to every repo that has not opted in.
    """
    path = root / REGISTRARS_FILE
    if not path.exists():
        return set()
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def _defined_function_names(root: Path, subdirs: list[str]) -> set[str]:
    """Every function defined in the scanned source.

    Used to check that a declared registrar is a real thing this repo wrote.
    Without it, `gates/registrars.txt` is a wishing well: write `dataclass`
    or `staticmethod` in it and a whole family of classes is forgiven with no
    argument behind it. This is the same instinct as keying FRAMEWORK_OVERRIDES
    on the base class - the entry has to point at something that exists.
    """
    names = set()
    for tree, _ in _parsed_files(root, subdirs):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
    return names


def _decorator_name(node: ast.AST) -> str | None:
    """A decorator's terminal name, called or not, bare or attributed.

    `@register_source`, `@register_source()` and `@registry.register_source`
    all answer `register_source` - terminal-name matching, the same tradeoff
    _framework_bases and _is_enum already make in this file.
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def registered_classes(root: Path,
                       subdirs: list[str]) -> list[tuple[str, str, int, str]]:
    """(class name, file relative to root, line, registrar) for every class
    a declared registration decorator puts into a registry.

    Classes only. A registered FUNCTION is deliberately out of scope: the
    plugin-registry inversion this fixes is a class put in a table and looked
    up by key, and widening it to functions would quietly cover a much larger
    surface than the argument supports.
    """
    declared = _registrars(root)
    if not declared:
        return []

    defined = _defined_function_names(root, subdirs)
    unknown = sorted(declared - defined)
    if unknown:
        print(
            f"# WARNING declared registrars not defined in this repo's source, "
            f"ignored: {unknown}. A registrar must be a decorator this repo "
            f"writes, not a borrowed name.",
            file=sys.stderr,
        )
    usable = declared & defined
    if not usable:
        return []

    found = []
    for tree, rel in _parsed_files(root, subdirs):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                name = _decorator_name(decorator)
                if name in usable:
                    found.append((node.name, rel, node.lineno, name))
    return sorted(set(found))


def render(handlers: list[tuple[str, str, int]], root: Path,
           members: list[tuple[str, str, int]] | None = None,
           overrides: list[tuple[str, str, int, str]] | None = None,
           registered: list[tuple[str, str, int, str]] | None = None) -> str:
    lines = [
        '"""vulture whitelist: names the framework reaches, not your code.',
        "",
        "GENERATED by Proofmark gates/route_handlers.py. Regenerate after adding",
        "routes, enum members, or framework subclasses; do not hand-edit. Every",
        "name here is reached by something other than a call by name - decorator",
        "registration for a route, value lookup for an enum member, by-name",
        "dispatch from a stdlib framework onto a subclass override - so vulture",
        "reports it as unwired code. Whitelisting is correct; grandfathering",
        "these into the vulture baseline instead would make every new endpoint,",
        "enum member, and framework override block its own commit.",
        "",
        "Deliberately NOT here: ORM columns and settings fields. A column that",
        "appears nowhere else is genuinely an unused column, and hiding that would",
        "suppress the findings L003 exists to surface.",
        '"""',
        "",
        "# route handlers",
    ]
    for name, rel, line in handlers:
        lines.append(f"{name}  # {rel}:{line}")
    if members:
        lines += ["", "# enum members"]
        for name, rel, line in members:
            lines.append(f"{name}  # {rel}:{line}")
    if overrides:
        lines += ["", "# framework overrides"]
        for name, rel, line, base in overrides:
            lines.append(f"{name}  # {rel}:{line} via {base}")
    if registered:
        lines += ["", "# registered classes"]
        for name, rel, line, registrar in registered:
            lines.append(f"{name}  # {rel}:{line} via @{registrar}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    check = "--check" in argv
    if not args:
        print(__doc__)
        return 2

    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    handlers = route_handlers(root, args[1:])
    members = enum_members(root, args[1:])
    overrides = framework_overrides(root, args[1:])
    registered = registered_classes(root, args[1:])
    if not handlers:
        # Zero is a real answer for a non-web repo, but it is also what a broken
        # matcher returns, and those must not look alike.
        print(f"# no route handlers found under {root}. If this repo serves HTTP, "
              f"the decorators are not in ROUTE_ATTRS: {sorted(ROUTE_ATTRS)}",
              file=sys.stderr)

    text = render(handlers, root, members, overrides, registered)
    if check:
        target = root / "gates" / "vulture_whitelist.py"
        if not target.exists():
            print(f"MISSING: {target}", file=sys.stderr)
            return 1
        if target.read_text(encoding="utf-8") != text:
            print(f"STALE: {target} does not match the current routes. "
                  f"Regenerate it.", file=sys.stderr)
            return 1
        print(f"OK: {target} lists all {len(handlers)} route handlers, "
              f"{len(members)} enum members, {len(overrides)} framework "
              f"overrides, and {len(registered)} registered classes")
        return 0

    sys.stdout.write(text)
    print(f"# {len(handlers)} route handlers, {len(members)} enum members, "
          f"{len(overrides)} framework overrides",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
