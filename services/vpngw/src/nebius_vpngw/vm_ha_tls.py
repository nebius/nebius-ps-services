"""Pre-mutation VM-HA certificate and mutual-TLS validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

VM_HA_MTLS_NOT_BEFORE = datetime(2000, 1, 1, tzinfo=timezone.utc)
VM_HA_MTLS_NOT_AFTER = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
_NODE_ID_SAN_PREFIX = "urn:nebius-vpngw:node:"


@dataclass(frozen=True)
class VMHAManagedIdentity:
    """One VM-local managed identity; private bytes never enter receipts."""

    node_id: str
    certificate_pem: bytes
    private_key_pem: bytes
    certificate_fingerprint: str
    spki_fingerprint: str


@dataclass(frozen=True)
class VMHAManagedCertificate:
    """Validated public certificate identity safe to exchange over pinned SSH."""

    node_id: str
    certificate_fingerprint: str
    spki_fingerprint: str


def _public_key_fingerprint(public_key: ec.EllipticCurvePublicKey) -> str:
    encoded = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(encoded)
    return digest.finalize().hex()


def generate_vm_ha_managed_identity(node_id: str) -> VMHAManagedIdentity:
    """Generate the fixed-profile self-signed leaf used by one VM-HA member."""

    if not node_id or len(node_id) > 64:
        raise ValueError("VM-HA managed TLS node identity is invalid")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(VM_HA_MTLS_NOT_BEFORE)
        .not_valid_after(VM_HA_MTLS_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(node_id),
                    x509.UniformResourceIdentifier(f"{_NODE_ID_SAN_PREFIX}{node_id}"),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    private_key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return VMHAManagedIdentity(
        node_id=node_id,
        certificate_pem=certificate_pem,
        private_key_pem=private_key_pem,
        certificate_fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
        spki_fingerprint=_public_key_fingerprint(key.public_key()),
    )


def validate_vm_ha_managed_certificate(
    node_id: str,
    certificate_pem: bytes,
    *,
    private_key_pem: bytes | None = None,
    now: datetime | None = None,
) -> VMHAManagedCertificate:
    """Validate the exact managed certificate profile and optional local key."""

    try:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError
        certificate = x509.load_pem_x509_certificate(certificate_pem)
        public_key = certificate.public_key()
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
            public_key.curve, ec.SECP256R1
        ):
            raise ValueError
        if certificate.subject != certificate.issuer:
            raise ValueError
        if certificate.subject != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]):
            raise ValueError
        signature_hash = certificate.signature_hash_algorithm
        if not isinstance(signature_hash, hashes.SHA256):
            raise ValueError
        public_key.verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            ec.ECDSA(signature_hash),
        )
        if certificate.serial_number <= 0:
            raise ValueError
        if (
            certificate.not_valid_before_utc != VM_HA_MTLS_NOT_BEFORE
            or certificate.not_valid_after_utc != VM_HA_MTLS_NOT_AFTER
            or not (certificate.not_valid_before_utc <= current < certificate.not_valid_after_utc)
        ):
            raise ValueError

        basic = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
        if not basic.critical or basic.value.ca or basic.value.path_length is not None:
            raise ValueError
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
        if (
            not usage.critical
            or not usage.value.digital_signature
            or any(
                (
                    usage.value.content_commitment,
                    usage.value.key_encipherment,
                    usage.value.data_encipherment,
                    usage.value.key_agreement,
                    usage.value.key_cert_sign,
                    usage.value.crl_sign,
                )
            )
        ):
            raise ValueError
        extended = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        if extended.critical or set(extended.value) != {
            ExtendedKeyUsageOID.CLIENT_AUTH,
            ExtendedKeyUsageOID.SERVER_AUTH,
        }:
            raise ValueError
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        if san.critical:
            raise ValueError
        if san.value.get_values_for_type(x509.DNSName) != [node_id]:
            raise ValueError
        if san.value.get_values_for_type(x509.UniformResourceIdentifier) != [
            f"{_NODE_ID_SAN_PREFIX}{node_id}"
        ]:
            raise ValueError
        subject_key = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        authority_key = certificate.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
        if subject_key.critical or authority_key.critical:
            raise ValueError
        if authority_key.value.key_identifier != subject_key.value.digest:
            raise ValueError

        if private_key_pem is not None:
            private_key = serialization.load_pem_private_key(private_key_pem, password=None)
            if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
                private_key.curve, ec.SECP256R1
            ):
                raise ValueError
            if _public_key_fingerprint(private_key.public_key()) != _public_key_fingerprint(
                public_key
            ):
                raise ValueError
        return VMHAManagedCertificate(
            node_id=node_id,
            certificate_fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
            spki_fingerprint=_public_key_fingerprint(public_key),
        )
    except (
        InvalidSignature,
        OSError,
        TypeError,
        UnsupportedAlgorithm,
        ValueError,
        x509.ExtensionNotFound,
    ):
        raise ValueError(f"VM-HA managed TLS certificate for {node_id} is invalid") from None
