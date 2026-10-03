#!/usr/bin/env python3
"""Extract functions/methods from a source file using tree-sitter.

Emits one JSON object per function on stdout (JSON Lines):

    {"name":"foo","kind":"method","startLine":2,"endLine":5,"code":"..."}

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
            "(struct_specifier name: (type_identifier) @name body: (field_declaration_list)) @fn\n"
            "\n"
            "(union_specifier name: (type_identifier) @name body: (field_declaration_list)) @fn\n"
            "\n"
            "(enum_specifier name: (type_identifier) @name body: (enumerator_list)) @fn"
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
        "kinds": ["function", "class"],
        "query": (
            "(function_definition\n"
            "  name: (identifier) @name) @fn\n"
            "\n"
            "(class_definition\n"
            "  name: (identifier) @name) @fn"
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
    offsets = [0]
    idx = source.find("\n")
    while idx != -1:
        offsets.append(idx + 1)
        idx = source.find("\n", idx + 1)
    return offsets


def extract_text(source, offsets, sr, sc, er, ec):
    start = offsets[sr] + sc
    end = offsets[er] + ec
    return source[start:end]


def parse_query_output(output, source, cfg):
    offsets = line_offsets(source)
    functions = []
    cur = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pm = PATTERN_RE.search(stripped)
        if pm:
            if cur.get("fn_pos"):
                functions.append(_materialize(cur, source, offsets, cfg))
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
    if cur.get("fn_pos"):
        functions.append(_materialize(cur, source, offsets, cfg))
    return functions


def _materialize(cur, source, offsets, cfg):
    sr, sc, er, ec = cur["fn_pos"]
    code = extract_text(source, offsets, sr, sc, er, ec)
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
        source = open(path, encoding="utf-8").read()
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
