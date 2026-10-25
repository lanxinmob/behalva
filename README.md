<p align="center">
  <img src="brand/behalva-mark-color.svg" width="96" height="96" alt="Behalva mark">
</p>

<h1 align="center">Behalva</h1>

<p align="center">
  <strong>A self-hosted personal assistant that acts on your behalf — after you approve.</strong>
</p>

<p align="center">
  C++ core · Kotlin Android · SQLite · local-first permissions
</p>

<p align="center">
  <a href="#status">Status</a> ·
  <a href="#what-it-does">What it does</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#privacy">Privacy</a> ·
  <a href="#license">License</a>
</p>

---

Behalva keeps a durable workspace on **your** machine: gather sources, index materials, draft next steps, and run outbound actions only when you confirm the exact payload. The name blends *on behalf* with a two-half mark — like a split tally, each side holds half of the same credential.

> **Prototype.** Runnable today; not a finished v1. Real model providers, phone, QQ, and Gmail still need your own credentials and verification.

## Status

| Area | State |
| --- | --- |
| C++ / SQLite core (tasks, authz, budget, approvals) | Working |
| Web workbench | Working |
| Android offline queue + pairing | Debug APK builds; real-device checks pending |
| Model calls (OpenAI-style protocols) | Working against configured endpoints |
| Gmail / QQ / browser automation | Scaffolded; live account verification pending |
| Self-update with dual review + enable gate | Designed; update manager not shipped |

## What it does

- **Workbench first** — pending tasks, approvals, and long-term goals on the home surface; chat is for intent, not the only UI.
- **Honest connection states** — each integration reports *not configured / unverified / verified / error* from the core, not guesses in the browser.
- **Approvals with a frozen payload** — recipients, body, attachments, and form fields are shown before send; changed content needs a new approval.
- **No blind retries on side effects** — unknown outcomes stay unknown until verified.
- **Local index, gated cloud use** — private files stay local until you authorize a path for model context.
- **Phone as a peer** — Kotlin app with offline drafts and conflict choice; the database is never exposed to the device.

## Architecture

```text
┌─────────────────┐     HTTPS      ┌──────────────────────────┐
│  Kotlin Android │◄──────────────►│  C++ core (authority)    │
│  offline cache  │                │  SQLite · tasks · budget │
└─────────────────┘                │  approvals · sync API    │
                                   └────────────┬─────────────┘
                                                │ claim / result
                                   ┌────────────▼─────────────┐
                                   │  Adapter processes       │
                                   │  (worker, Gmail, browser │
                                   │   indexer, …)            │
                                   └──────────────────────────┘
```

- **C++ owns** persistence, authorization, task state, model request context, budget, and approvals.
- **Adapters** may use other languages; they return structured results and never get arbitrary DB write access.
- **Kotlin** owns Android UI and offline state.
- Personal profile and credentials live **outside** this repository.

## Quick start

Windows with MSYS2 UCRT64 (GCC, SQLite, OpenSSL) is the path currently documented.

```powershell
./tools/build.ps1
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
./.venv/Scripts/python -m playwright install chromium
./.venv/Scripts/python tools/workspace.py init --data <YOUR_PRIVATE_DATA_DIR>
./tools/start.ps1 -Browser
```

Open `https://127.0.0.1:8765`, paste `owner.token` from the data directory, then complete the onboarding guide (workspace → model → materials → chat).

Do **not** commit the data directory. It holds certificates, tokens, browser sessions, and the database.

### Tests

```powershell
./.venv/Scripts/python -m unittest discover -s tests -v
./tools/build_android.ps1 -Test
```

## Privacy

This public tree is product code only.

| Goes in this repo | Stays private |
| --- | --- |
| Core, web, Android, connectors, docs, tests | Profile, admissions notes, real keys |
| `config/example.json` | `local.json`, OAuth tokens, device certs |
| Brand marks under `brand/` | Live workspace DB and logs |

Rotate any secret that ever touched a private clone before you treat a public tree as clean.

## Contributing

Issues and pull requests are welcome once the public repo is live. Keep changes generic: no personal admissions data, no real credentials, no silent side-effect retries. Code that changes the running product still needs dual review and an explicit enable step in the self-update design.

## License

Copyright 2026 Huxianbin / lanxinmob.

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).

```
                                 Apache License
                           Version 2.0, January 2004
```

The Behalva name and mark are used for this project; trademark registration is separate from the software license.
