"""Build static libcurl against the pinned OpenSSL package for this target."""

import shlex
import subprocess


OPENSSL_VERSION = "3.5.9"


def _build_linux(ctx, openssl):
    environment = {
        "CPPFLAGS": "-I" + shlex.quote(str(openssl / "include")),
        "CFLAGS": ctx.observed_build_environment["cflags"],
        "LDFLAGS": "-L" + shlex.quote(str(openssl / "lib")) + " " + ctx.environment.get("LDFLAGS", ""),
        # Only use the pinned OpenSSL headers and archives.
        "PKG_CONFIG_PATH": "",
        "PKG_CONFIG_LIBDIR": str(openssl / "lib/pkgconfig"),
    }
    ctx.run([
        ctx.source_dir / "configure",
        "--with-openssl=" + str(openssl),
        "--disable-shared", "--enable-static", "--with-pic",
        "--without-zlib", "--without-brotli", "--without-zstd", "--without-libpsl",
        "--without-libidn2", "--without-nghttp2", "--without-libssh2",
        "--disable-ftp", "--disable-ldap", "--disable-ldaps", "--disable-cookies",
        "--disable-rtsp", "--disable-pop3", "--disable-smtp", "--disable-imap",
        "--disable-telnet", "--disable-tftp", "--disable-verbose", "--disable-gopher",
        "--with-ca-bundle=/etc/pki/tls/certs/ca-bundle.crt", "--without-ca-path",
    ], env=environment)
    ctx.run(["make", "-j", str(ctx.jobs), "-C", "lib"], env=environment)

    _install(ctx, ctx.build_dir / "lib/.libs/libcurl.a")
    source = ctx.build_dir / "link-check.c"
    _write_link_check(source)
    program = ctx.build_dir / "link-check"
    ctx.run([
        ctx.environment["CC"], *shlex.split(environment["CFLAGS"]),
        *shlex.split(ctx.environment.get("LDFLAGS", "")),
        "-I" + str(ctx.stage_dir / "include"), source, "-o", program,
        ctx.stage_dir / "lib/libcurl.a", openssl / "lib/libssl.a",
        openssl / "lib/libcrypto.a", "-ldl", "-pthread",
    ])
    ctx.run([program])


def _install(ctx, library):
    ctx.install(library, "lib/libcurl.a")
    for header in sorted((ctx.source_dir / "include/curl").glob("*.h")):
        ctx.install(header, "include/curl/" + header.name)


def _write_link_check(source):
    source.write_text(
        '#include <curl/curl.h>\n'
        '#include <string.h>\n'
        'int main(void) {\n'
        '    const curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);\n'
        '    if (!info || !info->ssl_version ||\n'
        '        strcmp(info->ssl_version, "OpenSSL/' + OPENSSL_VERSION + '")) return 1;\n'
        '    CURL *curl = curl_easy_init();\n'
        '    if (!curl) return 1;\n'
        '    curl_easy_cleanup(curl);\n'
        '    return 0;\n'
        '}\n',
        encoding="ascii",
    )


def _build_sdk_target(ctx, openssl):
    use_zlib = ctx.target.family in ("android", "ios")
    options = {
        "BUILD_CURL_EXE": True,
        "BUILD_STATIC_CURL": True,
        "ENABLE_CURL_MANUAL": False,
        "BUILD_SHARED_LIBS": False,
        "BUILD_STATIC_LIBS": True,
        "BUILD_TESTING": False,
        "BUILD_EXAMPLES": False,
        "BUILD_LIBCURL_DOCS": False,
        "BUILD_MISC_DOCS": False,
        "CURL_USE_OPENSSL": True,
        "CURL_USE_PKGCONFIG": False,
        "CURL_USE_LIBPSL": False,
        "CURL_USE_LIBSSH2": False,
        "USE_LIBIDN2": False,
        "USE_NGHTTP2": False,
        "CURL_ZLIB": use_zlib,
        "CURL_BROTLI": False,
        "CURL_ZSTD": False,
        "CURL_DISABLE_LDAP": True,
        "CURL_DISABLE_LDAPS": True,
        "CURL_CA_BUNDLE": "/etc/ssl/cert.pem" if ctx.target.family == "macos" else "none",
        "CURL_CA_PATH": "none",
        "OPENSSL_INCLUDE_DIR": str(openssl / "include"),
        "OPENSSL_SSL_LIBRARY": str(openssl / "lib/libssl.a"),
        "OPENSSL_CRYPTO_LIBRARY": str(openssl / "lib/libcrypto.a"),
        "OPENSSL_USE_STATIC_LIBS": True,
    }
    # The upstream curl executable exercises the full static link on this SDK.
    ctx.cmake(options=options, targets=("curl",))
    if ctx.target.family == "macos":
        curl = ctx.build_dir / "src/curl"
        version = subprocess.check_output([str(curl), "--version"], env=ctx.environment, text=True)
        if "OpenSSL/" + OPENSSL_VERSION not in version.split():
            raise ValueError("curl did not link the pinned OpenSSL: " + version)
    _install(ctx, ctx.build_dir / "lib/libcurl.a")


def build(ctx):
    openssl = ctx.source_dir / ("openssl-" + ctx.target.id)
    if ctx.target.family == "linux":
        _build_linux(ctx, openssl)
    else:
        _build_sdk_target(ctx, openssl)
    ctx.observed_build_environment["openssl_version"] = OPENSSL_VERSION
    ctx.observed_build_environment["openssl_link_check"] = "passed"
