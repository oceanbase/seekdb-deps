"""ICU4C static library build recipe."""

from pathlib import Path


def _install_tree(ctx, source, destination):
    for path in sorted(source.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            ctx.install(path, destination + "/" + relative)


def _configure(ctx, build_dir, source_dir, prefix, extra=(), env=None):
    build_dir.mkdir(parents=True, exist_ok=True)
    ctx.run([
        str(source_dir / "configure"),
        "--prefix=" + str(prefix),
        "--enable-static",
        "--disable-shared",
        "--with-data-packaging=archive",
        "--disable-tests",
        "--disable-samples",
        "--disable-extras",
        "--disable-icuio",
        "--disable-layout",
        *extra,
    ], cwd=build_dir, env=env)


def _build_unix(ctx, source):
    build_dir = ctx.build_dir / "icu-build"
    prefix = Path("install-root")
    extra = []
    env = {}

    if ctx.target.family == "linux":
        profile_flags = ctx.profile.get("cflags", [])
        env["CFLAGS"] = " ".join(profile_flags)
        env["CXXFLAGS"] = " ".join(profile_flags)
    elif ctx.target.family == "macos":
        deployment_target = ctx.profile["abi"]["minimum_os_version"]
        env["MACOSX_DEPLOYMENT_TARGET"] = deployment_target
        env["CFLAGS"] = "-mmacosx-version-min=" + deployment_target
        env["CXXFLAGS"] = "-mmacosx-version-min=" + deployment_target
        env["LDFLAGS"] = "-mmacosx-version-min=" + deployment_target
    elif ctx.target.family == "android":
        ndk = Path(ctx.environment["ANDROID_NDK_HOME"])
        toolchain_bin = ndk / "toolchains" / "llvm" / "prebuilt"
        host = "linux-x86_64"
        clang_dir = toolchain_bin / host / "bin"
        api = str(ctx.profile["abi"]["api_level"])
        triple = "aarch64-linux-android"
        env["CC"] = str(clang_dir / (triple + api + "-clang"))
        env["CXX"] = str(clang_dir / (triple + api + "-clang++"))
        extra.append("--host=" + triple)

        host_build = ctx.build_dir / "icu-host-tools"
        _configure(ctx, host_build, source, Path("install-root"), [],
                   {"CC": "cc", "CXX": "c++"})
        ctx.run(["make", "--jobs=" + str(ctx.jobs), "-C", str(host_build / "tools")], cwd=ctx.build_dir)
        extra.append("--with-cross-build=" + str(host_build.resolve()))

    _configure(ctx, build_dir, source, prefix, extra, env)
    ctx.run(["make", "--jobs=" + str(ctx.jobs)], cwd=build_dir)
    ctx.run(["make", "install"], cwd=build_dir)

    installed = build_dir / prefix
    _install_tree(ctx, installed / "include", "include")
    library_dir = installed / "lib"
    for name in ("libicuuc.a", "libicui18n.a", "libicudata.a"):
        candidates = list(library_dir.glob(name))
        if not candidates:
            raise ValueError("ICU build did not produce " + name)
        ctx.install(candidates[0], "lib/" + name)
        if name == "libicudata.a":
            # SeekDB's Linux link configuration calls the stub data archive
            # libicustubdata.a; ICU 69.1 names the same stub archive libicudata.a.
            ctx.install(candidates[0], "lib/libicustubdata.a")


def _build_windows(ctx, source):
    solution = source / "allinone" / "allinone.sln"
    ctx.run([
        "msbuild", str(solution), "/m:" + str(ctx.jobs),
        "/p:Configuration=Release", "/p:Platform=x64", "/p:SkipUWP=true",
    ], cwd=source)

    output_root = source / ".."
    include = source / "common"
    _install_tree(ctx, include / "unicode", "include/unicode")
    for name in ("icuuc.lib", "icuin.lib", "icudt.lib"):
        candidates = list(output_root.rglob(name))
        if not candidates:
            raise ValueError("ICU Windows build did not produce " + name)
        ctx.install(candidates[0], "lib/" + name)


def build(ctx):
    source = ctx.source_dir / "source"
    if ctx.target.family == "windows":
        _build_windows(ctx, source)
    elif ctx.target.family in ("linux", "macos", "android"):
        _build_unix(ctx, source)
    else:
        raise ValueError("Unsupported ICU target family: " + ctx.target.family)
