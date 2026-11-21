# Evaluation of LINVAST AST builders on open-source repositories

Clone the repository, enter its directory, and run `eval` with the language or project paths to evaluate. The script initializes the `CLI` submodule it needs and downloads only the language submodules selected by those paths:

```sh
git clone <repository-url> eval
cd eval
./eval lang/python/requests       # one project; "python/requests" also works
./eval lang/c lang/go             # selected languages
./eval --whole-files lang/lua     # whole-file mode
./eval                            # all languages and their submodules
```

You do not need to clone with `--recurse-submodules`; `eval` fetches the required submodules on demand. Running it without paths selects every configured language project, so it downloads all configured language submodules. Declaration mode is the default. The corpus retains JDK11; JDK has been removed.

Each submodule must be recorded in the parent repository as a gitlink as well as listed in `.gitmodules`. If `eval` reports a requested submodule is not recorded, commit and push that submodule entry in the parent repository before running `eval` from a fresh clone.

The tables below are historical results from before child-declaration removal and
input deduplication. The JDK row has been removed and the Java subtotal recomputed
from the remaining historical rows. Current corpus totals and language proportions
require a fresh evaluation; these tables do not measure the new selection policy.

#### C

| Repository | Constructs | Passed | Failed | Pass% |
| ---------- | ---------- | ------ | ------ | ----- |
| 8cc | 277 | 270 | 7 | 97.5% |
| cmus | 1862 | 1655 | 207 | 88.9% |
| linenoise | 86 | 85 | 1 | 98.8% |
| miniml-parser | 25 | 21 | 4 | 84.0% |
| packcc | 225 | 190 | 35 | 84.4% |
| picoc | 603 | 591 | 12 | 98.0% |
| smalljsonparser | 24 | 24 | 0 | 100.0% |
| tinyexpr | 79 | 78 | 1 | 98.7% |
| **Total** | **3181** | **2914** | **267** | **91.6%** |

#### Go

| Repository | Constructs | Passed | Failed | Pass% |
| ---------- | ---------- | ------ | ------ | ----- |
| cobra | 285 | 262 | 23 | 91.6% |
| gum | 199 | 196 | 3 | 98.5% |
| hcl | 1034 | 1017 | 17 | 98.4% |
| lf | 409 | 386 | 23 | 94.4% |
| task | 904 | 801 | 103 | 88.6% |
| **Total** | **2831** | **2662** | **169** | **94.0%** |

#### Java

| Repository | Constructs | Passed | Failed | Pass% |
| ---------- | ---------- | ------ | ------ | ----- |
| antlr4 | 3623 | 3623 | 0 | 100.0% |
| graal | 140313 | 136112 | 4201 | 97.0% |
| javapoet | 454 | 449 | 5 | 98.9% |
| jdk11 | 253978 | 250807 | 3171 | 98.8% |
| **Total** | **398368** | **390991** | **7377** | **98.1%** |

#### Kotlin

| Repository | Constructs | Passed | Failed | Pass% |
| ---------- | ---------- | ------ | ------ | ----- |
| detekt | 2973 | 2821 | 152 | 94.9% |
| kotlin-benchmarks | 362 | 359 | 3 | 99.2% |
| kotlinx-benchmark | 688 | 517 | 171 | 75.2% |
| ktor | 11025 | 8876 | 2149 | 80.5% |
| okhttp | 6818 | 5986 | 832 | 87.8% |
| **Total** | **21866** | **18559** | **3307** | **84.9%** |

#### Lua

| Repository | Constructs | Passed | Failed | Pass% |
| ---------- | ---------- | ------ | ------ | ----- |
| bump | 25 | 25 | 0 | 100.0% |
| lite | 72 | 72 | 0 | 100.0% |
| lite-plugins | 58 | 58 | 0 | 100.0% |
| luarocks | 311 | 310 | 1 | 99.7% |
| middleclass | 9 | 9 | 0 | 100.0% |
| **Total** | **475** | **474** | **1** | **99.8%** |

#### Python

| Repository | Constructs | Passed | Failed | Pass% |
| ---------- | ---------- | ------ | ------ | ----- |
| click | 719 | 708 | 11 | 98.5% |
| flask | 461 | 443 | 18 | 96.1% |
| httpx | 533 | 522 | 11 | 97.9% |
| pytest | 127 | 125 | 2 | 98.4% |
| requests | 321 | 311 | 10 | 96.9% |
| **Total** | **2161** | **2109** | **52** | **97.6%** |

#### How it works

