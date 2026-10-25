# Implementation contract

Read docs/requirements.md, docs/architecture.md and docs/acceptance.md before changing their modules. The user approved the design on 2026-10-14.

- Keep the product generic; private profile and credentials belong outside this repository.
- C++ owns persistence, authorization, task state, model request context, budget and approvals. Kotlin owns Android UI and offline state. Adapter processes are permitted.
- Never implement real side effects as automatically retryable tasks. Ambiguous outcomes remain unknown until verified.
- Every self-code update needs two independent developer-agent reviews and user activation approval. Review exact revisions; do not claim manual/mobile checks passed without evidence.
- Tests must expose meaningful behavioral defects or validate the real workflow. Explain their purpose; do not write tests merely to increase counts.
- No public push or release without explicit authorization. Local development and validation are authorized.
- Do not put personal admissions data into source code, fixtures or public docs.
