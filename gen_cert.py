#!/usr/bin/env python3
"""
make_certs.py
Tạo CA, chứng chỉ và private key cho broker và gateway.
Sử dụng: python make_certs.py
Các tuỳ chọn có thể chỉnh trong phần DEFAULTS hoặc truyền qua argparse.
"""
import ipaddress
import os
import sys
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import argparse

# ---------- Config mặc định (đổi nếu cần) ----------
DEFAULTS = {
    "ca_common_name": "MyLocalCA",
    "broker_common_name": "broker.local",
    "gateway_common_name": "gateway.local",
    "server_common_name": "VPS.server",
    "broker_sans": ["broker.local", "192.168.1.148"],
    "gateway_sans": ["gateway.local", "192.168.1.148"],
    "server_sans": ["VPS.server", "159.223.63.61"],
    "key_size": 2048,
    "ca_valid_days": 3650,      # 10 years
    "cert_valid_days": 825,     # ~2.25 years (common max)
    "output_dir": ".",      # thư mục lưu
}

# ---------- Helpers ----------
def ensure_outdir(path):
    os.makedirs(path, exist_ok=True)

def write_pem(path, data: bytes, mode=0o644):
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, mode)

def gen_rsa_key(key_size=2048):
    return rsa.generate_private_key(public_exponent=65537, key_size=key_size)

def private_key_to_pem(private_key, passphrase=None):
    enc = serialization.NoEncryption()
    if passphrase:
        enc = serialization.BestAvailableEncryption(passphrase.encode())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=enc,
    )

def cert_to_pem(cert: x509.Certificate):
    return cert.public_bytes(serialization.Encoding.PEM)

def name_from_cn(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

def build_san(san_list):
    san_objs = []
    for s in san_list:
        # detect if IP
        try:
            ip = ipaddress.ip_address(s)
            san_objs.append(x509.IPAddress(ip))
        except ValueError:
            san_objs.append(x509.DNSName(s))
    return x509.SubjectAlternativeName(san_objs)

# ---------- CA creation ----------
def create_ca(common_name, key_size=2048, valid_days=3650):
    key = gen_rsa_key(key_size)
    subject = issuer = name_from_cn(common_name)

    now = datetime.utcnow()
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_cert_sign=True,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                encipher_only=False,
                decipher_only=False,
                crl_sign=True,
            ),
            critical=True,
        )
    )
    cert = cert_builder.sign(private_key=key, algorithm=hashes.SHA256())
    return key, cert

# ---------- Server cert (CSR + sign) ----------
def create_csr_and_signed_cert(common_name, san_list, ca_key, ca_cert, key_size=2048, valid_days=825):
    # 1) generate key
    key = gen_rsa_key(key_size)

    # 2) CSR
    csr_builder = x509.CertificateSigningRequestBuilder().subject_name(name_from_cn(common_name))
    csr_builder = csr_builder.add_extension(build_san(san_list), critical=False)
    csr = csr_builder.sign(key, hashes.SHA256())

    # 3) sign CSR with CA
    now = datetime.utcnow()
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(build_san(san_list), critical=False)
        # typical server key usages:
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                encipher_only=False,
                decipher_only=False,
                crl_sign=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH, x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    )
    signed_cert = cert_builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return key, csr, signed_cert

