"""Build the Windows libcurl DLL with Schannel and privately linked zlib."""

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import shutil
import subprocess
from threading import Thread


def _verify_windows(ctx, dll):
    dumpbin = shutil.which("dumpbin", path=ctx.environment["PATH"])
    if not dumpbin:
        raise ValueError("dumpbin was not found in the Visual Studio environment")
    imports = subprocess.check_output([dumpbin, "/dependents", str(dll)],
                                      env=ctx.environment, text=True)
    if re.search(r"^\s*zlib\S*\.dll\s*$", imports, re.I | re.M):
        raise ValueError("libcurl.dll must link zlib statically")

    check = ctx.build_dir / "link-check"
    check.mkdir()
    source = check / "main.c"
    source.write_text(
        """#include <curl/curl.h>
#include <string.h>

int main(int argc, char **argv) {
    const curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);
    if (argc != 2 || !info || !info->ssl_version ||
        strncmp(info->ssl_version, "Schannel", 8) || !info->libz_version)
        return 1;
    CURL *curl = curl_easy_init();
    if (!curl)
        return 1;
    curl_easy_setopt(curl, CURLOPT_URL, argv[1]);
    curl_easy_setopt(curl, CURLOPT_ACCEPT_ENCODING, "gzip");
    curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURLcode result = curl_easy_perform(curl);
    curl_easy_cleanup(curl);
    return result != CURLE_OK;
}
""",
        encoding="ascii",
    )
    client = check / "link-check.exe"
    ctx.run([ctx.observed_build_environment["c_compiler"], "/nologo", "/MD",
             "/I" + str(ctx.stage_dir / "include"), source, "/Fe" + str(client),
             ctx.stage_dir / "lib/libcurl.lib"], cwd=check)
    shutil.copy2(dll, check / dll.name)
    payload = b"seekdb-deps curl gzip check"
    compressed = gzip.compress(payload)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = subprocess.check_output(
                [str(client), "http://127.0.0.1:{}/".format(server.server_port)],
                env=ctx.environment, cwd=check, timeout=20,
            )
            if response != payload:
                raise ValueError("libcurl did not decode the gzip response")
        finally:
            server.shutdown()
            thread.join()


def build(ctx):
    zlib = ctx.source_dir / "zlib"
    options = {
        "BUILD_CURL_EXE": False,
        "BUILD_SHARED_LIBS": True,
        "BUILD_STATIC_LIBS": False,
        "IMPORT_LIB_SUFFIX": "",
        "BUILD_TESTING": False,
        "BUILD_EXAMPLES": False,
        "BUILD_LIBCURL_DOCS": False,
        "BUILD_MISC_DOCS": False,
        "CURL_STATIC_CRT": False,
        "CURL_USE_SCHANNEL": True,
        "CURL_USE_OPENSSL": False,
        "CURL_USE_PKGCONFIG": False,
        "CURL_USE_LIBPSL": False,
        "CURL_USE_LIBSSH2": False,
        "USE_LIBIDN2": False,
        "USE_NGHTTP2": False,
        "CURL_BROTLI": False,
        "CURL_ZSTD": False,
        "CURL_ZLIB": True,
        "ZLIB_INCLUDE_DIR": str(zlib / "include"),
        "ZLIB_LIBRARY": str(zlib / "lib/libzs.lib"),
    }
    ctx.cmake(options=options, targets=("libcurl_shared",))
    ctx.install(ctx.build_dir / "lib/libcurl.dll", "bin/libcurl.dll")
    ctx.install(ctx.build_dir / "lib/libcurl.lib", "lib/libcurl.lib")
    for header in sorted((ctx.source_dir / "include/curl").glob("*.h")):
        ctx.install(header, "include/curl/" + header.name)
    ctx.install(zlib / "share/licenses/zlib/LICENSE", "share/licenses/zlib/LICENSE")
    _verify_windows(ctx, ctx.stage_dir / "bin/libcurl.dll")
    ctx.observed_build_environment.update({
        "curl_tls_backend": "Schannel",
        "zlib_linkage": "static",
        "windows_gzip_check": "passed",
    })
