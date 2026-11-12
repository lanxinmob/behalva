<p align="center">
  <img src="brand/behalva-mark-color.svg" width="96" height="96" alt="Behalva mark">
</p>

<h1 align="center">Behalva</h1>

<p align="center">
  <strong>A self-hosted personal assistant that acts on your behalf — after you approve.</strong>
</p>

<p align="center">C++ core · Kotlin Android · SQLite</p>

Behalva keeps documents, conversations, tasks and drafts in a workspace on your computer. Its Android client provides offline access and synchronizes over HTTPS. The name blends *on behalf* with a two-half mark, inspired by a split tally.

## What it does

- Index local materials and keep source versions for retrieval.
- Chat with a configured model and turn discussions into investigation tasks.
- Prepare drafts and outbound actions for approval.
- Cache documents, drafts and pending work on Android, with conflict handling after reconnection.

The project is a runnable prototype. Real model providers, QQ, Gmail and physical-phone workflows still need integration testing. OCR, automatic updates, model routing and instant notifications are not complete.

## Quick start

On Windows, install MSYS2 UCRT64 with GCC, SQLite and OpenSSL, and add its toolchain directory to `PATH`. Run from the repository root:

```powershell
./tools/build.ps1
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
./.venv/Scripts/python -m playwright install chromium
$WorkspacePath = 'D:/assistant-data'
./.venv/Scripts/python tools/workspace.py init --data $WorkspacePath
./tools/start.ps1 -Data $WorkspacePath -Browser
```

Open `https://127.0.0.1:8765`, sign in with the workspace's `owner.token`, and configure a model provider and material permissions. Keep the workspace outside the source repository: it contains the database, tokens and browser sessions.

Stop with `./tools/stop.ps1 -Data $WorkspacePath`. These scripts run background processes; they do not install a Windows service or enable login startup.

For WSL2, keep the core, database and runtime environment on the Linux filesystem. `tools/wsl_service.py` manages the service. Windows and Linux must not open the same database concurrently.

## Models and phone connection

Add a provider endpoint, model ID and API key in the desktop connection settings. Responses and Chat Completions text interfaces are supported. Credentials are encrypted in the desktop workspace and are not synchronized to the phone.

Use an HTTPS address reachable from the phone, such as Tailscale Serve. Pair a device in the desktop interface and import its connection configuration in the Android app. For a LAN connection, configure the listening address and verify the certificate fingerprint. The phone's `127.0.0.1` refers to the phone itself.

## Data and approvals

Local indexing does not grant permission to send private material to a model. Approve cloud use explicitly. Outbound actions bind approval to their contents and attachments; check receipts before retrying an action with an unknown result.

Offline clients can read cached material, edit drafts and queue work. Remote execution requires the desktop service to be reachable.

## Development

```powershell
./.venv/Scripts/python -m unittest discover -s tests -v
./tools/build_android.ps1 -Test
```

The debug APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`. Automated tests use synthetic materials and simulated services.

## License

Copyright 2026 Huxianbin / lanxinmob. Licensed under [Apache-2.0](LICENSE); see [NOTICE](NOTICE).