```
eval [path ...]           # initialize CLI and submodules for these paths, then evaluate them
  ├─ paths: lang/python, python, lang/python/requests, or python/requests
  └─ for each project and source file:
     └─ extractors/extract-functions.py <lang> <file>  →  JSONL (one object per construct)
        └─ tree-sitter query:
             constructor + method + class/interface/enum/annotation_type (java)
             function_declaration + method_declaration + type_declaration (go)
             function_declaration (lua)
             function_definition + struct/union/enum_specifier (c)
             function_declaration + class_declaration + object_declaration (kotlin)
             function_definition + class_definition (python)
        └─ emit each declaration once; remove extracted children from its body
  └─ evaluation.py checks extractor exit status and validates all JSONL before counting
  └─ skip repeated identical wrapped inputs within each language across the run
  └─ for each construct:
        write .wrapped (minimal valid program per language) to a temp file
        linvast ast <wrapper> -o /dev/null -c
        record acceptance, parser/builder rejection, unsupported language, or timeout
  └─ per-project summary + global TOTAL
```

Per-language wrappers are minimal and language-correct:

| Lang | wrapper |
| ---- | ------- |
| c    | `<construct>` (no wrapper needed) |
| go   | `package main\n<construct>` |
| java | `class Wrapper { <method> }` (methods/constructors; class/interface/enum bare) |
| kotlin | `<construct>` (no wrapper; class/function/object top-level) |
| lua  | `<construct>` (no wrapper needed) |
| python | `<construct>` (no wrapper needed) |

Every captured declaration is emitted, including nested functions, methods,
constructors, and classes. Each unit has its directly contained extracted
declarations removed. For example, if `A` contains a field, method `f`, and class
`B`, the units are `A` with just its field, `f`, and `B`. If `B` contains `g`,
`B` is emitted without `g`, and `g` is emitted separately. Functions also have
their extracted nested declarations removed. Repeated query captures of exactly
the same range are emitted once (for example, a grouped Go type declaration).
Adjacent declarations on the same line remain separate.

Removed text is replaced with spaces while retaining original line endings and
byte positions. Line metadata refers to the original declaration range. Python
uses `pass` at removed declaration sites to keep suites valid, and removes
attached decorators from the enclosing unit. Extracted Python definitions retain
the existing behavior of omitting their own decorators. For an inline C type
definition used by a field, the parent retains the type reference and field
declarator, but the nested type body is removed and evaluated separately.

The runner then deduplicates exact wrapped UTF-8 inputs within each language for
the entire run, including across projects, using SHA-256. The first occurrence
is evaluated; subsequent copies produce `duplicate_construct` events pointing
to that occurrence and do not enter the evaluation denominator. Whitespace and
comments outside the removed declarations are preserved, so this does not identify
semantic or approximate clones. Deduplication uses the resulting stripped units.
Whole-file mode does not apply declaration selection or construct deduplication.

#### Requirements

- `tree-sitter` CLI (v0.26+)
- Python 3 and `fd` (source discovery)
- `git` + network access on first run (the script initializes the `CLI` submodule and submodules for the requested language/project paths; the extractor also clones tree-sitter grammars into `extractors/tree-sitter-{java,go,lua,c,python}/` and `extractors/tree-sitter-kotlin/` and builds a native `<lang>.so` parser with `tree-sitter build`). Parsers are cached after the first build.
- .NET 10 SDK (to build the `linvast` CLI and local LINVAST projects)

#### Outcome accounting

Each run writes `results/evaluation-*/summary.json`, structured `events.jsonl`,
and raw extractor/build/CLI diagnostics in `diagnostics.log`. The run summary also
records resolved LINVAST versions and whether each dependency came from a local
project or a package, including the project path. Per-project and
global summaries count files discovered, deliberately excluded files, successfully
extracted files, extraction failures, zero-match files, matched constructs,
unique constructs, duplicate constructs,
whole-file submissions, attempted units, parser rejections, builder rejections,
unexpected builder errors, CLI errors/crashes,
unsupported-language rejections, timeouts, accepted ASTs, and infrastructure
failures separately. Events identify files, construct indices, names, line ranges,
exit codes, and exclusion reasons where applicable.

Project discovery uses the language projects registered in `.gitmodules`;
unregistered directories left on disk are not evaluated. File discovery uses
`fd`'s default visibility and ignore rules, then applies the existing
path-substring exclusions (`tests`, `test`, `vendor`, `node_modules`). The discovered
count covers files returned by `fd`; hidden or ignored files are outside that
scope. All selected files are inventoried before evaluation, with overlapping
targets deduplicated. A successful extractor with no matches increments
`zero_match_files`. A failed extractor increments `extraction_failures`, discards
all partial output, and aborts. Malformed extractor JSON is also a failure.

