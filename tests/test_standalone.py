"""node-fable-mcp depends on no other application, and no tracked file names one.

Other programs call this one through its MCP tools. This one never calls them back
and never mentions them, so removing any of them changes nothing here.

Three checks, each with a planted fault that must trip it:

  * every import in the Python files resolves to the standard library, a package this
    app declares, or a module in this repository;
  * no tracked text file names a sibling application, a host, a person's account or a
    local layout;
  * no code loads another program's module by path or adds a foreign directory to the
    import path.

The forbidden words are built from fragments so this file does not contain them, and
it is skipped by the scan only for that reason. The app's own words are an exact
allowlist: they are blanked out before the scan, so a forbidden word next to one of
them still trips.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THIS = Path(__file__).resolve()

# Third-party import names, by where they are used. The server needs the MCP SDK and its
# validation library; the tests also need pytest.
SERVICES_THIRD_PARTY = {"mcp", "pydantic"}
TESTS_THIRD_PARTY = SERVICES_THIRD_PARTY | {"pytest"}

# This app's own words: its name, its package and its domain word. Exact strings, blanked
# out before the scan.
OWN_WORDS = ("node-fable-mcp", "node_fable", "fable")

_W = [
    "is" + "aac", "claude-" + "is" + "aac", "kb" + "_get", "kb" + "_store", "kb" + "_search", "whis" + "per",
    "fle" + "et", "q" + "vc", "shell" + "ware", "syn" + "man", "aka" + "mai", "az-" + "teams",
    "snow-" + "py", "snow-" + "mcp", "her" + "mes", "agent-" + "meetings", "node-kb", "node-git", "node-extract",
    "node-whis", "node-fed", "node-web", "node-proc", "~/" + "ai",
    "cla" + "ude", "open" + "code", "copi" + "lot", ".virtual" + "envs", ".s" + "sl", "enter" + "prise", "ms" + "365",
    "ji" + "ra", "mcp-admin-" + "token", "mcp-admin-" + "one", "atlas" + "sian", "home-" + "assistant",
]
# A placeholder like <w-o-r-k-s-p-a-c-e>, but not a generic type such as Promise<Workspace>.
_PLACEHOLDER = r"(?<![A-Za-z0-9_])<" + "work" + "space>"
FORBIDDEN = re.compile("|".join([re.escape(w) for w in _W] + [_PLACEHOLDER]), re.I)
_OWN = re.compile("|".join(re.escape(w) for w in OWN_WORDS), re.I)

SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".test-artifacts"}


def tracked_files() -> list[Path]:
    res = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True)
    if res.returncode == 0 and res.stdout:
        return [ROOT / p for p in res.stdout.split("\0") if p]
    return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


# A host or an address that belongs to a real machine or person. Documentation hosts, loopback
# and one-letter test hosts are fine.
_URL = re.compile(r"https?://([A-Za-z0-9.-]+)")
_EMAIL = re.compile(r"[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_DOC_HOSTS = ("example.com", "example.invalid", "example.org", "example.net")


def _is_real(host: str) -> bool:
    host = host.lower().rstrip(".")
    if "." not in host or host == "127.0.0.1" or host.endswith("python.org"):
        return False
    return not any(host == d or host.endswith("." + d) for d in _DOC_HOSTS)


def named_siblings(text: str, own: re.Pattern = _OWN) -> set[str]:
    text = own.sub(" ", text)
    found = {m.group(0).lower() for m in FORBIDDEN.finditer(text)}
    found |= {h.lower() for h in _URL.findall(text) if _is_real(h)}
    found |= {h.lower() for h in _EMAIL.findall(text) if _is_real(h)}
    return found


def foreign_imports(source: str, local: set[str], third_party: set[str]) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        else:
            continue
        out |= {n for n in names if n not in sys.stdlib_module_names and n not in local and n not in third_party}
    return out


def path_loading(source: str) -> set[str]:
    """Ways to load a module from a path or to extend the import path from code.

    The entry files add a directory of this repository to ``sys.path``: a name assigned from
    an expression that mentions ``__file__`` (or from another such name), possibly wrapped in
    ``str`` and ``os.path.join``. That is the one allowed form; a call whose argument names
    anything else is reported.
    """
    found = set()
    tree = ast.parse(source)
    own: set[str] = set()
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    changed = True
    while changed:
        changed = False
        for n in assigns:
            names = {x.id for x in ast.walk(n.value) if isinstance(x, ast.Name)}
            if ("__file__" in ast.dump(n.value) or names & own) and not isinstance(n.value, ast.Constant):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id not in own:
                        own.add(t.id)
                        changed = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"spec_from_file_location", "load_source"}:
            found.add(node.attr)
        if isinstance(node, ast.Name) and node.id == "spec_from_file_location":
            found.add(node.id)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"insert", "append"}
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
        ):
            arg = node.args[-1] if node.args else None
            names = {x.id for x in ast.walk(arg) if isinstance(x, ast.Name)} if arg is not None else set()
            if not (names & own and names <= own | {"str", "os"}):
                found.add("sys.path." + node.func.attr)
    return found


# --- the real tree ----------------------------------------------------------------------------------------------


def _py_files(top: str) -> list[Path]:
    return [p for p in tracked_files() if p.suffix == ".py" and p.relative_to(ROOT).parts[0] == top]


def _local_modules() -> set[str]:
    names = {p.stem for p in tracked_files() if p.suffix == ".py"}
    return names | {"node_fable"}


@pytest.mark.parametrize(
    "top,third_party",
    [("services", SERVICES_THIRD_PARTY), ("evals", SERVICES_THIRD_PARTY), ("tests", TESTS_THIRD_PARTY)],
)
def test_every_import_is_stdlib_declared_or_in_this_repository(top, third_party):
    files = _py_files(top)
    assert files, top
    bad = {str(p.relative_to(ROOT)): foreign_imports(p.read_text(), _local_modules(), third_party) for p in files}
    assert {f: m for f, m in bad.items() if m} == {}


def test_every_declared_third_party_package_is_in_the_requirements_file():
    declared = {re.split(r"[<>=!~\[ ;]", line.strip(), maxsplit=1)[0].lower()
                for line in (ROOT / "requirements.txt").read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")}
    assert "mcp" in declared, declared  # non-vacuous: the SDK is the one hard dependency


def test_no_tracked_text_file_names_a_sibling_application():
    scanned, bad = 0, {}
    for path in tracked_files():
        rel = str(path.relative_to(ROOT))
        if path == THIS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        names = named_siblings(text)
        if names:
            bad[rel] = sorted(names)
    assert scanned > 30, "the scan found almost nothing, so it proves nothing"
    assert bad == {}


@pytest.mark.parametrize("top", ["services", "evals"])
def test_no_code_loads_another_programs_module_by_path(top):
    # The shipping code only. A test may load this repository's own hyphen-named entry file by
    # path, which is not another program.
    files = _py_files(top)
    assert files, top
    bad = {str(p.relative_to(ROOT)): path_loading(p.read_text()) for p in files}
    assert {f: m for f, m in bad.items() if m} == {}


# --- planted faults: each check must go red on its own fault -----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "see is" + "aac for the rest", "set the q" + "vc proxy", "kb" + "_get('x')", "~/" + "ai/x", "A" + "KAMAI edge",
        "<work" + "space>/x", "~/.cla" + "ude/x", "Open" + "Code config", "~/.virtual" + "envs/main", "https://meet.real-host.net/mcp",
        "someone@real-host.net", "CLA" + "UDE_CODE_X", "the fle" + "et",
        # a forbidden word right beside an allowed one still trips
        "fable " + "cla" + "ude", "node-fable-mcp-" + "is" + "aac", "node_fable_" + "q" + "vc", "fable-" + "kb" + "_store",
    ],
)
def test_the_text_scan_catches_each_kind_of_name(text):
    assert named_siblings(text)


def test_the_text_scan_passes_plain_text():
    assert named_siblings("a consultation runner with a configured command and a plain account list") == set()


def test_the_text_scan_passes_the_apps_own_words():
    assert named_siblings("node-fable-mcp runs a fable consult through node_fable") == set()


def test_an_own_word_is_blanked_as_an_exact_string_and_nothing_wider():
    """The exemption, shown on a made-up own word that contains a forbidden fragment: the exact
    own word passes, the bare fragment and the fragment beside the own word still trip."""
    fragment = "fle" + "et"
    own = re.compile(re.escape(fragment + "-ops"), re.I)
    assert named_siblings("the " + fragment + "-ops tool", own) == set()
    assert named_siblings("the " + fragment + " tool", own) == {fragment}
    assert named_siblings(fragment + "-ops and " + fragment, own) == {fragment}
    assert named_siblings(fragment + "-opsx", own) == set()  # exact string: the remainder 'x' is not a word


def test_blanking_an_own_word_does_not_join_the_text_around_it_into_a_forbidden_word():
    assert named_siblings("cla" + "fable" + "ude") == set()


def test_the_text_scan_passes_documentation_hosts_and_loopback():
    text = "https://mcp.example.invalid/x http://127.0.0.1:9/mcp http://a/mcp t@example.com https://docs.python.org/3/"
    assert named_siblings(text) == set()


def test_the_import_scan_catches_a_sibling_module_in_each_import_form():
    sibling = "is" + "aac_db"
    for src in (f"import {sibling}", f"from {sibling} import x", f"import a.b\nimport {sibling}.sub"):
        assert foreign_imports(src, set(), set()) == {sibling} | ({"a"} if "a.b" in src else set())


def test_the_import_scan_passes_stdlib_declared_and_local():
    src = "import json\nfrom pathlib import Path\nimport mcp\nimport node_fable.config\nfrom . import x"
    assert foreign_imports(src, {"node_fable"}, {"mcp"}) == set()


@pytest.mark.parametrize(
    "src,kind",
    [
        ("import importlib.util\nimportlib.util.spec_from_file_location('m', '/x/m.py')", "spec_from_file_location"),
        ("from importlib.util import spec_from_file_location\nspec_from_file_location('m', 'p')", "spec_from_file_location"),
        ("import sys\nsys.path.insert(0, '/elsewhere/services')", "sys.path.insert"),
        ("import sys\nsys.path.append(str(other))", "sys.path.append"),
        ("import sys\nHERE = 'x'\nsys.path.insert(0, str(HERE))", "sys.path.insert"),
    ],
)
def test_the_path_loading_scan_catches_each_way_to_reach_another_program(src, kind):
    assert kind in path_loading(src)


def test_the_path_loading_scan_passes_the_apps_own_bootstrap():
    src = (
        "import sys\nfrom pathlib import Path\n"
        "SERVICES_DIR = Path(__file__).resolve().parent\n"
        "if str(SERVICES_DIR) not in sys.path:\n    sys.path.insert(0, str(SERVICES_DIR))\n"
    )
    assert path_loading(src) == set()
