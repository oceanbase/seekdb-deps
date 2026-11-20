"""Build the LLVM 18 tools needed by seekdb's Windows compiler toolchain."""

from pathlib import Path
import shutil


TOOLS = (
    "clang", "lld", "llvm-ar", "llvm-cov", "llvm-cxxfilt", "llvm-dwp",
    "llvm-lib", "llvm-nm", "llvm-objcopy", "llvm-objdump", "llvm-profdata",
    "llvm-rc",
)
ALIASES = {
    "clang++": "clang",
    "clang-cl": "clang",
    "clang-cpp": "clang",
    "lld-link": "lld",
    "llvm-ranlib": "llvm-ar",
    "llvm-strip": "llvm-objcopy",
}


def build(ctx):
    if ctx.target.family != "windows" or ctx.target.architecture != "x86_64":
        raise ValueError("LLVM 18 tools are packaged only for Windows x86_64")

    options = {
        "CMAKE_BUILD_TYPE": "Release",
        "LLVM_TARGETS_TO_BUILD": "X86",
        "LLVM_ENABLE_PROJECTS": "clang;lld",
        "LLVM_INCLUDE_TESTS": "OFF",
        "LLVM_INCLUDE_EXAMPLES": "OFF",
        "LLVM_INCLUDE_BENCHMARKS": "OFF",
        "LLVM_INCLUDE_DOCS": "OFF",
        "LLVM_ENABLE_BINDINGS": "OFF",
        "LLVM_ENABLE_TERMINFO": "OFF",
        "LLVM_ENABLE_LIBEDIT": "OFF",
        "LLVM_ENABLE_LIBXML2": "OFF",
        "LLVM_ENABLE_ZSTD": "OFF",
        "LLVM_ENABLE_ZLIB": "OFF",
        "LLVM_BUILD_LLVM_DYLIB": "OFF",
        "LLVM_LINK_LLVM_DYLIB": "OFF",
        "CLANG_ENABLE_STATIC_ANALYZER": "OFF",
        "CLANG_ENABLE_ARCMT": "OFF",
    }
    configure = [
        "cmake", "-S", str(ctx.source_dir / "llvm"),
        "-B", str(ctx.build_dir), "-G", "Ninja",
    ] + ctx.cmake_args
    configure += ["-D{}={}".format(key, value) for key, value in sorted(options.items())]
    ctx.run(configure)
    ctx.run(["cmake", "--build", str(ctx.build_dir), "--config", "Release",
             "--parallel", str(ctx.jobs), "--target", *TOOLS])

    target_bin = ctx.stage_dir / "bin"
    target_bin.mkdir(parents=True)
    for name in TOOLS:
        source = ctx.build_dir / "bin" / (name + ".exe")
        if not source.is_file():
            raise ValueError("Required LLVM tool is missing: " + str(source))
        shutil.copy2(source, target_bin / source.name)
    for alias, target in ALIASES.items():
        shutil.copy2(target_bin / (target + ".exe"), target_bin / (alias + ".exe"))

    built_resource = ctx.build_dir / "lib" / "clang" / "18"
    resource_headers = built_resource / "include"
    if not (resource_headers / "stddef.h").is_file():
        raise ValueError("Clang resource headers are missing: " + str(resource_headers))
    target_resource = ctx.stage_dir / "lib" / "clang" / "18"
    shutil.copytree(resource_headers, target_resource / "include")
    resource_libraries = built_resource / "lib" / "windows"
    if resource_libraries.is_dir():
        shutil.copytree(resource_libraries, target_resource / "lib" / "windows")
