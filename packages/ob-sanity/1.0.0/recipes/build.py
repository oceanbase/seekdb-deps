"""Build the ob-sanity pass against LLVM 18.1.8's headers."""

import shlex


def build(ctx):
    llvm_source = ctx.source_dir / "llvm"
    llvm_build = ctx.build_dir / "llvm"
    target = {"x86_64": "X86", "arm64": "AArch64"}[ctx.target.architecture]
    ctx.run(["cmake", "-S", llvm_source / "llvm", "-B", llvm_build, "-G", "Ninja",
             *ctx.cmake_args, "-DLLVM_TARGETS_TO_BUILD=" + target,
             "-DLLVM_ENABLE_RTTI=ON", "-DLLVM_ENABLE_EH=ON",
             "-DLLVM_INCLUDE_TESTS=OFF", "-DLLVM_INCLUDE_EXAMPLES=OFF",
             "-DLLVM_INCLUDE_BENCHMARKS=OFF", "-DLLVM_ENABLE_ZLIB=OFF",
             "-DLLVM_ENABLE_ZSTD=OFF", "-DLLVM_ENABLE_TERMINFO=OFF"])
    ctx.run(["cmake", "--build", llvm_build, "--parallel", ctx.jobs, "--target", "llvm-headers"])

    plugin = ctx.build_dir / "libsanitypass.so"
    ctx.run([
        ctx.environment["CXX"], *shlex.split(ctx.observed_build_environment["cxxflags"]),
        "-std=c++20", "-DCPP_STANDARD=20", "-O2", "-fPIC", "-shared",
        "-static-libstdc++", "-static-libgcc", "-I" + str(llvm_build / "include"),
        "-I" + str(llvm_source / "llvm/include"), ctx.source_dir / "src/sanity/sanitypass.cpp",
        "-o", plugin,
    ])
    ctx.install(plugin, "lib/libsanitypass.so")
    ctx.install(ctx.source_dir / "include/sanity/sanity.h", "include/sanity/sanity.h")
    ctx.observed_build_environment["llvm_version"] = "18.1.8"
