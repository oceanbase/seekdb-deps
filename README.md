# seekdb-deps

This repository contains pinned third-party source archive references, package-specific build recipes, and patches maintained for seekdb. One package version lives under `packages/<name>/<version>/`; shared download, build, and archive behavior lives under `scripts/`.

## Layout

```text
packages/<name>/<version>/
  recipes/build.py        only package-specific build and file selection
  patches/                ordered package patches, when needed
  package.json            source URL, revision, archive checksum, license, and outputs
  source/                 fetched source; included in source bundles, not stored in Git
scripts/
  package.py              source/binary release orchestration
  init_build_env.py       shared compiler and SDK environment initialization
  build.py                shared target handling and RecipeContext
  archive.py              manifests, deterministic tar.gz, and SHA256
platforms/<target>.json   target ABI, C flags, and supported build hosts
```

Package recipes use `RecipeContext.cmake(...)` for the configure/build lifecycle, `ctx.run(...)` for an upstream-specific command, and `ctx.install(source, relative_path)` to add an output to the package. They do not manage temporary directories, patches, license placement, manifests, checksums, or archives. The package runner initializes the build environment before downloading source or invoking a recipe. `scripts/init_build_env.py` selects or loads host compiler settings, checks minimum tool versions, and prepares the target-specific CMake arguments.

Binary archives have one package-version directory containing relative `include/`, `lib/`, `bin/`, and `share/` paths. They have no fixed installation prefix; consumers extract the archive into their chosen dependency directory. Build workspaces and staging files are created under temporary output directories, not under the checked-out package sources.

## Build and package

The command requires Python 3.8 or newer. Binary builds also require CMake 3.22.1 or newer and Ninja. The package runner downloads each package's HTTPS source archive, checks its SHA256, and extracts the declared top-level directory. `--kind source` creates a source bundle without a compiler or target; the bundle includes the verified package source, recipe, target profiles, and shared runner, so it can build a binary package after extraction without network access.

```sh
# Source bundle only; no target or compiler is needed.
python3 scripts/package.py zlib 1.4 --kind source

# Build and package the Linux binary.
python3 scripts/package.py zlib 1.4 --kind binary \
  --target linux-x86_64-glibc217

# Create both a source bundle and a binary package.
python3 scripts/package.py zlib 1.4 --kind both \
  --target linux-x86_64-glibc217 --output dist

# Rebuild from an extracted source bundle using the same entry point.
python3 scripts/package.py zlib 1.4 --kind binary \
  --target linux-x86_64-glibc217
```

Use `--date YYYYMMDD` to set the artifact date and `--jobs N` to set parallel build jobs. By default, the date is the current UTC date and output goes to `dist/`.

Artifact names contain the package version, source/binary kind, target for binary packages, date, and the first 12 characters of the seekdb-deps commit. Target names describe the package ABI and do not encode the compiler. Each archive has a `.sha256` sidecar. `manifest.json` inside the archive records the full repository and upstream revisions, ordered patch digests, target profile, the actual build environment for provenance, license, and per-file hashes.

## Target profiles

The initial profiles cover these build configurations:

| Target | Build host | Target ABI |
| --- | --- | --- |
| `linux-x86_64-glibc217` | Linux x86_64 | x86_64, glibc 2.17 or newer |
| `linux-aarch64-glibc217` | Linux aarch64 | aarch64, glibc 2.17 or newer |
| `macos-arm64-min13` | macOS arm64 | arm64, deployment target macOS 13 |
| `windows-x86_64-md` | Windows x86_64 | x86_64, dynamic MSVC runtime (`/MD`) |
| `android-arm64-v8a-api26` | Linux, macOS, or Windows x86_64 | arm64-v8a, Android API 26 |

Profiles describe target ABI, target C flags where applicable, and supported build hosts, not the compiler used to produce a package. The shared initializer reads profile flags and passes the effective value to CMake; it does not define a second platform-specific CFLAGS value. It honors `CC` and `CXX` when supplied and otherwise selects host defaults; Linux compilers must be version 9.4.0 or newer, and the Windows C++ toolset must be 14.30 or newer. Linux builds use glibc 2.17-or-older build environments; the workflow uses native x86_64 and aarch64 runners with matching manylinux2014 images. The initializer loads the Visual Studio developer environment when needed. For Android, set `ANDROID_NDK_HOME` or `ANDROID_NDK_ROOT`; the NDK must be 27.2.12479018 or newer, while the profile records only ABI and API level. macOS builds require the Xcode command line tools. The actual compiler and SDK versions are recorded in the package manifest for provenance.

Profiles are build contracts, not validation claims. The zlib 1.4 recipe builds only upstream's `zlibstatic` target and stages the static library and public headers. Linux aarch64, macOS, Windows, and Android profiles have not been built.

## Licensing

Files maintained specifically for this repository are licensed under Apache License 2.0. Third-party dependencies retain their own licenses and notices; each binary package includes the dependency license under `share/licenses/<name>/LICENSE`.
