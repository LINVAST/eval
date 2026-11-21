#!/usr/bin/env python3
"""Extract declarations separately, removing extracted children from parents.

Emits one JSON object per selected declaration on stdout (JSON Lines):

    {"name":"foo","type":"class","startLine":2,"endLine":5,"code":"..."}

Usage:
    extract-functions.py <lang> <file>
    extract-functions.py --langs        # list supported languages

Supported languages: c, go, java, lua, python, kotlin
"""

import json
import os
import re
import subprocess
import sys
import tempfile

SELF_DIR = os.path.dirname(os.path.abspath(__file__))
TS_BIN = os.environ.get("TREE_SITTER", "tree-sitter")

LANGUAGES = {
    "java": {
        "grammar_url": "https://github.com/tree-sitter/tree-sitter-java",
        "grammar_dir": "tree-sitter-java",
        "lang": "java",
        "kinds": ["constructor", "method", "class", "interface", "enum",
                  "annotation_type"],
        "query": (
            "(constructor_declaration\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(method_declaration\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(class_declaration\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(interface_declaration\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(enum_declaration\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(annotation_type_declaration\n"
            "  name: (identifier) @name) @fn"
        ),
        "wrappers": {
            "default": "{code}\n",
            "constructor": "class Wrapper {\n{code}\n}\n",
            "method": "class Wrapper {\n{code}\n}\n",
        },
    },
    "go": {
        "grammar_url": "https://github.com/tree-sitter/tree-sitter-go",
        "grammar_dir": "tree-sitter-go",
        "lang": "go",
        "kinds": ["function", "method", "type"],
        "query": (
            "(function_declaration\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(method_declaration\n"
            "  name: (field_identifier) @name) @fn\n"
            "\n"
            "(type_declaration\n"
            "  (type_spec\n"
            "    name: (type_identifier) @name)) @fn"
        ),
        "wrappers": {"default": "package main\n{code}\n"},
    },
    "lua": {
        "grammar_url": "https://github.com/tree-sitter-grammars/tree-sitter-lua",
        "grammar_dir": "tree-sitter-lua",
        "lang": "lua",
        "kinds": ["function"],
        "query": (
            "(function_declaration\n"
            "  name: (identifier) @name) @fn"
        ),
        "wrappers": {"default": "{code}\n"},
    },
    "c": {
        "grammar_url": "https://github.com/tree-sitter/tree-sitter-c",
        "grammar_dir": "tree-sitter-c",
        "lang": "c",
        "kinds": ["function", "struct", "union", "enum"],
        "query": (
            "(function_definition\n"
            "  declarator: (function_declarator\n"
            "    declarator: (identifier) @name)) @fn\n"
            "\n"
            "(struct_specifier name: (type_identifier) @name body: (field_declaration_list) @body) @fn\n"
            "\n"
            "(union_specifier name: (type_identifier) @name body: (field_declaration_list) @body) @fn\n"
            "\n"
            "(enum_specifier name: (type_identifier) @name body: (enumerator_list) @body) @fn"
        ),
        "wrappers": {
            "default": "{code}\n",
            "struct": "{code};\n",
            "union": "{code};\n",
            "enum": "{code};\n",
        },
    },
    "python": {
        "grammar_url": "https://github.com/tree-sitter/tree-sitter-python",
        "grammar_dir": "tree-sitter-python",
        "lang": "python",
        "kinds": ["function", "class", "function", "class"],
        "query": (
            "(function_definition\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(class_definition\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(decorated_definition\n"
            "  definition: (function_definition name: (identifier) @name) @fn) @remove\n"
            "\n"
            "(decorated_definition\n"
            "  definition: (class_definition name: (identifier) @name) @fn) @remove"
        ),
        "wrappers": {"default": "{code}\n"},
    },
    "kotlin": {
        "grammar_url": "https://github.com/fwcd/tree-sitter-kotlin",
        "grammar_dir": "tree-sitter-kotlin",
        "lang": "kotlin",
        "kinds": ["function", "class", "object"],
        "query": (
            "(function_declaration\n"
            "  (simple_identifier) @name) @fn\n"
            "\n"
            "(class_declaration\n"
            "  (type_identifier) @name) @fn\n"
            "\n"
            "(object_declaration\n"
            "  (type_identifier) @name) @fn"
        ),
        "wrappers": {"default": "{code}\n"},
    },
}

