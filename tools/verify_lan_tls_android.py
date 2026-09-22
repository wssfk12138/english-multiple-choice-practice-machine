"""Real Python/OpenSSL to Java/OkHttp TLS probe, synthetic data only."""
from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from backend.app.lan_tls import load_or_create_identity, tls_server_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--android-root", type=Path, required=True)
    args = parser.parse_args()
    cache = Path.home() / ".gradle/caches/modules-2/files-2.1"
    coordinates = [
        ("com.squareup.okhttp3", "okhttp", "4.12.0"),
        ("com.squareup.okio", "okio-jvm", "3.6.0"),
        ("org.jetbrains.kotlin", "kotlin-stdlib", "*"),
        ("org.jetbrains", "annotations", "*"),
    ]
    jars = []
    for group, artifact, version in coordinates:
        candidates = sorted((cache / group / artifact).glob(f"{version}/*/{artifact}-*.jar"))
        if not candidates:
            raise RuntimeError(f"Missing installed Java dependency: {artifact}")
        jars.append(str(candidates[-1]))
    networks = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    address = next((a for a in addresses if any(ipaddress.ip_address(a) in n for n in networks)), None)
    if address is None:
        raise RuntimeError("No RFC1918 adapter available for production Android URL validation")
    with tempfile.TemporaryDirectory(prefix="epm-tls-interop-") as temporary:
        root = Path(temporary)
        identity = load_or_create_identity(root / "primary")
        other = load_or_create_identity(root / "other")
        for prefix, item in (("", identity), ("other-", other)):
            (root / f"{prefix}host-id").write_text(item.host_id, encoding="ascii")
            (root / f"{prefix}certificate.pem").write_text(item.certificate_pem, encoding="ascii")
            (root / f"{prefix}pin").write_text(item.certificate_pin, encoding="ascii")
        sources = args.android_root / "frontend/android/app/src"
        package = Path("java/com/wssfk/englishpracticemachine")
        classes = root / "classes"
        classes.mkdir()
        classpath = os.pathsep.join(jars)
        subprocess.run(["javac", "-encoding", "UTF-8", "-cp", classpath, "-d", str(classes),
                        str(sources / "main" / package / "LanSyncTransport.java"),
                        str(sources / "main" / package / "LanTlsTrust.java"),
                        str(sources / "test" / package / "LanTlsInterop.java")], check=True, timeout=60)
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        received = []

        @app.post("/api/lan-sync/handshake")
        def probe():
            received.append("handshake")
            return {"probe": "tls-probe"}

        @app.post("/api/lan-sync/pull")
        def redirect():
            received.append("redirect")
            return RedirectResponse("/api/lan-sync/handshake")

        listener = socket.socket()
        listener.bind((address, 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(tls_server_config(app, identity, address, port))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not server.started:
                raise RuntimeError("TLS probe listener not ready")
            subprocess.run(["java", "-cp", os.pathsep.join([str(classes), classpath]),
                            "com.wssfk.englishpracticemachine.LanTlsInterop",
                            str(root), f"https://{address}:{port}"], check=True, timeout=90)
            if received != ["handshake", "redirect"]:
                raise AssertionError(f"Unexpected application requests: {received!r}")
            print("PASS: rejected identities sent no HTTP body; redirect not followed")
        finally:
            server.should_exit = True
            thread.join(5)
            listener.close()
            if thread.is_alive():
                raise RuntimeError("TLS probe did not stop")


if __name__ == "__main__":
    main()
