# Evaluation of LINVAST AST builders on open-source repositories

## Java

| Repository |  %   | Files parsed (LoC) | Files failed (LoC) |
| ---------- | ---- | ------------------ | ------------------ |
| antlr4     | 98.5 | 513 (73577)        | 8 (1917)           |
| graal      | 91.2 | 11155 (2012335)    | 1097 (398405)      |
| jdk        | 87.3 | 43799 (7525068)    | 6372 (1780131)     | 

*Note*: The lines of code (LoC) metric for failed files is a bit misleading as it seems the parser failed to parse that many lines, when in fact the parser probably failed on few lines in the file, but the benchmark counts all of them as failed.
