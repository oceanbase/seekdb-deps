"""Build the LLVM tools and compiler runtimes used by seekdb."""

from pathlib import Path
import shlex
import shutil
import subprocess


RESOURCE_VERSION = "18"
TOOLS = (
    "clang", "lld", "llvm-ar", "llvm-cxxfilt", "llvm-dwp",
    "llvm-nm", "llvm-objcopy", "llvm-objdump",
)
WINDOWS_TOOLS = (
    "llvm-cov", "llvm-lib", "llvm-pdbutil", "llvm-profdata",
    "llvm-rc", "llvm-readobj",
)
ALIASES = {
    "clang++": "clang",
    "llvm-ranlib": "llvm-ar",
    "llvm-strip": "llvm-objcopy",
}
WINDOWS_ALIASES = {
    "clang-cl": "clang",
    "clang-cpp": "clang",
    "lld-link": "lld",
}


def _output(ctx, argv):
    return subprocess.check_output(
        [str(value) for value in argv], env=ctx.environment, text=True,
    ).strip()


def _configure(ctx, source, build, options, *, base_args=()):
    args = ["cmake", "-S", source, "-B", build, "-G", "Ninja", *base_args]
    args.extend("-D{}={}".format(key, value) for key, value in sorted(options.items()))
    ctx.run(args)


def _stage_llvm(ctx, tools, aliases, suffix):
    target_bin = ctx.stage_dir / "bin"
    for tool in tools:
        filename = tool + suffix
        ctx.install(ctx.build_dir / "bin" / filename, "bin/" + filename)
    for name, target in aliases.items():
        destination = target_bin / (name + suffix)
        if suffix:
            shutil.copy2(target_bin / (target + suffix), destination)
        else:
            destination.symlink_to(target)

    built_library = ctx.build_dir / "lib"
    target_library = ctx.stage_dir / "lib"
    family = ctx.target.family
    if family != "windows":
        target_library.mkdir()
        pattern = "libLLVM*.so*" if family == "linux" else "libLLVM*.dylib"
        for source in built_library.glob(pattern):
            destination = target_library / source.name
            if source.is_symlink():
                destination.symlink_to(source.readlink())
            elif source.is_file():
                shutil.copy2(source, destination)
        if family == "linux":
            strip = _output(ctx, [ctx.environment["CC"], "-print-prog-name=strip"])
            for binary in (target_bin / name for name in tools):
                ctx.run([strip, "--strip-unneeded", binary])
                ctx.run(["patchelf", "--set-rpath", "$ORIGIN/../lib", binary])
            for library in target_library.iterdir():
                if library.is_file() and not library.is_symlink():
                    ctx.run([strip, "--strip-unneeded", library])
                    ctx.run(["patchelf", "--set-rpath", "$ORIGIN", library])

    headers = built_library / "clang" / RESOURCE_VERSION / "include"
    shutil.copytree(headers, target_library / "clang" / RESOURCE_VERSION / "include")
    return target_bin


def _linux_driver_args(ctx):
    gcc = ctx.environment["CC"]
    gcc_library = Path(_output(ctx, [gcc, "-print-file-name=libgcc.a"]))
    if not gcc_library.is_absolute() or not gcc_library.is_file():
        raise ValueError("Missing GCC runtime for compiler-rt: " + str(gcc_library))
    flags = ["--gcc-install-dir=" + str(gcc_library.parent)]
    sysroot = ctx.observed_build_environment["sysroot"]
    if sysroot:
        flags.append("--sysroot=" + sysroot)
    return flags


def _build_compiler_rt(ctx, target_bin):
    windows = ctx.target.family == "windows"
    clang = target_bin / ("clang.exe" if windows else "clang")
    triple = _output(ctx, [clang, "-dumpmachine"])
    cc = target_bin / "clang-cl.exe" if windows else clang
    cxx = cc if windows else target_bin / "clang++"
    options = {
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_C_COMPILER": cc,
        "CMAKE_CXX_COMPILER": cxx,
        "CMAKE_ASM_COMPILER": clang,
        "CMAKE_C_COMPILER_TARGET": triple,
        "CMAKE_CXX_COMPILER_TARGET": triple,
        "CMAKE_ASM_COMPILER_TARGET": triple,
        "COMPILER_RT_DEFAULT_TARGET_ONLY": "ON",
        "COMPILER_RT_INCLUDE_TESTS": "OFF",
        "COMPILER_RT_BUILD_BUILTINS": "ON",
        "COMPILER_RT_BUILD_PROFILE": "OFF",
        "COMPILER_RT_BUILD_XRAY": "OFF",
        "COMPILER_RT_BUILD_LIBFUZZER": "OFF",
        "COMPILER_RT_BUILD_MEMPROF": "OFF",
        "COMPILER_RT_BUILD_ORC": "OFF",
        "COMPILER_RT_BUILD_GWP_ASAN": "OFF",
        "COMPILER_RT_BUILD_SANITIZERS": "OFF" if windows else "ON",
        "LLVM_ENABLE_PER_TARGET_RUNTIME_DIR": "OFF",
    }
    if windows:
        options["CMAKE_MSVC_RUNTIME_LIBRARY"] = ctx.target.document["abi"]["runtime_library"]
    else:
        flags = shlex.join(_linux_driver_args(ctx))
        options.update({
            "CMAKE_C_FLAGS": flags,
            "CMAKE_CXX_FLAGS": flags,
            "CMAKE_ASM_FLAGS": flags,
            "CMAKE_EXE_LINKER_FLAGS": "-fuse-ld=lld",
            "CMAKE_SHARED_LINKER_FLAGS": "-fuse-ld=lld",
            # LLVM 18 builds the UBSan runtime with sanitizer_common.
            "COMPILER_RT_SANITIZERS_TO_BUILD": "asan",
        })
    runtime_build = ctx.build_dir / "compiler-rt"
    _configure(ctx, ctx.source_dir / "compiler-rt", runtime_build, options)
    ctx.run(["cmake", "--build", runtime_build, "--parallel", ctx.jobs])
    ctx.run(["cmake", "--install", runtime_build, "--prefix",
             ctx.stage_dir / "lib" / "clang" / RESOURCE_VERSION])


