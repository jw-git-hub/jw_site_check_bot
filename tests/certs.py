"""Сертификаты для тестов: свой удостоверяющий центр, промежуточный, листья с нужными датами и именами."""
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

DAY = timedelta(days=1)
VALID_DAYS = 90
CA_USAGE = x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                         data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                         encipher_only=False, decipher_only=False)


@dataclass(frozen=True)
class Issued:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey


def issue(name: str, issuer: Issued | None = None, *, is_ca: bool = False, dns_names: tuple[str, ...] = (),
          not_before: datetime | None = None, not_after: datetime | None = None) -> Issued:
    key = ec.generate_private_key(ec.SECP256R1())
    builder = _base(name, key, issuer, not_before, not_after)
    builder = _extensions(builder, key, issuer, is_ca, dns_names)
    return Issued(builder.sign(issuer.key if issuer else key, hashes.SHA256()), key)


def _base(name, key, issuer, not_before, not_after) -> x509.CertificateBuilder:
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, f"{name} org")])
    return (x509.CertificateBuilder().subject_name(subject)
            .issuer_name(issuer.cert.subject if issuer else subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(not_before or now - DAY).not_valid_after(not_after or now + VALID_DAYS * DAY))


def _extensions(builder, key, issuer, is_ca, dns_names) -> x509.CertificateBuilder:
    builder = builder.add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    if issuer:
        authority = x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.key.public_key())
        builder = builder.add_extension(authority, critical=False)
    if is_ca:
        builder = builder.add_extension(CA_USAGE, critical=True)
    if dns_names:
        names = x509.SubjectAlternativeName([x509.DNSName(name) for name in dns_names])
        builder = builder.add_extension(names, critical=False)
    return builder


def write_pem(folder: Path, name: str, *certs: x509.Certificate) -> Path:
    path = folder / f"{name}.pem"
    path.write_bytes(b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certs))
    return path


def write_key(folder: Path, name: str, key: ec.EllipticCurvePrivateKey) -> Path:
    path = folder / f"{name}.key"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return path


def server_context(folder: Path, leaf: Issued, *chain: x509.Certificate) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(write_pem(folder, "chain", leaf.cert, *chain), write_key(folder, "leaf", leaf.key))
    return context


def client_context_trusting(folder: Path, root: Issued) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(write_pem(folder, "root", root.cert)))
