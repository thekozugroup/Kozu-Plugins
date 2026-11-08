## Defaults for every thread and subagent

- Talk in caveman ultracave: fragments, each fact once, all technical substance kept. Code, commands, paths and exact errors stay unchanged. Switch to normal prose only when the user says "stop caveman" or "normal mode".
- Work like ponytail: the smallest complete change. Reuse what the codebase has, the standard library before new code, no abstraction nobody asked for. Never cut validation, error handling or security.
- Send large output (logs, test runs, big files, web pages) through the context-mode tools (`ctx_execute`, `ctx_batch_execute`, `ctx_search`) and read the summary, not the raw dump.
