# Tracking research dependencies

This is an optional **host-only** Python environment. None of these Python packages or
wheels are bundled in the Android APK. The separately labeled Kotlin adaptation
retains its own Roboflow license and attribution in the app license assets. The app's native, volatile
frame/landmark boundary and lack of network permission are unchanged. Installing
the environment uses the network; the synthetic adapter tests require no network,
accounts, API keys, paid services, models, recordings or user devices.

## Reproducible environment

`requirements-trackers-linux-py312.txt` pins all 30 packages in the resolved
`trackers==2.7.0` environment, excluding the pip installer itself. It targets
CPython 3.12 on Linux x86_64, including Ubuntu 24.04. It is not a portable lock
for macOS, Windows, ARM or other Python versions. Each entry identifies the
selected official PyPI wheel and its SHA-256. No source distributions or optional
`detection`, `reid`, `mask` or `tune` extras are used.

On 2026-11-09, these exact versions were downloaded with `pip download
--index-url https://pypi.org/simple --only-binary=:all: --no-deps --no-cache-dir`.
Each downloaded wheel's SHA-256 was independently compared with the matching
filename in `https://pypi.org/pypi/<distribution>/<version>/json`. The lock records
the resulting wheel hashes; these are integrity pins, not a security audit.

```sh
python3.12 -m venv /tmp/sign-trackers
/tmp/sign-trackers/bin/python -m pip install \
  --index-url https://pypi.org/simple --only-binary=:all: \
  --require-hashes --no-cache-dir \
  -r native/research/requirements-trackers-linux-py312.txt
/tmp/sign-trackers/bin/python -m pip check
/tmp/sign-trackers/bin/python -m unittest discover \
  -s native/research -p 'test_*.py' -v
```

The [live tracking workflow](../../.github/workflows/sign-roboflow-trackers.yml)
runs these checks and Kotlin reference parity for eligible same-repository PRs
from the integration branch or on manual dispatch. It also runs host and Android
unit checks, prepares pinned models on the build host, builds and audits the
unsigned ARM64 QA app and instrumentation APKs, and compiles x86_64 emulator
instrumentation without executing it. The workflow retains bounded build and test
evidence for three days. Uploading the unsigned QA pair requires overall success.
The workflow uses no devices and performs no signing or installation.

Updating a Python pin requires regenerating the selected wheel hashes and
rerunning the real-library tests and `pip check` in a fresh environment.

## Source and license scope

- [Roboflow Trackers 2.7.0 on PyPI](https://pypi.org/project/trackers/2.7.0/)
  is Apache-2.0. Its [source repository](https://github.com/roboflow/trackers)
  was inspected at commit
  `3d1532372b46bae1e31a7f4aa3d7350482dadfb6`. The installed wheel supplies the
  host reference implementation.
- The upstream package implements box tracking. The Android app uses an
  attributed Kotlin adaptation of its SORT tracker with the upstream Apache-2.0
  license and modification notice. The Python package remains host-only.
- Transitive dependencies retain their own licenses and bundled notices. Apache
  licensing of Trackers does not cover the whole Python stack, multimedia codecs,
  models, datasets or private recordings. Preserve each wheel's license files
  when redistributing it. Redistribution or Android bundling would require a
  separate dependency, binary and notice review; this host test environment is
  not that admission.
- This dependency lock does not grant permission to acquire, export or publish
  landmarks or video. Use synthetic fixtures for CI and separately authorized,
  appropriately licensed inputs for any subsequent offline research.
