"""Generate a local development CA and a TLS server certificate for the SaaS backend.

    python scripts/gen_certs.py [--out certificates] [--force]

Creates:
  ca.pem / ca.key          – local development root CA (trust anchor for the collector)
  server.pem / server.key  – server certificate for localhost / 127.0.0.1, signed by the CA

The collector verifies the SaaS certificate against ca.pem. Plaintext HTTP is never used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _write_key(key, path: Path) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def generate(out_dir: str | Path, force: bool = False, ca_cn: str = "NEXORA Dev Local CA") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {k: out / f for k, f in
             {"ca_cert": "ca.pem", "ca_key": "ca.key", "server_cert": "server.pem", "server_key": "server.key"}.items()}
    if not force and all(p.exists() for p in paths.values()):
        return {k: str(v) for k, v in paths.items()}

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_cn),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NEXORA Dev (synthetic)")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    srv_key = ec.generate_private_key(ec.SECP256R1())
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    paths["ca_cert"].write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    _write_key(ca_key, paths["ca_key"])
    paths["server_cert"].write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    _write_key(srv_key, paths["server_key"])
    return {k: str(v) for k, v in paths.items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "certificates"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    res = generate(a.out, force=a.force)
    for k, v in res.items():
        print(f"{k:12s} {v}")
