# Implementation contract

Read README.md for the project scope and usage.

- Keep the product generic. Personal profiles, real messages and credentials stay outside this repository, including tests, fixtures and docs.
- C++ owns persistence, authorization, task state, model request context, budget and approvals. Kotlin owns Android UI and offline state. Adapter processes are permitted.
- Never implement real side effects as automatically retryable tasks. Ambiguous outcomes remain unknown until verified.
- When the assistant changes its own code, the exact revision needs two independent agent reviews and the owner's approval before it is activated. Don't report a manual or phone check as passed unless it was actually run.
- Each test should catch a real behavioral defect or exercise a real workflow, and say which. Don't add tests just to raise the count.
