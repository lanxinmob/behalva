# Scrub report

History-free product tree for seeding `lanxinmob/behalva`. Nothing from this run was committed to `lanxinmob/personal-assistant` or pushed to `behalva`.

## Source

- Repository: `lanxinmob/personal-assistant`
- Revision: `origin/main` `07a3f8b519b52add7a456d86161b8196bda71578`
- Subject: `连接与授权: honest per-connection states (#6)`
- The checkout on this machine was `feat/wsl2-runtime`. That branch was not exported.

## Artifact

- Path: `/opt/cursor/artifacts/behalva-product-tree.tar.gz`
- SHA-256: `ea6f5261fe9fa36a857c8421e77f7f35a7e8f7b7d0cb5f7584884a8d593795ba`
- Size: 500628 bytes
- Archive members: 132 (directories plus 105 files)
- Format: gzip tar of the tree root. No `.git` directory and no commit history.
- This report is beside the archive. It is not inside the tarball.

## Included

`src/`, `web/`, `android/` source and Gradle project files, `connectors/`, `tests/`, `tools/`, `scripts/`, `config/example.json`, `docs/` (including `docs/README.md` and `docs/reviews/7cd02ca.md`), `AGENTS.md`, `CMakeLists.txt`, `requirements.txt`, `requirements.lock.txt`, `.gitignore`, `.gitattributes`, `.clang-format`, and `vendor/` (`httplib.h`, `json.hpp`, and their license files). `vendor/` is required to compile the C++ core.

## Excluded

- `.git` and all history.
- Repository-root `README.md`. It is not part of the requested product file list, and the copy on `main` contains the private workspace path. The follow-up overlay is expected to add Apache `LICENSE`, `NOTICE`, and `README`.
- `config/local.json`. It is gitignored and was not on `main`.
- Android `build/`, `.gradle/`, `local.properties`, and `jniLibs/` binaries. None of these were on `main`. The export still dropped those names if present.
- Certificates, keys, token files, databases, and logs. None were in the `main` tree. `.gitignore` already ignores `*.pem`, `*.key`, `*.db`, `.env`, and `/config/local.json`.

## Redacted

| Location | Change |
|---|---|
| `tools/start.ps1` | Default `-Data` changed from `D:\personal-assistant-private\workspace` to `D:\path\to\private-workspace`. |
| `tools/workspace.py` | Windows `PA_RUNTIME_BIN` fallback changed from `D:\msy2\ucrt64\bin` to `D:\path\to\msys64\ucrt64\bin`. Callers can still set `PA_RUNTIME_BIN`. |
| `docs/status.md` | The sentence that named a specific university school site now says a public college site was fetched and snapshotted, with no login or form submit. The school name is not in the public tree. |

A post-scrub scan of the staged tree found no `personal-assistant-private`, `msy2`, that school name, private-key PEM blocks, or cloud/API token prefixes.

## Reviewed and left unchanged

- `owner.token`, `worker.token`, and `qq.token` are file names and test reads of tokens created in temporary fixtures. `main` has no sample token file and no real token value.
- Addresses in tests and UI fixtures use `example.org`, `example.test`, `example.invalid`, `fixture.invalid`, `personal-assistant.local`, or Tailscale documentation hosts. `vendor/json.hpp` keeps the upstream copyright address `evan@nemerson.com`.
- `tests/test_migrate_workspace.py` still uses synthetic `D:\notes` paths. Those strings are asserted by the migration test. They are not the private workspace directory and not a machine toolchain path.
- `tests/test_ui_design.py` still fills `https://192.168.50.20:8765` as a LAN fixture.
- Docs still name iQOO as the physical phone used for acceptance. That is a device target, not an account or an application record.
- Generic admissions-workflow wording (升学, 报名, 招生) stays. It describes the product. It does not include a person's schools, scores, or application materials.
- `config/example.json` already uses `YOUR_ACTUAL_MODEL_ID`, `PA_API_KEY`, and zero prices.
- `tools/prepare_android.py` keeps the published SHA-256 of the Android command-line tools zip. That checksum is a public download pin, not a credential.
