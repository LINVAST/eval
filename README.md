# Evaluation of LINVAST AST builders on open-source repositories

*Run with:* `./eval <folder>` (function-level is the default; Java, Go, Lua, Kotlin, C supported). Whole-file mode: `./eval --whole-files <folder>`.

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
| jdk | 232379 | 224046 | 8333 | 96.4% |
| jdk11 | 253978 | 250807 | 3171 | 98.8% |
| **Total** | **630747** | **615037** | **15710** | **97.5%** |

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
eval <folder>            # function-level (default); whole-file: add --whole-files
  └─ extractors/extract-functions.py <lang> <file>  →  JSONL (one object per construct)
        └─ tree-sitter query:
             constructor + method + class/interface/enum/annotation_type (java)
             function_declaration + method_declaration + type_declaration (go)
             function_declaration (lua)
             function_definition + struct/union/enum_specifier (c)
             function_declaration + class_declaration + object_declaration (kotlin)
             function_definition + class_definition (python)
  └─ for each construct (bash + jq):
        write .wrapped (minimal valid program per language) to a temp file
        linvast ast <wrapper> -o /dev/null -c
        tally pass/fail; failures logged as {file,name,startLine,endLine,type,result}
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

#### Requirements

- `tree-sitter` CLI (v0.26+)
- `jq`
- `git` + network access on first run (the extractor clones the tree-sitter grammars into `extractors/tree-sitter-{java,go,lua,c,python}/` and `extractors/tree-sitter-kotlin/` and builds a native `<lang>.so` parser with `tree-sitter build`). Parsers are cached after the first build.
- `dotnet` SDK (to build the `linvast` CLI, same as file-level mode)

#### Limitations

- Extracts named function, method, and type declarations at any scoping depth. Those whose body depends on enclosing-class/package context (e.g. `Outer.this` references, Go types declared in other files, Python methods referencing module-level imports) fail the wrapper parse and are reported as failures — this is intended (it surfaces exactly which construct is problematic).
- Lua/anonymous function expressions (e.g. `local f = function() … end`) are not captured; only named `function_declaration`s are.
- Kotlin top-level functions, classes, and objects are extracted without a wrapper context; constructs that reference other top-level Kotlin declarations may fail if those are omitted from the extracted code.
- Python top-level functions and classes are extracted without their file context; constructs that depend on module-level imports or globals will fail the wrapper parse.
- C functions frequently reference file-scope globals, types, and macros defined in sibling headers (e.g. `CJSON_PUBLIC`, `global_error`, struct tags). The minimal `<construct>` wrapper does not include these, so such functions fail the wrapper parse even though the whole file parses at file-level. This is a known C trade-off, not a bug.
- One `linvast ast` CLI invocation is spawned per construct. This is correct but slow for very large repositories (e.g. `go/task`, ~6.5 MB / 904 constructs); prefer a smaller project or package for exploratory runs.

*Notes*:

- The file-level LoC metric for failed files is misleading: the parser likely fails on few lines in the file, but the benchmark counts all of them as failed. Function-level mode addresses this by reporting pass/fail per construct.
- Each evaluated project prints its own `summary:` line (e.g. `=> antlr4/... summary: constructs total=3623 succ=3623 fail=0`), and a single `=== TOTAL ===` aggregates everything.
