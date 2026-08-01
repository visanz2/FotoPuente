"""
Genera el certificado autofirmado que necesita FotoPuente para servir HTTPS.

Safari en iOS ya no abre paginas por HTTP plano: intenta ascender a HTTPS y,
si no hay nadie escuchando, deja la pestana en blanco. Con HTTPS ademas el
navegador considera la pagina "contexto seguro", lo que permite mantener la
pantalla encendida durante la transferencia.

El certificado se crea una sola vez y se reutiliza. Si cambian las IPs del
equipo se regenera solo, porque Safari valida la IP contra el campo SAN.

Se intenta, por orden: el modulo `cryptography`, y si no esta, el ejecutable
`openssl` (viene con Git para Windows).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DIAS_VALIDEZ = 820          # Apple rechaza certificados de mas de 825 dias


def _huella(nombres: list[str]) -> str:
    return json.dumps(sorted(nombres), ensure_ascii=False)


def _rutas(carpeta: Path) -> tuple[Path, Path, Path]:
    return (carpeta / "fotopuente.crt",
            carpeta / "fotopuente.key",
            carpeta / "fotopuente.san.json")


def _sirve(carpeta: Path, nombres: list[str]) -> bool:
    """True si ya hay un certificado valido para exactamente estos nombres."""
    crt, key, san = _rutas(carpeta)
    if not (crt.exists() and key.exists() and san.exists()):
        return False
    try:
        guardado = json.loads(san.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if guardado.get("nombres") != _huella(nombres):
        return False
    try:
        caduca = dt.datetime.fromisoformat(guardado["caduca"])
    except (KeyError, ValueError):
        return False
    return caduca > dt.datetime.now() + dt.timedelta(days=2)


# --- Generacion con el modulo `cryptography` ---------------------------------

def _con_cryptography(crt: Path, key: Path, nombres: list[str]) -> bool:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        return False

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    alt: list = []
    for n in nombres:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            alt.append(x509.DNSName(n))

    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "FotoPuente"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FotoPuente (local)"),
    ])
    ahora = dt.datetime.now(dt.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(days=1))
        .not_valid_after(ahora + dt.timedelta(days=DIAS_VALIDEZ))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(clave, hashes.SHA256())
    )

    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return True


# --- Generacion con el ejecutable `openssl` ----------------------------------

def _buscar_openssl() -> str | None:
    encontrado = shutil.which("openssl")
    if encontrado:
        return encontrado
    # Git para Windows lo trae, pero no siempre esta en el PATH.
    for cand in (
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        r"C:\Program Files (x86)\Git\usr\bin\openssl.exe",
    ):
        if os.path.exists(cand):
            return cand
    return None


def _con_openssl(crt: Path, key: Path, nombres: list[str]) -> bool:
    exe = _buscar_openssl()
    if not exe:
        return False

    entradas = []
    n_dns = n_ip = 0
    for n in nombres:
        try:
            ipaddress.ip_address(n)
            n_ip += 1
            entradas.append(f"IP.{n_ip} = {n}")
        except ValueError:
            n_dns += 1
            entradas.append(f"DNS.{n_dns} = {n}")

    conf = crt.parent / "openssl.cnf"
    conf.write_text(
        "[req]\n"
        "distinguished_name = dn\n"
        "x509_extensions = v3\n"
        "prompt = no\n"
        "[dn]\n"
        "CN = FotoPuente\n"
        "O = FotoPuente (local)\n"
        "[v3]\n"
        "basicConstraints = critical, CA:FALSE\n"
        "extendedKeyUsage = serverAuth\n"
        "subjectAltName = @alt\n"
        "[alt]\n" + "\n".join(entradas) + "\n",
        "utf-8",
    )

    try:
        res = subprocess.run(
            [exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(crt),
             "-days", str(DIAS_VALIDEZ), "-config", str(conf), "-extensions", "v3"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        conf.unlink(missing_ok=True)

    return res.returncode == 0 and crt.exists() and key.exists()


# --- API publica --------------------------------------------------------------

class SinCertificado(RuntimeError):
    """No hay forma de generar el certificado en este equipo."""


def asegurar(carpeta: Path, ips: list[str]) -> tuple[str, str]:
    """Devuelve (ruta_certificado, ruta_clave), generandolos si hace falta."""
    carpeta.mkdir(parents=True, exist_ok=True)
    nombres = list(dict.fromkeys([*ips, "localhost", "127.0.0.1"]))
    crt, key, san = _rutas(carpeta)

    if _sirve(carpeta, nombres):
        return str(crt), str(key)

    print("  Generando certificado HTTPS para este equipo...", flush=True)
    if not (_con_cryptography(crt, key, nombres) or _con_openssl(crt, key, nombres)):
        raise SinCertificado(
            "No se pudo crear el certificado: falta el modulo 'cryptography' y "
            "tampoco se encontro openssl.exe.\n"
            "  Solucion rapida:  py -3 -m pip install cryptography\n"
            "  O bien arranca sin cifrado:  py -3 fotopuente.py --http"
        )

    san.write_text(json.dumps({
        "nombres": _huella(nombres),
        "caduca": (dt.datetime.now() + dt.timedelta(days=DIAS_VALIDEZ)).isoformat(),
    }, ensure_ascii=False), "utf-8")

    # La clave privada no deberia ser legible por otros usuarios del equipo.
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass

    print(f"  Certificado listo para: {', '.join(nombres)}", flush=True)
    return str(crt), str(key)
