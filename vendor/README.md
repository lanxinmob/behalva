# Vendored dependencies

The files below are unmodified release headers from their upstream projects.

| File | Upstream version | SHA-256 | License |
|---|---|---|---|
| `json.hpp` | [nlohmann/json v3.12.0](https://github.com/nlohmann/json/releases/tag/v3.12.0) | `aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63` | MIT, `json.LICENSE` |
| `httplib.h` | [cpp-httplib v0.18.7](https://github.com/yhirose/cpp-httplib/releases/tag/v0.18.7) | `4770f8ea3d3fcd27b0b713c783d9b53bce3baddbd5adad13714011df96142526` | MIT, `httplib.LICENSE` |

SQLite and OpenSSL are linked from the local toolchain, rather than copied into
this source tree. Kotlin, Gradle and Python dependency versions are declared in
their build files and `requirements.lock.txt`. The latter records the tested
Windows development environment, not a guarantee of portable dependency wheels.

Android SDK and Robolectric runtime downloads remain in the ignored `.tools`
directory. `tools/prepare_android_test.py` verifies the official Maven SHA-512
before using a downloaded runtime.
