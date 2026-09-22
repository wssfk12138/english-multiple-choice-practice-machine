"""Persistent LAN TLS identity. This module does not enable the legacy listener."""
from __future__ import annotations

import base64
import json
import os
import secrets
import ssl
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .security import protect_text, unprotect_text

_lock = threading.Lock()


@dataclass(frozen=True)
class LanTlsIdentity:
    host_id: str
    certificate_pem: str
    certificate_pin: str
    directory: Path
    _password: str = field(repr=False)

    @property
    def server_name(self) -> str:
        return f"epm-{self.host_id}.invalid"

    def public_descriptor(self) -> dict:
        return {
            "version": 2,
            "host_id": self.host_id,
            "server_name": self.server_name,
            "certificate_pem": self.certificate_pem,
            "certificate_pin": self.certificate_pin,
        }

    def server_context(self) -> ssl.SSLContext:
        # CPython/OpenSSL validates the encrypted PKCS8 key against the certificate.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            self.directory / "certificate.pem",
            self.directory / "private-key.pem",
            password=self._password,
        )
        return context


def _load(directory: Path) -> LanTlsIdentity:
    metadata = json.loads((directory / "identity.json").read_text(encoding="utf-8"))
    host_id = str(uuid.UUID(metadata["host_id"]))
    if metadata.get("version") != 2 or metadata["host_id"] != host_id:
        raise ValueError("Invalid LAN identity metadata")
    password = unprotect_text(metadata["protected_password"])
    if not password:
        raise ValueError("LAN identity protection unavailable")
    pem = (directory / "certificate.pem").read_text(encoding="ascii")
    certificate = x509.load_pem_x509_certificate(pem.encode("ascii"))
    certificate.verify_directly_issued_by(certificate)
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
        raise ValueError("LAN certificate expired or not yet valid; explicit re-pairing required")
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if names.get_values_for_type(x509.DNSName) != [f"epm-{host_id}.invalid"]:
        raise ValueError("LAN certificate identity mismatch")
    if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError("LAN identity must be a leaf certificate")
    usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    if list(usage) != [ExtendedKeyUsageOID.SERVER_AUTH]:
        raise ValueError("Invalid LAN certificate purpose")
    # Pin the full certificate, not only the key, so certificate replacement is explicit.
    pin = base64.b64encode(certificate.fingerprint(hashes.SHA256())).decode("ascii")
    identity = LanTlsIdentity(host_id, pem, f"sha256/{pin}", directory, password)
    identity.server_context()
    return identity


def load_or_create_identity(data_dir: Path) -> LanTlsIdentity:
    """Publish once; damaged/existing identities fail closed and are never regenerated."""
    directory = data_dir / "lan-tls-v2"
    with _lock:
        if directory.exists():
            return _load(directory)
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".lan-tls-", dir=data_dir) as temporary:
            candidate = Path(temporary) / "identity"
            candidate.mkdir()
            host_id = str(uuid.uuid4())
            server_name = f"epm-{host_id}.invalid"
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, server_name)])
            now = datetime.now(timezone.utc)
            # cryptography X.509 builder + OpenSSL TLS, not an application crypto protocol.
            certificate = (
                x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(server_name)]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True)
                .sign(key, hashes.SHA256())
            )
            password = secrets.token_urlsafe(48)
            protected_password = protect_text(password)
            if not protected_password or unprotect_text(protected_password) != password:
                raise ValueError("LAN identity protection verification failed")
            (candidate / "private-key.pem").write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password.encode("ascii")),
            ))
            (candidate / "certificate.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            (candidate / "identity.json").write_text(json.dumps({
                "version": 2, "host_id": host_id, "protected_password": protected_password,
            }), encoding="utf-8")
            _load(candidate)
            try:
                # rename, never replace: a concurrent creator must not overwrite a binding.
                os.rename(candidate, directory)
            except OSError:
                if not directory.exists():
                    raise
            return _load(directory)


def tls_server_config(app, identity: LanTlsIdentity, host: str, port: int) -> uvicorn.Config:
    """Build a TLS-only server; no HTTP retry, redirect or forwarded-scheme trust."""
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False,
        proxy_headers=False, ssl_certfile=str(identity.directory / "certificate.pem"),
        ssl_keyfile=str(identity.directory / "private-key.pem"),
        ssl_keyfile_password=identity._password,
    )
    config.load()
    config.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    return config
