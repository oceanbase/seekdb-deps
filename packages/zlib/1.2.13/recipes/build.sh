#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
: "${SOURCE_DIR:=${PACKAGE_DIR}/source}"
: "${BUILD_DIR:?BUILD_DIR must point to a temporary build directory}"
: "${DESTDIR:?DESTDIR must point to the package staging directory}"
: "${PREFIX:=/usr/local/oceanbase/deps/devel}"
: "${JOBS:=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)}"

mkdir -p "${BUILD_DIR}" "${DESTDIR}"
rm -rf "${BUILD_DIR}/source" "${DESTDIR:?}"/*
cp -a "${SOURCE_DIR}" "${BUILD_DIR}/source"

cd "${BUILD_DIR}/source"
export CFLAGS="${CFLAGS:--O2 -fPIC -march=x86-64}"
./configure --static --prefix="${PREFIX}"
make ARFLAGS=rcD -j"${JOBS}"
make install DESTDIR="${DESTDIR}"

test -f "${DESTDIR}${PREFIX}/lib/libz.a"
test -f "${DESTDIR}${PREFIX}/include/zlib.h"
test -f "${DESTDIR}${PREFIX}/include/zconf.h"

mkdir -p "${DESTDIR}${PREFIX}/share/licenses/zlib"
cp "${SOURCE_DIR}/LICENSE" "${DESTDIR}${PREFIX}/share/licenses/zlib/LICENSE"