# ---------- CLI main ----------
def main():
    p = argparse.ArgumentParser(description="Tạo CA + cert/key cho broker và gateway.")
    p.add_argument("--out", "-o", default=DEFAULTS["output_dir"], help="Thư mục lưu chứng chỉ/khóa")
    p.add_argument("--ca-cn", default=DEFAULTS["ca_common_name"], help="CA Common Name")
    p.add_argument("--broker-cn", default=DEFAULTS["broker_common_name"], help="Broker Common Name")
    p.add_argument("--gateway-cn", default=DEFAULTS["gateway_common_name"], help="Gateway Common Name")
    p.add_argument("--server-cn", default=DEFAULTS["server_common_name"], help="Server Common Name")
    p.add_argument("--broker-san", nargs="+", default=DEFAULTS["broker_sans"], help="SANs cho broker (DNS/IP). Ví dụ: broker.local 127.0.0.1")
    p.add_argument("--gateway-san", nargs="+", default=DEFAULTS["gateway_sans"], help="SANs cho gateway (DNS/IP).")
    p.add_argument("--server-san", nargs="+", default=DEFAULTS["server_sans"], help="SANs cho server")
    p.add_argument("--key-size", type=int, default=DEFAULTS["key_size"], help="Kích thước RSA key (bits)")
    p.add_argument("--ca-days", type=int, default=DEFAULTS["ca_valid_days"], help="Thời hạn CA (ngày)")
    p.add_argument("--cert-days", type=int, default=DEFAULTS["cert_valid_days"], help="Thời hạn cert server (ngày)")
    args = p.parse_args()

    out = args.out
    ensure_outdir(out)

    print("Tạo CA...")
    ca_key, ca_cert = create_ca(args.ca_cn, key_size=args.key_size, valid_days=args.ca_days)
    ca_key_pem = private_key_to_pem(ca_key)
    ca_cert_pem = cert_to_pem(ca_cert)
    write_pem(os.path.join(out, "ca.key.pem"), ca_key_pem, mode=0o600)
    write_pem(os.path.join(out, "ca.cert.pem"), ca_cert_pem)

    print("Tạo broker key, csr và ký bởi CA...")
    broker_key, broker_csr, broker_cert = create_csr_and_signed_cert(
        args.broker_cn, args.broker_san, ca_key, ca_cert, key_size=args.key_size, valid_days=args.cert_days
    )
    write_pem(os.path.join(out, "broker.key.pem"), private_key_to_pem(broker_key), mode=0o600)
    write_pem(os.path.join(out, "broker.cert.pem"), cert_to_pem(broker_cert))
    # Lưu CSR nếu cần
    write_pem(os.path.join(out, "broker.csr.pem"), broker_csr.public_bytes(serialization.Encoding.PEM))

    print("Tạo gateway key, csr và ký bởi CA...")
    gateway_key, gateway_csr, gateway_cert = create_csr_and_signed_cert(
        args.gateway_cn, args.gateway_san, ca_key, ca_cert, key_size=args.key_size, valid_days=args.cert_days
    )
    write_pem(os.path.join(out, "gateway.key.pem"), private_key_to_pem(gateway_key), mode=0o600)
    write_pem(os.path.join(out, "gateway.cert.pem"), cert_to_pem(gateway_cert))
    write_pem(os.path.join(out, "gateway.csr.pem"), gateway_csr.public_bytes(serialization.Encoding.PEM))

    server_key, server_csr, server_cert = create_csr_and_signed_cert(
        args.server_cn, args.server_san, ca_key, ca_cert, key_size=args.key_size, valid_days=args.cert_days
    )
    write_pem(os.path.join(out, "server.key.pem"), private_key_to_pem(gateway_key), mode=0o600)
    write_pem(os.path.join(out, "server.cert.pem"), cert_to_pem(server_cert))
    write_pem(os.path.join(out, "server.csr.pem"), server_csr.public_bytes(serialization.Encoding.PEM))

    # Optional: create combined PEM for server (key + cert) if some services want
    with open(os.path.join(out, "broker.full.pem"), "wb") as f:
        f.write(private_key_to_pem(broker_key))
        f.write(cert_to_pem(broker_cert))
        f.write(ca_cert_pem)
    with open(os.path.join(out, "gateway.full.pem"), "wb") as f:
        f.write(private_key_to_pem(gateway_key))
        f.write(cert_to_pem(gateway_cert))
        f.write(ca_cert_pem)
    with open(os.path.join(out, "server.full.pem"), "wb") as f:
        f.write(private_key_to_pem(server_key))
        f.write(cert_to_pem(server_cert))
        f.write(ca_cert_pem)

    print(f"Hoàn tất. File lưu tại: {os.path.abspath(out)}")
    print("Files:")
    for fn in sorted(os.listdir(out)):
        print(" -", fn)

if __name__ == "__main__":
    main()
