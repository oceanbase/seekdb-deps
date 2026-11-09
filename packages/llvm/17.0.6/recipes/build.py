"""Build only the LLVM tools used by seekdb's Linux Clang toolchain."""

from pathlib import Path
import shutil
import subprocess


PREFIX = Path("devtools")
TOOLS = (
    "clang", "lld", "llvm-ar", "llvm-cov", "llvm-cxxfilt", "llvm-dwp",
    "llvm-nm", "llvm-objcopy", "llvm-objdump",
)
ALIASES = {
    "clang++": "clang",
    "ld.lld": "lld",
    "llvm-ranlib": "llvm-ar",
    "llvm-strip": "llvm-objcopy",
}


def build(ctx):
    if ctx.target.family != "linux":
        raise ValueError("LLVM toolchain package is only used by Linux seekdb builds")
    llvm_target = {"x86_64": "X86", "arm64": "AArch64"}.get(ctx.target.architecture)
    if llvm_target is None:
        raise ValueError("Unsupported LLVM host architecture: " + ctx.target.architecture)

    options = {
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_BUILD_WITH_INSTALL_RPATH": "ON",
        "CMAKE_INSTALL_RPATH": "$ORIGIN/../lib",
        "CMAKE_EXE_LINKER_FLAGS": "-static-libstdc++ -static-libgcc",
        "CMAKE_SHARED_LINKER_FLAGS": "-static-libstdc++ -static-libgcc",
        "LLVM_TARGETS_TO_BUILD": llvm_target,
        "LLVM_ENABLE_PROJECTS": "clang;lld",
        "LLVM_BUILD_LLVM_DYLIB": "ON",
        "LLVM_LINK_LLVM_DYLIB": "ON",
        "CLANG_LINK_CLANG_DYLIB": "OFF",
        "LLVM_ENABLE_RTTI": "ON",
        "LLVM_ENABLE_EH": "ON",
        "LLVM_ENABLE_DUMP": "ON",
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
        "CLANG_ENABLE_STATIC_ANALYZER": "OFF",
        "CLANG_ENABLE_ARCMT": "OFF",
    }
    configure = [
        "cmake", "-S", str(ctx.source_dir / "llvm"),
        "-B", str(ctx.build_dir), "-G", "Ninja",
        "-DCMAKE_INSTALL_PREFIX=/" + PREFIX.as_posix(),
    ] + ctx.cmake_args
    configure += ["-D{}={}".format(key, value) for key, value in sorted(options.items())]
    ctx.run(configure)
    ctx.run(["cmake", "--build", str(ctx.build_dir), "--parallel", str(ctx.jobs),
             "--target", *TOOLS])

    target_prefix = ctx.stage_dir / PREFIX
    target_bin = target_prefix / "bin"
    target_bin.mkdir(parents=True)
    for tool in TOOLS:
        source = ctx.build_dir / "bin" / tool
        if not source.is_file():
            raise ValueError("Required LLVM tool is missing: " + str(source))
        shutil.copy2(source, target_bin / tool)
    for name, target in ALIASES.items():
        (target_bin / name).symlink_to(target)

    built_library = ctx.build_dir / "lib"
    target_library = target_prefix / "lib"
    target_library.mkdir(parents=True)
    for source in built_library.glob("libLLVM*.so*"):
        destination = target_library / source.name
        if source.is_symlink():
            destination.symlink_to(source.readlink())
        elif source.is_file():
            shutil.copy2(source, destination)
    if not (target_library / "libLLVM-17.so").exists():
        raise ValueError("Required LLVM shared library is missing: libLLVM-17.so")

    strip_tool = subprocess.check_output(
        [ctx.environment["CC"], "-print-prog-name=strip"],
        env=ctx.environment, text=True,
    ).strip()
    for binary in (target_bin / name for name in TOOLS):
        ctx.run([strip_tool, "--strip-unneeded", str(binary)])
        ctx.run(["patchelf", "--set-rpath", "$ORIGIN/../lib", str(binary)])
    for library in target_library.iterdir():
        if library.is_file() and not library.is_symlink():
            ctx.run([strip_tool, "--strip-unneeded", str(library)])
            ctx.run(["patchelf", "--set-rpath", "$ORIGIN", str(library)])

    resource_headers = built_library / "clang" / "17" / "include"
    if not (resource_headers / "stddef.h").is_file():
        raise ValueError("Clang resource headers are missing: " + str(resource_headers))
    shutil.copytree(resource_headers, target_library / "clang" / "17" / "include")
