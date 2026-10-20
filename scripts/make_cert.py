"""Create a self-signed HTTPS certificate for phone/LAN access.

Example:
    .\.venv\Scripts\python.exe scripts\make_cert.py
    .\.venv\Scripts\python.exe -m rti.server --host 0.0.0.0 `
      --ssl-keyfile certs\key.pem --ssl-certfile certs\cert.pem
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def lan_ip() -> str | None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a local HTTPS certificate")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "certs")
    parser.add_argument("--ip", action="append", default=None)
    parser.add_argument("--days", type=int, default=3650)
    args = parser.parse_args(argv)

    ips: list[str] = ["127.0.0.1"]
    detected = lan_ip()
    if detected:
        ips.append(detected)
    if args.ip:
        ips.extend(args.ip)
    ips = list(dict.fromkeys(ips))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RTI Local")])
    now = datetime.now(timezone.utc)
    san = [x509.DNSName("localhost")] + [
        x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips
    ]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=args.days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    key_path = out / "key.pem"
    cert_path = out / "cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"cert: {cert_path}")
    print(f"key:  {key_path}")
    print("SAN IPs:", ", ".join(ips))
    print("\nphone: install cert.pem as a trusted certificate, then open:")
    for ip in ips:
        if ip != "127.0.0.1":
            print(f"  https://{ip}:8765/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
