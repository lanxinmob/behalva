# Android embedded private connection

The assistant uses an official `tailscale.com/tsnet` node inside its Android
process. It does not register an Android `VpnService`, require root, or modify
Clash. Clash can remain the phone's one active VPN. The C++ workspace service
and device-token permissions are unchanged.

## Connect

1. Keep Tailscale and the assistant service running on the computer. Enable
   Tailscale Serve for the assistant; this provides a private HTTPS address.
2. On the computer, pair a phone in the assistant's connection page and copy
   its connection configuration. Transfer it privately to the phone.
3. On the phone, stop standalone Tailscale and keep ordinary Clash connected.
4. Import the connection configuration in the assistant's settings. Select
   the embedded Tailscale connection, then tap Connect.
5. Tap Open Tailscale login page when it appears. Authorize this phone using
   the same tailnet as the computer. Return to the assistant and synchronize.
   An administrator may also need to approve the new device.

No API key belongs in the connection configuration. Model credentials stay on
the computer. Existing direct HTTPS connections remain available through the
System network option.

Stop persists across process restarts and background jobs. Reconnect requires
an explicit tap. Logging out clears only this workspace's local node identity;
offline documents, drafts and pending work remain. Remove an obsolete device
from the Tailscale account separately if desired. Android may suspend or kill
the app in the background; this is not an always-on VPN or an instant push
service. A previously enabled connection restarts on the next sync after
process death, while an explicitly stopped connection stays stopped.

## Transport and security

Kotlin passes workspace HTTP requests to a small JNI bridge. Go sends them
over `tsnet.Server.Dial`; no local HTTP/SOCKS proxy is opened. All workspace
calls, including chat, sync, attachments and browser handoff, use the selected
transport. Embedded failures never fall back silently to a different route.

HTTPS is still required for the workspace. Without a pin, Go checks the
hostname, validity and certificate chain against a snapshot of Android's
default trusted issuers. This does not reproduce every Android TrustManager
policy (for example, platform-specific revocation or certificate transparency
rules). With a user-verified SHA-256 leaf pin, the exact certificate and its
validity authenticate the server, including self-hosted VPN IP endpoints.
Redirects are not followed. Device tokens are sent only to the configured
origin. Authorization URLs are memory-only, allowlisted official device-login
URLs, and opened only by a user tap. Logtail is omitted from the build.

Each offline workspace database has its own node state beneath Android's
no-backup private directory. Transport changes preserve database identity.
Queued work and callbacks are bound to the connection revision; switching
workspaces cancels stale work. A request that already reached the old server
cannot be undone, so a lost result still requires checking its receipt.

The native HTTP transport does not retry operations. Existing offline sync
reconciles only backend operations that have stable request IDs and server-side
idempotency receipts; browser commands are never placed in that queue. An
interrupted browser operation must be checked by refreshing the same session.

## Build from source

Pinned dependency: Tailscale v1.104.1, Go 1.27.1, Android NDK r29. The Go module
and its checksum file are committed. This adapter currently builds arm64-v8a,
matching the initial Android target. Other ABIs require a corresponding native
build and device verification; they are not silently emulated.

On Windows, install those tools and the project's documented Android SDK/JDK
and Gradle dependencies, then run from the repository root:

```powershell
$env:ANDROID_NDK_HOME = 'C:/Android/android-ndk-r29'
./tools/build-tailnet.ps1
cd android
gradle testDebugUnitTest assembleDebug
```

The helper accepts `-Go` and `-Ndk` for explicit tool paths. Go downloads the
checksummed upstream dependencies. The library uses `ts_omit_logtail`, trimpath,
and 16 KiB ELF load alignment. Generated binaries are ignored by Git. APK
assembly checks the library hash and its generated source-hash manifest;
missing, changed or stale native output must be rebuilt after Go/JNI changes.

Run focused transport tests with `go test -tags ts_omit_logtail .` in
`android/tailnet`. Android unit tests cover workspace isolation, stop state,
connection races and existing offline/UI behavior. They do not execute the
arm64 JNI library. On-device JNI loading, authorization, Clash coexistence,
Wi-Fi/cellular changes, sleep and background process restart require a physical
phone acceptance run before calling this connection validated.

## Open-source distribution

Tailscale's module is BSD-3-Clause licensed; its own dependency tree contains
other licenses. Preserve upstream notices when distributing native binaries.
Generate a module inventory with `go list -m -json all` from the pinned module
and collect the corresponding license/notice files for a public distribution.
No public binary release is authorized by this local development build.
Tailscale is an external account/control-plane dependency; this feature does
not claim independence from its hosted service or its terms.
