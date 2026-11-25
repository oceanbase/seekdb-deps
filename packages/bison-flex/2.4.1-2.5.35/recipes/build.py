"""Build the native Bison and Flex executables used to generate seekdb parsers."""


def build(ctx):
    architecture = "aarch64" if ctx.target.architecture == "arm64" else ctx.target.architecture
    env = {"CFLAGS": ctx.observed_build_environment["cflags"], "M4": "m4"}
    if ctx.target.family == "macos":
        triple = architecture + "-apple-darwin"
        env["MACOSX_DEPLOYMENT_TARGET"] = ctx.profile["abi"]["minimum_os_version"]
    else:
        triple = architecture + "-unknown-linux-gnu"

    bison_build = ctx.build_dir / "bison"
    flex_build = ctx.build_dir / "flex"
    flex_source = ctx.source_dir / "flex-2.5.35"
    for source, directory in ((ctx.source_dir, bison_build), (flex_source, flex_build)):
        directory.mkdir()
        ctx.run([
            source / "configure", "--build=" + triple, "--prefix=/", "--disable-nls",
        ], cwd=directory, env=env)

    make = ["make", "-j", str(ctx.jobs)]
    ctx.run([*make, "-C", "lib"], cwd=bison_build)
    ctx.run([*make, "-C", "src", "bison"], cwd=bison_build)
    installed = bison_build / "installed"
    ctx.run(["make", "-C", "data", "install-data", "DESTDIR=" + str(installed)], cwd=bison_build)
    ctx.run([*make, "flex"], cwd=flex_build)

    ctx.install(bison_build / "src/bison", "bin/bison")
    ctx.install(flex_build / "flex", "bin/flex")
    data = installed / "share/bison"
    for path in sorted(data.rglob("*")):
        if path.is_file():
            ctx.install(path, "share/bison/" + path.relative_to(data).as_posix())
    ctx.install(flex_source / "COPYING", "share/licenses/bison-flex/FLEX-LICENSE")
