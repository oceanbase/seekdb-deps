"""Build and stage GoogleTest and GoogleMock libraries and headers."""


def build(ctx):
    options = {
        "BUILD_GMOCK": True,
        "INSTALL_GTEST": True,
    }
    if ctx.target.family == "windows":
        options["gtest_force_shared_crt"] = True

    ctx.cmake(options=options, install=True)