CAPTURE_RE = re.compile(
    r"capture:\s*(?:\d+\s*-\s*)?(?P<cap>[A-Za-z_.]+)"
    r",\s*start:\s*\((?P<sr>\d+),\s*(?P<sc>\d+)\)"
    r",\s*end:\s*\((?P<er>\d+),\s*(?P<ec>\d+)\)"
)
PATTERN_RE = re.compile(r"pattern:\s*(\d+)")


def ensure_parser(cfg):
    lib = os.path.join(SELF_DIR, cfg["lang"] + ".so")
    if os.path.isfile(lib):
        return lib
    grammar = os.path.join(SELF_DIR, cfg["grammar_dir"])
    if not os.path.isdir(grammar):
        subprocess.run(
            ["git", "clone", "--depth", "1", cfg["grammar_url"], grammar],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    subprocess.run(
        [TS_BIN, "build", "-o", lib],
        cwd=grammar, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return lib


def line_offsets(source):
    """Return byte offsets of LF-delimited lines in the original source bytes."""
    offsets = [0]
    idx = source.find(b"\n")
    while idx != -1:
        offsets.append(idx + 1)
        idx = source.find(b"\n", idx + 1)
    return offsets


def extract_text(source, offsets, sr, sc, er, ec):
    """Slice Tree-sitter byte coordinates before decoding the captured text."""
    start = offsets[sr] + sc
    end = offsets[er] + ec
    return source[start:end].decode("utf-8")


def parse_query_output(output, source, cfg):
    offsets = line_offsets(source)
    matches = []
    cur = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pm = PATTERN_RE.search(stripped)
        if pm:
            if cur.get("fn_pos"):
                matches.append(cur)
            cur = {"pat": int(pm.group(1))}
            continue
        m = CAPTURE_RE.search(line)
        if not m:
            continue
        cap = m.group("cap")
        sr, sc, er, ec = (int(m.group(g)) for g in ("sr", "sc", "er", "ec"))
        if cap == "name":
            cur["name_pos"] = (sr, sc, er, ec)
        elif cap == "fn":
            cur["fn_pos"] = (sr, sc, er, ec)
        elif cap in ("remove", "body"):
            cur[cap + "_pos"] = (sr, sc, er, ec)
    if cur.get("fn_pos"):
        matches.append(cur)
    return [_materialize(match, source, offsets, cfg)
            for match in declaration_matches(matches)]


def declaration_matches(matches):
    """Keep each range once and attach directly contained declarations.

    Compare byte-column points, not line numbers: sibling declarations may share
    a line. For repeated captures of one range (e.g. a grouped Go type declaration),
    use the first name in source order. Prefer a decorated capture's wider removal
    range so decorators are removed with the declaration from its parent.
    """
    def order(match):
        sr, sc, er, ec = match['fn_pos']
        rsr, rsc, rer, rec = match.get('remove_pos', match['fn_pos'])
        return sr, sc, -er, -ec, rsr, rsc, -rer, -rec, match.get('name_pos', ()), match.get('pat', 0)

    selected = []
    seen = set()
    parents = []
    for match in sorted(matches, key=order):
        sr, sc, er, ec = match['fn_pos']
        start, end = (sr, sc), (er, ec)
        if end <= start:
            raise ValueError('empty or reversed declaration range')
        if match['fn_pos'] in seen:
            continue
        seen.add(match['fn_pos'])
        match = {**match, 'children': []}
        while parents and start >= parents[-1]['fn_pos'][2:]:
            parents.pop()
        if parents:
            parent = parents[-1]
            if end > parent['fn_pos'][2:]:
                raise ValueError('declaration ranges overlap without containment')
            remove = match.get('remove_pos', match['fn_pos'])
            if remove[:2] < parent['fn_pos'][:2] or remove[2:] > parent['fn_pos'][2:]:
                raise ValueError('child removal range extends outside its parent')
            parent['children'].append(match)
        selected.append(match)
        parents.append(match)
    return selected


def _materialize(cur, source, offsets, cfg):
    sr, sc, er, ec = cur["fn_pos"]
    start, end = offsets[sr] + sc, offsets[er] + ec
    code = bytearray(source[start:end])
    for child in cur.get('children', []):
        # C nested type definitions can also declare a field. Keep its type
        # reference ("struct Inner field;") while removing the nested body.
        position = child.get('body_pos') if cfg.get('lang') == 'c' else None
        cs, cc, ce, ec_child = position or child.get('remove_pos', child['fn_pos'])
        child_start, child_end = offsets[cs] + cc, offsets[ce] + ec_child
        # Preserve byte positions and line endings; no child text remains here.
        code[child_start - start:child_end - start] = re.sub(
            rb'[^\r\n]', b' ', source[child_start:child_end]
        )
        if cfg.get('lang') == 'python':
            # Removing a definition may leave an empty suite, even inside an if
            # or try. A pass at the original def/class location keeps it valid.
            psr, psc, _, _ = child['fn_pos']
            placeholder = offsets[psr] + psc - start
            code[placeholder:placeholder + 4] = b'pass'
    code = code.decode('utf-8')
    name = None
    if "name_pos" in cur:
        nsr, nsc, ner, nec = cur["name_pos"]
        name = extract_text(source, offsets, nsr, nsc, ner, nec)
    kinds = cfg.get("kinds", ["function"])
    kind = kinds[cur.get("pat", 0)] if 0 <= cur.get("pat", 0) < len(kinds) else "function"
    wrappers = cfg.get("wrappers", {})
    tmpl = wrappers.get(kind, wrappers.get("default", "{code}\n"))
    return {
        "name": name,
        "type": kind,
        "startLine": sr + 1,
        "endLine": er + 1,
        "code": code,
        "wrapped": tmpl.replace("{code}", code),
    }


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.stderr.write("usage: {} <lang> <file>\n".format(sys.argv[0]))
        sys.stderr.write("       {} --langs\n".format(sys.argv[0]))
        return 2
    if args[0] == "--langs":
        print("\n".join(sorted(LANGUAGES)))
        return 0
    if len(args) != 2:
        sys.stderr.write("usage: {} <lang> <file>\n".format(sys.argv[0]))
        return 2
    lang, path = args
    if lang not in LANGUAGES:
        sys.stderr.write("unsupported language: {} (supported: {})\n".format(
            lang, ", ".join(sorted(LANGUAGES))))
        return 2
    cfg = LANGUAGES[lang]

    try:
        # Match the bytes read by Tree-sitter, including original line endings.
        with open(path, "rb") as source_file:
            source = source_file.read()
    except OSError as e:
        sys.stderr.write("cannot read {}: {}\n".format(path, e))
        return 2

    lib = ensure_parser(cfg)

    with tempfile.NamedTemporaryFile("w", suffix=".scm", delete=False) as qf:
        qf.write(cfg["query"])
        qpath = qf.name
    try:
        out = subprocess.run(
            [TS_BIN, "query", "--lib-path", lib, "--lang-name", cfg["lang"],
             qpath, path],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(qpath)

    if out.returncode != 0:
        sys.stderr.write(out.stderr)
        return 1

    for fn in parse_query_output(out.stdout, source, cfg):
        print(json.dumps(fn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
