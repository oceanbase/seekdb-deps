"""Build the Windows ports of Bison and Flex, including Bison's skeletons."""

def build(ctx):
    ctx.cmake(options={"CMAKE_POLICY_VERSION_MINIMUM": "3.15"}, targets=("win_bison", "win_flex"))

    output = ctx.source_dir / "bin/Release"
    for tool in ("win_bison.exe", "win_flex.exe"):
        ctx.install(output / tool, "bin/" + tool)
    data = output / "data"
    for path in sorted(data.rglob("*")):
        if path.is_file():
            ctx.install(path, "bin/data/" + path.relative_to(data).as_posix())
    ctx.install(ctx.source_dir / "flex/src/COPYING", "share/licenses/bison-flex/FLEX-LICENSE")
