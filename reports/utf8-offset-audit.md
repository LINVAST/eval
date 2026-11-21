# UTF-8 extraction audit

The extractor now reads the original source bytes, computes line starts in bytes,
slices captures using Tree-sitter's byte columns, and then decodes each capture as
UTF-8. This corrects both construct text and captured names. Binary reading also
preserves CRLF and lone CR bytes instead of applying Python's universal-newline
translation. Tree-sitter defines rows using LF and columns using bytes in its
[parsing API documentation](https://tree-sitter.github.io/tree-sitter/using-parsers/2-basic-parsing.html#syntax-nodes).

On 2026-11-21, extraction was rerun on six smaller repositories, one per supported
language, as requested. **No units changed among the 1,653 extracted units in 119
files.** There were no extraction errors. This sample does not measure the number
of affected units in the full published corpus.

| Repository | Files | Units before | Units after | Changed units |
| --- | ---: | ---: | ---: | ---: |
| c/tinyexpr | 7 | 82 | 82 | 0 |
| go/cobra | 19 | 285 | 285 | 0 |
| java/javapoet | 17 | 454 | 454 | 0 |
| kotlin/kotlin-benchmarks | 31 | 362 | 362 | 0 |
| lua/middleclass | 11 | 9 | 9 | 0 |
| python/flask | 34 | 461 | 461 | 0 |
| Total | 119 | 1,653 | 1,653 | 0 |

The comparison uses the pre-fix extractor at
`06e70b259a2cc732869ffd9922687d995cdb7f00` and the corrected extractor, with identical
Tree-sitter query output supplied to each. All emitted fields are compared,
including `name`, `code`, `wrapped`, type, and line numbers. File selection follows
`eval`: `fd -e EXT`, excluding paths containing `tests`, `test`, `vendor`, or
`node_modules`. The baseline reproduces the old UTF-8 text read and newline
translation. The corrected version uses the original bytes.

Tree-sitter CLI version: **0.26.9**. Repository and grammar commit IDs, the corrected
extractor's SHA-256, and per-project results are in
[utf8-offset-audit.json](utf8-offset-audit.json). Grammars were freshly downloaded;
the original published run's grammar revisions are not recorded. Tinyexpr's 82
units differ from the published table's 79; the other five sample totals match.
These results therefore describe the checked-out sources and recorded parser
versions, not an exact reproduction of all published inputs. LINVAST pass/fail
evaluation was not rerun; this audit measures extraction changes.

Run the regression checks from the repository root:

```sh
python3 -m unittest discover -s extractors -p 'test_*.py'
```

The temporary audit helper was removed after use. It wrote complete
`before.jsonl` and `after.jsonl` snapshots,
`changed.jsonl` (empty in this run), source hashes in `inputs.jsonl`, and
`summary.json`. These local artifacts are under the ignored `results/` directory.
For matching results, use the repository and grammar revisions in the recorded
JSON and rebuild cached parser libraries if grammar revisions change.

Validation: eight regression tests pass, including the reported `/*é*/ int f() {}`
example, multibyte names and bodies, multiline byte ranges, ASCII boundaries, and
original CRLF/lone-CR handling. Additional real CLI fixtures passed for all six
languages, checking exact extracted code, names, and wrapper inclusion; the Python
fixture also checks CRLF preservation. The reported C example now yields exactly
`int f() {}`.
