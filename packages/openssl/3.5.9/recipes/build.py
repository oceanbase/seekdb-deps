"""Build OpenSSL's static libraries and stage its public headers."""

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess


TARGETS = {
    ("linux", "x86_64"): "linux-x86_64",
    ("linux", "arm64"): "linux-aarch64",
    ("macos", "arm64"): "darwin64-arm64-cc",
    ("wasm", "wasm32"): "linux-generic32",
    ("windows", "x86_64"): "VC-WIN64A",
    ("android", "arm64-v8a"): "android-arm64",
}


def build(ctx):
    family = ctx.target.family
    if family == "ios":
        sdk = ctx.target.document["abi"]["sdk"]
        target = "iossimulator-arm64-xcrun" if sdk == "iphonesimulator" else "ios64-xcrun"
    else:
        target = TARGETS[(family, ctx.target.architecture)]
    environment = {}
    # Embed the default, base, and legacy providers in libcrypto so consumers
    # do not need a separately installed OpenSSL modules directory.
    options = ["no-shared", "no-tests", "no-apps", "no-module", "no-pinshared"]

    if family == "windows":
        if ctx.target.document["abi"]["runtime"] != "MT":
            raise ValueError("Windows OpenSSL must use the static MSVC runtime (/MT)")
        if re.search(r"/MDd?\b|/MTd\b", ctx.environment.get("CFLAGS", ""), re.I):
            raise ValueError("CFLAGS conflicts with the Windows /MT target")
        # OpenSSL 3.5 uses /MT for its static libraries when no-shared is set.
        # CreateProcess searches the parent process PATH, not the environment
        # passed to the child; VS tools are on the initialized child PATH.
        build_tool = shutil.which("nmake", path=ctx.environment["PATH"])
        if not build_tool:
            raise ValueError("nmake was not found in the Visual Studio developer environment")
        libraries = ("libcrypto.lib", "libssl.lib")
    else:
        build_tool = "make"
        libraries = ("libcrypto.a", "libssl.a")
        options.extend(ctx.target.document.get("cflags", []))
        if "-fPIC" not in options:
            options.append("-fPIC")
        if family == "macos":
            minimum = ctx.target.document["abi"]["minimum_os_version"]
            environment["MACOSX_DEPLOYMENT_TARGET"] = minimum
            options.append("-mmacosx-version-min=" + minimum)
        elif family == "ios":
            minimum = ctx.target.document["abi"]["minimum_os_version"]
            sdk_path = subprocess.check_output(
                ["xcrun", "--sdk", sdk, "--show-sdk-path"],
                env=ctx.environment, text=True,
            ).strip()
            triple = "arm64-apple-ios" + minimum
            if sdk == "iphonesimulator":
                triple += "-simulator"
            minimum_flag = ("-mios-simulator-version-min=" if sdk == "iphonesimulator"
                            else "-miphoneos-version-min=")
            environment["CFLAGS"] = " ".join([
                ctx.environment.get("CFLAGS", ""), "-O2", "-target", triple,
                "-isysroot", shlex.quote(sdk_path), minimum_flag + minimum,
            ]).strip()
        elif family == "wasm":
            environment.update({"AR": "emar", "RANLIB": "emranlib"})
            environment["CFLAGS"] = " ".join([
                ctx.environment.get("CFLAGS", ""), "-pthread", "-msimd128",
            ]).strip()
            options.extend([
                "no-asm", "no-dso", "no-engine", "no-async", "no-sock",
                "no-afalgeng", "no-ui-console", "-pthread",
            ])
        elif family == "android":
            ndk = Path(ctx.environment["ANDROID_NDK_HOME"])
            bin_dir = ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin"
            environment["PATH"] = str(bin_dir) + os.pathsep + ctx.environment["PATH"]
            options.append("-D__ANDROID_API__=" + str(ctx.target.document["abi"]["api_level"]))

    ctx.observed_build_environment.update({
        "openssl_target": target,
        "openssl_configure_options": options,
    })
    ctx.run(["perl", ctx.source_dir / "Configure", target, *options],
            cwd=ctx.build_dir, env=environment)
    ctx.run([build_tool, "build_libs"], cwd=ctx.build_dir, env=environment)

    for library in libraries:
        ctx.install(ctx.build_dir / library, "lib/" + library)

    # The build tree adds generated public headers, notably opensslconf.h.
    for directory in (ctx.source_dir / "include/openssl", ctx.build_dir / "include/openssl"):
        if directory.is_dir():
            for header in sorted(directory.glob("*.h")):
                ctx.install(header, "include/openssl/" + header.name)
