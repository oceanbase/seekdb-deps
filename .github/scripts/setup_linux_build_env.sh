#!/usr/bin/env bash
set -euo pipefail

archive="$RUNNER_TEMP/micromamba.tar.bz2"
binary_dir="$RUNNER_TEMP/micromamba-bin"
environment_dir="$RUNNER_TEMP/seekdb-build-env"
mkdir -p "$binary_dir"
curl --fail --location --retry 3 --silent --show-error \
  "https://micro.mamba.pm/api/micromamba/linux-${CONDA_ARCH}/2.4.0" \
  --output "$archive"
echo "${MICROMAMBA_SHA256}  $archive" | sha256sum --check
tar -xj -f "$archive" -C "$binary_dir" --strip-components=1 bin/micromamba

export MAMBA_ROOT_PREFIX="$RUNNER_TEMP/micromamba-root"
"$binary_dir/micromamba" create --yes --prefix "$environment_dir" \
  --channel conda-forge --strict-channel-priority \
  python=3.11 cmake ninja patchelf \
  "gcc_linux-${CONDA_ARCH}=12" \
  "gxx_linux-${CONDA_ARCH}=12" \
  "sysroot_linux-${CONDA_ARCH}=2.17"
echo "$environment_dir/bin" >> "$GITHUB_PATH"
echo "CONDA_PREFIX=$environment_dir" >> "$GITHUB_ENV"
