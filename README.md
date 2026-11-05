# seekdb-deps

This repository contains pinned third-party source archive references, package-specific build recipes, and patches maintained for SeekDB. One package version lives under `packages/<name>/<version>/`; shared download, build, and archive behavior lives under `scripts/`.

## Layout

```text
packages/<name>/<version>/
  recipes/build.py        only package-specific build and file selection
  patches/                ordered package patches, when needed
  package.json            source URL, revision, archive checksum, license, and outputs
  source/                 fetched source; included in source bundles, not stored in Git
scripts/
  package.py              source/binary release orchestration
  build.py                shared target handling and RecipeContext
  archive.py              manifests, deterministic tar.gz, and SHA256
platforms/<target>.json   target ABI and supported build hosts
```

Package recipes use `RecipeContext.cmake(...)` for the configure/build lifecycle, `ctx.run(...)` for an upstream-specific command, and `ctx.install(source, relative_path)` to add an output to the package. They do not manage temporary directories, patches, license placement, manifests, checksums, or archives.

Binary archives have one package-version directory containing relative `include/`, `lib/`, `bin/`, and `share/` paths. They have no fixed installation prefix; consumers extract the archive into their chosen dependency directory. Build workspaces and staging files are created under temporary output directories, not under the checked-out package sources.

## Build and package

The command requires Python 3.8 or newer. Binary builds also require CMake 3.22.1 or newer and Ninja. The package runner downloads each package's HTTPS source archive, checks its SHA256, and extracts the declared top-level directory. `--kind source` creates a source bundle without a compiler or target; the bundle includes the verified package source, recipe, target profiles, and shared runner, so it can build a binary package after extraction without network access.

```sh
# Source bundle only; no target or compiler is needed.
python3 scripts/package.py zlib 1.2.13 --kind source

# Build and package the Linux binary.
python3 scripts/package.py zlib 1.2.13 --kind binary \
  --target linux-x86_64-glibc232-gcc9

# Create both a source bundle and a binary package.
python3 scripts/package.py zlib 1.2.13 --kind both \
  --target linux-x86_64-glibc232-gcc9 --output dist

# Rebuild from an extracted source bundle using the same entry point.
python3 scripts/package.py zlib 1.2.13 --kind binary \
  --target linux-x86_64-glibc232-gcc9
```

Use `--date YYYYMMDD` to set the artifact date and `--jobs N` to set parallel build jobs. By default, the date is the current UTC date and output goes to `dist/`.

Artifact names contain the package version, source/binary kind, target for binary packages, date, and the first 12 characters of the seekdb-deps commit. Each archive has a `.sha256` sidecar. `manifest.json` inside the archive records the full repository and upstream revisions, ordered patch digests, target profile, observed toolchain, license, and per-file hashes.

## Target profiles

The initial profiles cover these build configurations:

| Target | Build host | Toolchain contract |
| --- | --- | --- |
| `linux-x86_64-glibc232-gcc9` | Linux x86_64 | glibc 2.32 builder, GCC/G++ 9.4.0 |
| `macos-arm64-min13` | macOS arm64 | Apple Clang, arm64, deployment target macOS 13 |
| `windows-x86_64-msvc143-md` | Windows x86_64 | Visual Studio v143, Release, dynamic MSVC runtime (`/MD`) |
| `android-arm64-v8a-api26-ndk27` | Linux, macOS, or Windows x86_64 | Android NDK 27.2.12479018, arm64-v8a, API 26 |

Linux, macOS, and Windows profiles describe native builds. Android profiles distinguish the host toolchain from the Android target and use the NDK CMake toolchain. Set `ANDROID_NDK_HOME` or `ANDROID_NDK_ROOT` to the required NDK. Windows builds run from an initialized Visual Studio developer environment. macOS builds require the Xcode command line tools.

Profiles are build contracts, not validation claims. The zlib 1.2.13 recipe currently builds only upstream's `zlibstatic` target and stages the static library and public headers. The Linux profile has been built on Linux x86_64 with glibc 2.32 and GCC 9.4.0. macOS, Windows, and Android profiles are configured but have not been built in this change.

## Licensing

Files maintained specifically for this repository are licensed under Apache License 2.0. Third-party dependencies retain their own licenses and notices; each binary package includes the dependency license under `share/licenses/<name>/LICENSE`.