The CLI's `ast` command returns these distinct exit codes:

| Exit code | Meaning |
| --- | --- |
| 0 | AST built and serialized successfully |
| 10 | Lexer/parser rejection from the LINVAST error listener or ANTLR recognition error |
| 11 | Builder rejection: unsupported construct, unknown operator, or builder-originated syntax exception |
| 12 | Unsupported language |
| 13 | Unexpected LINVAST builder or serialization error |
| 70 | Input/output or configuration failure |

The runner enforces `FUN_TIMEOUT` (positive seconds; default 60), kills the timed-out
process group, and records a timeout independently of CLI exit codes. Other unknown
nonzero exits and crashes count as `cli_errors` and evaluation continues. Wrapped
unexpected exceptions inside builder syntax exceptions count as `builder_errors`.
Build failures abort before invoking any previously published CLI.

Rejections, LINVAST errors, CLI crashes, and timeouts allow evaluation to continue.
Setup, extraction, and I/O infrastructure failures
produce a nonzero runner exit and `status: "aborted"` with partial counts; these
are not completed benchmark results. On a completed run, attempted units equal
accepted ASTs plus parser rejections, builder rejections, builder errors, CLI errors,
unsupported-language rejections, and timeouts. `matched_constructs` counts the
declaration units emitted by successful extraction. On a completed run it
equals `unique_constructs + duplicate_constructs`; attempts equal unique
constructs plus whole-file submissions. An aborted run can have matched units
that were not yet deduplicated or attempted. Whole-file mode
counts `whole_files_submitted` instead of matched constructs. Submodule setup
failures abort in the shell before the evaluation run starts.

The exit-code protocol requires the corresponding changes in the `CLI` submodule.
Runner regression checks: `python3 -m unittest discover -s tests -p 'test_*.py'`.
Set `EVAL_TEST_CLI` to the absolute path of a freshly built CLI executable to
include the real CLI exit-code tests; otherwise those tests are skipped.

#### Limitations

- Extracts named function, method, and type declarations at all scoping depths. Each unit retains its own members and statements, with extracted children removed. References to removed declarations or to file context such as imports can affect evaluation.
- Lua/anonymous function expressions (e.g. `local f = function() … end`) are not captured; only named `function_declaration`s are.
- Kotlin top-level functions, classes, and objects are extracted without a wrapper context; constructs that reference other top-level Kotlin declarations may fail if those are omitted from the extracted code.
- Python top-level functions and classes are extracted without their file context; constructs that depend on module-level imports or globals will fail the wrapper parse.
- C functions frequently reference file-scope globals, types, and macros defined in sibling headers (e.g. `CJSON_PUBLIC`, `global_error`, struct tags). The minimal `<construct>` wrapper does not include these, so such functions fail the wrapper parse even though the whole file parses at file-level. This is a known C trade-off, not a bug.
- One `linvast ast` CLI invocation is spawned per unique construct. Large repositories can be slow; prefer a smaller project or package for exploratory runs.

*Notes*:

- Source captures use original UTF-8 byte offsets. The [UTF-8 extraction audit](reports/utf8-offset-audit.md) records the slicing fix and a six-repository before/after comparison: 1,653 sampled units, none changed. The full published corpus has not been re-audited.
- Each evaluated project prints its own `summary:` line, and a single `=== TOTAL (complete/aborted) ===` reports the run status and aggregate counts. The historical tables above predate this accounting and selection policy.

## LINVAST dependency selection

Builds use the neighboring **local LINVAST source checkout by default** in both
Debug and Release. For this repository layout, it is `../LINVAST` relative to
the evaluation root. The projects currently declare version **3.0.0**.

Build explicitly against local source, or override the checkout location:

```sh
dotnet build CLI/CLI/CLI.csproj -p:UseLocalLinvast=true
dotnet build CLI/CLI/CLI.csproj -p:LinvastSourceRoot=/path/to/LINVAST
```

Local mode requires both the core and imperative projects and fails clearly if
either is missing. It uses checkout changes directly. There is no automatic
fallback to packages. Use `-p:UseLocalLinvast=false` to explicitly select exactly
version **3.0.0** of both NuGet packages. The obsolete comparer package is no longer
used; the CLI uses the comparison helper from the current neighboring CLI source.

Use the same property for `dotnet run` or `dotnet publish`. If restoring separately,
pass the same selection to both restore and build/publish; restore again when
switching dependency modes before using `--no-restore`.

`./eval` uses local source by default. `UseLocalLinvast=false ./eval` explicitly
selects packages. The runner places build artifacts under `results/build/`,
including those from the referenced local projects.
