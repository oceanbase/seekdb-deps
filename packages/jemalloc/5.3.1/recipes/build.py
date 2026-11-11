"""Build the C/C++ jemalloc archive from seekdb's pinned Cargo crate."""

import os
from pathlib import Path
import re
import subprocess


def _android_tools(ctx):
    ndk = ctx.environment.get("ANDROID_NDK_HOME") or ctx.environment.get("ANDROID_NDK_ROOT")
    if not ndk:
        raise ValueError("Android NDK path is missing")
    bin_dir = Path(ndk) / "toolchains/llvm/prebuilt/linux-x86_64/bin"
    if not bin_dir.is_dir():
        raise ValueError("Android NDK toolchain is missing: " + str(bin_dir))
    api = ctx.target.document["abi"]["api_level"]
    return {
        "CC": str(bin_dir / ("aarch64-linux-android" + str(api) + "-clang")),
        "AR": str(bin_dir / "llvm-ar"),
        "NM": str(bin_dir / "llvm-nm"),
        "RANLIB": str(bin_dir / "llvm-ranlib"),
    }


def build(ctx):
    family = ctx.target.family
    output_dir = ctx.build_dir / "native-output"
    output_dir.mkdir(parents=True)
    environment = {
        "CARGO_TARGET_DIR": str(ctx.build_dir / "cargo-target"),
        "JEMALLOC_SYS_OUTPUT_DIR": str(output_dir),
        "NUM_JOBS": str(ctx.jobs),
    }
    configure = ["--with-jemalloc-prefix=je_"]
    flags = list(ctx.target.document.get("cflags", []))
    if family == "linux":
        configure.append("--disable-initial-exec-tls")
    elif family == "macos":
        environment["MACOSX_DEPLOYMENT_TARGET"] = ctx.target.document["abi"]["minimum_os_version"]
        flags.extend(["-O2", "-fPIC"])
    elif family == "windows":
        configure.append("--host=x86_64-w64-mingw32")
        runtime = ctx.target.document["abi"]["runtime"]
        existing_runtime = re.findall(r"/M[DT]d?\b", ctx.environment.get("CFLAGS", ""), re.I)
        if any(flag[2:4].upper() != runtime or flag.lower().endswith("d")
               for flag in existing_runtime):
            raise ValueError("CFLAGS conflicts with Windows target CRT: /" + runtime)
        flags.extend(["/O2", "/" + ctx.target.document["abi"]["runtime"],
                      "/D_WIN32_WINNT=0x0A00"])
        msys2_root = ctx.environment.get("MSYS2_LOCATION")
        if not msys2_root:
            raise ValueError("Windows jemalloc builds require MSYS2_LOCATION")
        tools = Path(msys2_root) / "usr/bin"
        environment["JEMALLOC_SYS_SHELL"] = str(tools / "sh.exe")
        environment["JEMALLOC_SYS_MAKE"] = str(tools / "make.exe")
        environment["MSYS2_ARG_CONV_EXCL"] = "*"
        environment["PATH"] = str(tools) + os.pathsep + ctx.environment["PATH"]
    elif family == "android":
        environment.update(_android_tools(ctx))
        configure.extend(["--host=aarch64-linux-android", "--with-lg-page=14",
                          "--disable-initial-exec-tls"])
        flags.extend(["-O2", "-fPIC"])
    else:
        raise ValueError("Unsupported jemalloc target family: " + family)

    if flags or ctx.environment.get("CFLAGS"):
        environment["CFLAGS"] = " ".join(flags + [ctx.environment.get("CFLAGS", "")]).strip()
    environment["JEMALLOC_SYS_CONFIGURE_ARGS"] = "\n".join(configure)
    ctx.observed_build_environment.update({
        "cargo_version": subprocess.check_output(["cargo", "--version"],
                                                env=ctx.environment, text=True).strip(),
        "jemalloc_features": ["stats"],
        "jemalloc_configure_args": configure,
        "jemalloc_cflags": environment.get("CFLAGS", ""),
    })
    command = ["cargo", "build", "--manifest-path", ctx.source_dir / "Cargo.toml",
               "--locked", "--offline", "--release", "--features", "stats"]
    if family == "android":
        command.extend(["--target", "aarch64-linux-android"])
    ctx.run(command, env=environment)

    library = "jemalloc.lib" if family == "windows" else "libjemalloc_pic.a"
    ctx.install(output_dir / "lib" / library, "lib/" + library)
    ctx.install(output_dir / "include/jemalloc/jemalloc.h", "include/jemalloc/jemalloc.h")
    ctx.install(ctx.source_dir / "vendor/jemalloc/COPYING", "share/licenses/jemalloc/COPYING")
