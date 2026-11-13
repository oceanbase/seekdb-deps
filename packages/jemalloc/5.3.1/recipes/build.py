"""Build the official jemalloc 5.3.1 release with its configure and Makefile."""

import os
from pathlib import Path
import re


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
    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    environment = {}
    configure = [
        "--with-version=VERSION",
        "--enable-static",
        "--disable-shared",
        "--disable-cxx",
        "--disable-doc",
        "--enable-stats",
        "--with-jemalloc-prefix=je_",
    ]
    flags = list(ctx.target.document.get("cflags", []))
    shell = "sh"
    make = "make"

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
        flags.extend(["/O2", "/" + runtime, "/D_WIN32_WINNT=0x0A00"])
        msys2_root = ctx.environment.get("MSYS2_LOCATION")
        if not msys2_root:
            raise ValueError("Windows jemalloc builds require MSYS2_LOCATION")
        tools = Path(msys2_root) / "usr/bin"
        shell = str(tools / "sh.exe")
        make = str(tools / "make.exe")
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
    ctx.observed_build_environment.update({
        "jemalloc_configure_args": configure,
        "jemalloc_cflags": environment.get("CFLAGS", ""),
    })
    configure_script = (ctx.source_dir / "configure").as_posix()
    ctx.run([shell, configure_script, *configure], env=environment)
    ctx.run([make, "-j{}".format(ctx.jobs), "build_lib_static"], env=environment)

    library_dir = ctx.build_dir / "lib"
    if family == "windows":
        candidates = [library_dir / name for name in
                      ("jemalloc_pic.lib", "jemalloc_s.lib", "jemalloc.lib")]
        library = next((path for path in candidates if path.is_file()), None)
        output_name = "jemalloc.lib"
    else:
        library = library_dir / "libjemalloc_pic.a"
        output_name = library.name
    if library is None or not library.is_file():
        raise ValueError("jemalloc static archive is missing from " + str(library_dir))
    ctx.install(library, "lib/" + output_name)
    ctx.install(ctx.build_dir / "include/jemalloc/jemalloc.h", "include/jemalloc/jemalloc.h")