def _verify(ctx, target_bin):
    source = ctx.build_dir / "link-check.cpp"
    if ctx.target.family == "windows":
        source.write_text("int main() { return 0; }\n", encoding="ascii")
        exe = ctx.build_dir / "link-check.exe"
        pdb = ctx.build_dir / "link-check.pdb"
        builtins = ctx.stage_dir / "lib/clang" / RESOURCE_VERSION / "lib/windows/clang_rt.builtins-x86_64.lib"
        ctx.run([target_bin / "clang-cl.exe", "/MD", "/Z7", "-fuse-ld=lld",
                 source, builtins, "/Fe" + str(exe), "/link", "/debug", "/pdb:" + str(pdb)])
        ctx.run([target_bin / "llvm-readobj.exe", "--coff-debug-directory", exe])
        ctx.run([target_bin / "llvm-pdbutil.exe", "dump", "-summary", pdb])
        ctx.run([exe])
    elif ctx.target.family == "linux":
        source.write_text(
            "#include <vector>\nint main() { return std::vector<int>{42}.front() != 42; }\n",
            encoding="ascii",
        )
        driver_args = _linux_driver_args(ctx)
        for name, flags in (
            ("plain", []),
            ("asan", ["-fsanitize=address"]),
            ("ubsan", ["-fsanitize=undefined"]),
        ):
            exe = ctx.build_dir / ("link-check-" + name)
            ctx.run([target_bin / "clang++", *driver_args, "-fuse-ld=lld",
                     "-static-libstdc++", "-rtlib=compiler-rt", "-unwindlib=libgcc",
                     *flags, source, "-o", exe])
            ctx.run([exe])
    else:
        source.write_text("int main() { return 0; }\n", encoding="ascii")
        target = "{}-apple-macos{}".format(
            ctx.target.architecture, ctx.observed_build_environment["deployment_target"],
        )
        ctx.run([target_bin / "clang++", "--target=" + target, "-fuse-ld=lld",
                 "-nostdlib", source, "-o", ctx.build_dir / "link-check"])


def build(ctx):
    family = ctx.target.family
    llvm_target = {"x86_64": "X86", "arm64": "AArch64"}[ctx.target.architecture]
    windows = family == "windows"
    tools = TOOLS
    if windows:
        tools += WINDOWS_TOOLS
    elif family == "linux":
        tools += ("llvm-symbolizer",)
    aliases = dict(ALIASES)
    if windows:
        aliases.update(WINDOWS_ALIASES)
    else:
        aliases["ld.lld" if family == "linux" else "ld64.lld"] = "lld"

    options = {
        "LLVM_TARGETS_TO_BUILD": llvm_target,
        "LLVM_ENABLE_PROJECTS": "clang;lld",
        "LLVM_BUILD_LLVM_DYLIB": "OFF" if windows else "ON",
        "LLVM_LINK_LLVM_DYLIB": "OFF" if windows else "ON",
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
        "CLANG_LINK_CLANG_DYLIB": "OFF",
        "CLANG_ENABLE_STATIC_ANALYZER": "OFF",
        "CLANG_ENABLE_ARCMT": "OFF",
    }
    if family == "linux":
        options.update({
            "CMAKE_BUILD_WITH_INSTALL_RPATH": "ON",
            "CMAKE_INSTALL_RPATH": "$ORIGIN/../lib",
            "CMAKE_EXE_LINKER_FLAGS": "-static-libstdc++ -static-libgcc",
            "CMAKE_SHARED_LINKER_FLAGS": "-static-libstdc++ -static-libgcc",
        })
    elif family == "macos":
        options.update({
            "CMAKE_BUILD_WITH_INSTALL_RPATH": "ON",
            "CMAKE_INSTALL_RPATH": "@loader_path/../lib",
            "CMAKE_MACOSX_RPATH": "ON",
        })
    _configure(ctx, ctx.source_dir / "llvm", ctx.build_dir, options, base_args=ctx.cmake_args)
    ctx.run(["cmake", "--build", ctx.build_dir, "--config", "Release", "--parallel",
             ctx.jobs, "--target", *tools])
    target_bin = _stage_llvm(ctx, tools, aliases, ".exe" if windows else "")
    if family in ("linux", "windows"):
        _build_compiler_rt(ctx, target_bin)
    _verify(ctx, target_bin)
