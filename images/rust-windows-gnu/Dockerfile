FROM ubuntu:26.04@sha256:d66b366dac61b575bee2aef07e9c26e8070096fbe84a3d4d3eb0da069b5d76db

LABEL org.opencontainers.image.source="https://github.com/oceanbase/seekdb-deps"

ARG RUST_VERSION
ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    PATH=/opt/cargo/bin:$PATH

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git \
        gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' -fsSL --retry 3 https://sh.rustup.rs -o /tmp/rustup-init.sh \
    && sh /tmp/rustup-init.sh -y --no-modify-path --default-toolchain none \
    && rustup toolchain install "$RUST_VERSION" --profile minimal \
        --component clippy --target x86_64-pc-windows-gnu \
    && rustup default "$RUST_VERSION" \
    && rm /tmp/rustup-init.sh

# Exercise both compilers with the preinstalled toolchain before publishing.
RUN --network=none \
    printf 'pub fn check() {}\n' \
        | rustc --target x86_64-pc-windows-gnu --crate-type lib --emit metadata -o /tmp/check.rmeta - \
    && printf '#include <windows.h>\nDWORD check() { return 0; }\n' \
        | x86_64-w64-mingw32-g++ -x c++ -c - -o /tmp/check.o \
    && rm /tmp/check.rmeta /tmp/check.o
