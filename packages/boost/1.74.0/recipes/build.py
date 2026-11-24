"""Package the Boost headers used by header-only consumers."""


def build(ctx):
    headers = ctx.source_dir / "boost"
    for header in sorted(headers.rglob("*")):
        if header.is_file():
            ctx.install(header, "include/" + header.relative_to(ctx.source_dir).as_posix())
