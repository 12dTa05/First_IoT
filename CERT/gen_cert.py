from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
import ipaddress
from datetime import datetime, timedelta

# ==== Tạo khóa CA ====
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ca_name = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "VN"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Hanoi"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "Hanoi"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MyIoT"),
    x509.NameAttribute(NameOID.COMMON_NAME, "MyIoT-CA"),
])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.utcnow())
    .not_valid_after(datetime.utcnow() + timedelta(days=3650))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(ca_key, hashes.SHA256())
)

# Lưu CA
with open("CA.key", "wb") as f:
    f.write(ca_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()
    ))
with open("CA.crt", "wb") as f:
    f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

# ==== Tạo broker cert ====
broker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
broker_name = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "VN"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Hanoi"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MyIoT"),
    x509.NameAttribute(NameOID.COMMON_NAME, "192.168.1.148"),
])
alt_names = [x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("192.168.1.148"))])]
broker_cert = (
    x509.CertificateBuilder()
    .subject_name(broker_name)
    .issuer_name(ca_name)
    .public_key(broker_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.utcnow())
    .not_valid_after(datetime.utcnow() + timedelta(days=365))
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(alt_names[0], critical=False)
    .sign(ca_key, hashes.SHA256())
)

# Lưu broker cert và key
with open("broker.key", "wb") as f:
    f.write(broker_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()
    ))
with open("broker.crt", "wb") as f:
    f.write(broker_cert.public_bytes(serialization.Encoding.PEM))
