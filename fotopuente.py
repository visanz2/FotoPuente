#!/usr/bin/env python3
"""
FotoPuente - pasa las fotos del iPhone a Windows por WiFi.

Levanta un servidor web en la red local. Escaneas el QR con el iPhone, eliges
las fotos en Safari y se guardan en la carpeta que tu elijas (local o compartida).

No necesita instalar nada: solo la libreria estandar de Python 3.9+.

    py -3 fotopuente.py
    py -3 fotopuente.py --carpeta "\\\\MI-PC\\Fotos" --puerto 8765
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import certificado
import fechas
import qrlite

RAIZ = Path(__file__).resolve().parent
WEB = RAIZ / "web"
DATOS = RAIZ / "datos"
CONFIG = RAIZ / "config.json"

VERSION = "1.0"
LOCALES = {"127.0.0.1", "::1", "localhost"}

# Extensiones que aceptamos; el resto se rechaza para que no acabe
# cualquier cosa en la carpeta de fotos.
EXT_PERMITIDAS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".heic", ".heif",
    ".tif", ".tiff", ".bmp", ".dng", ".avif",
    ".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm",
}

CONFIG_POR_DEFECTO = {
    "carpeta_destino": str(Path.home() / "Pictures" / "FotoPuente"),
    "puerto": 8765,
    "organizar": "anio-mes",       # plano | anio | anio-mes | anio-mes-dia
    "formato": "compatible",       # compatible (fuerza JPEG) | original (intenta HEIC)
    "token": "",
    "abrir_panel": True,
}


# --- Configuracion ------------------------------------------------------------

def cargar_config() -> dict:
    cfg = dict(CONFIG_POR_DEFECTO)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text("utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Aviso: config.json ilegible ({e}); se usan los valores por defecto.")
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(9)
    return cfg


def guardar_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


# --- Utilidades ---------------------------------------------------------------

_cache_ips: tuple[float, list[str]] = (0.0, [])
_lock_ips = threading.Lock()


def ips_locales() -> list[str]:
    """IPs de la maquina en la LAN, la mas probable primero.

    El resultado se cachea 30 s: durante una transferencia grande esto se
    consulta muy a menudo y `getaddrinfo` puede tardar.
    """
    global _cache_ips
    with _lock_ips:
        cuando, valor = _cache_ips
        if valor and time.time() - cuando < 30:
            return valor

    encontradas: list[str] = []

    # Truco del socket UDP: revela la interfaz que usa la ruta por defecto.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        encontradas.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in encontradas and not ip.startswith(("127.", "169.254.")):
                encontradas.append(ip)
    except socket.gaierror:
        pass

    resultado = encontradas or ["127.0.0.1"]
    with _lock_ips:
        _cache_ips = (time.time(), resultado)
    return resultado


def etiqueta_destino(ruta: str) -> str:
    """Nombre corto y reconocible de la carpeta destino para el movil."""
    limpia = ruta.rstrip("\\/")
    if limpia.startswith("\\\\"):                 # recurso de red: \\PC\Fotos
        return limpia
    partes = [p for p in limpia.replace("/", "\\").split("\\") if p]
    return "\\".join(partes[-2:]) if len(partes) >= 2 else (limpia or ruta)


_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def nombre_seguro(nombre: str) -> str:
    """Convierte el nombre recibido en algo que Windows acepte, sin rutas."""
    nombre = nombre.replace("\\", "/").split("/")[-1]
    nombre = _INVALIDOS.sub("_", nombre).strip(" .")
    if not nombre:
        nombre = "sin_nombre"
    tallo, punto, ext = nombre.rpartition(".")
    if punto and tallo.upper() in _RESERVADOS:
        nombre = f"_{nombre}"
    elif not punto and nombre.upper() in _RESERVADOS:
        nombre = f"_{nombre}"
    if len(nombre) > 120:
        tallo, punto, ext = nombre.rpartition(".")
        nombre = (tallo[:110] + punto + ext[:9]) if punto else nombre[:120]
    return nombre


def ruta_libre(carpeta: Path, nombre: str) -> Path:
    """Devuelve una ruta que no exista, anadiendo (2), (3)... si hace falta."""
    destino = carpeta / nombre
    if not destino.exists():
        return destino
    tallo, punto, ext = nombre.rpartition(".")
    if not punto:
        tallo, ext = nombre, ""
    for i in range(2, 10000):
        cand = carpeta / (f"{tallo} ({i}).{ext}" if ext else f"{tallo} ({i})")
        if not cand.exists():
            return cand
    return carpeta / f"{tallo}_{secrets.token_hex(4)}{('.' + ext) if ext else ''}"


def humano(n: float) -> str:
    for unidad in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024
    return f"{n:.1f} PB"


# --- Indice de archivos ya transferidos ---------------------------------------

class Indice:
    """Registro en SQLite de lo ya recibido, para no repetir transferencias."""

    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(ruta), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS archivos (
                   hash    TEXT PRIMARY KEY,
                   nombre  TEXT NOT NULL,
                   tamano  INTEGER NOT NULL,
                   ruta    TEXT NOT NULL,
                   fecha   TEXT,
                   recibido TEXT NOT NULL
               )"""
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_nombre_tamano ON archivos(nombre, tamano)"
        )
        self._db.commit()

    def cuales_ya_estan(self, pares: list[tuple[str, int]]) -> set[tuple[str, int]]:
        """De la lista (nombre, tamano) dada, cuales ya se transfirieron.

        Comprueba ademas que el archivo siga en disco: si lo borras, vuelve a
        enviarse. Agrupa por lotes de 400 nombres en lugar de una consulta por
        archivo, que al sincronizar un carrete entero se nota.
        """
        if not pares:
            return set()
        candidatos: dict[tuple[str, int], list[str]] = {}
        nombres = sorted({n for n, _ in pares})
        with self._lock:
            for i in range(0, len(nombres), 400):
                trozo = nombres[i:i + 400]
                marcas = ",".join("?" * len(trozo))
                for nombre, tamano, ruta in self._db.execute(
                    f"SELECT nombre, tamano, ruta FROM archivos WHERE nombre IN ({marcas})",
                    trozo,
                ):
                    candidatos.setdefault((nombre, tamano), []).append(ruta)

        presentes = set()
        for clave in set(pares):
            if any(os.path.exists(r) for r in candidatos.get(clave, ())):
                presentes.add(clave)
        return presentes

    def por_hash(self, h: str) -> str | None:
        with self._lock:
            fila = self._db.execute(
                "SELECT ruta FROM archivos WHERE hash=?", (h,)
            ).fetchone()
        if fila and os.path.exists(fila[0]):
            return fila[0]
        return None

    def registrar(self, h: str, nombre: str, tamano: int, ruta: str,
                  fecha: dt.datetime | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO archivos VALUES (?,?,?,?,?,?)",
                (h, nombre, tamano, ruta,
                 fecha.isoformat() if fecha else None,
                 dt.datetime.now().isoformat(timespec="seconds")),
            )
            self._db.commit()

    def total(self) -> tuple[int, int]:
        with self._lock:
            fila = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(tamano),0) FROM archivos"
            ).fetchone()
        return int(fila[0]), int(fila[1])

    def olvidar_todo(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM archivos")
            self._db.commit()


# --- Estado compartido y eventos para el panel --------------------------------

class Estado:
    def __init__(self, cfg: dict, indice: Indice, esquema: str = "https"):
        self.cfg = cfg
        self.indice = indice
        self.esquema = esquema      # https salvo que se arranque con --http
        self.lock = threading.Lock()
        self.recibidos = 0
        self.bytes_recibidos = 0
        self.duplicados = 0
        self.errores = 0
        self.arrancado = time.time()
        self.ultimos: list[dict] = []
        self._suscriptores: list[queue.Queue] = []

    # -- eventos SSE --
    def suscribir(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock:
            self._suscriptores.append(q)
        return q

    def desuscribir(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self._suscriptores:
                self._suscriptores.remove(q)

    def emitir(self, tipo: str, datos: dict) -> None:
        mensaje = json.dumps({"tipo": tipo, **datos}, ensure_ascii=False)
        with self.lock:
            muertos = []
            for q in self._suscriptores:
                try:
                    q.put_nowait(mensaje)
                except queue.Full:
                    muertos.append(q)
            for q in muertos:
                self._suscriptores.remove(q)

    def anotar(self, entrada: dict) -> None:
        with self.lock:
            self.ultimos.insert(0, entrada)
            del self.ultimos[60:]

    def resumen(self) -> dict:
        with self.lock:
            recibidos, bytes_, dup, err = (
                self.recibidos, self.bytes_recibidos, self.duplicados, self.errores
            )
            ultimos = list(self.ultimos)
        total_arch, total_bytes = self.indice.total()
        destino = self.cfg["carpeta_destino"]
        try:
            libre = shutil.disk_usage(destino).free
        except OSError:
            libre = None
        return {
            "destino": destino,
            "organizar": self.cfg["organizar"],
            "formato": self.cfg.get("formato", "compatible"),
            "puerto": self.cfg["puerto"],
            "recibidos": recibidos,
            "bytes": bytes_,
            "duplicados": dup,
            "errores": err,
            "historico_archivos": total_arch,
            "historico_bytes": total_bytes,
            "libre": libre,
            "ultimos": ultimos,
            "ips": ips_locales(),
            "esquema": self.esquema,
            "version": VERSION,
        }


# --- Servidor HTTP ------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"FotoPuente/{VERSION}"
    protocol_version = "HTTP/1.1"

    estado: Estado = None  # inyectado en arranque
    registro_detallado = False

    # Rutas que se repiten constantemente y no aportan nada al diagnostico.
    _RUIDO = ("/api/subir", "/api/eventos", "/api/ping", "/api/estado", "/web/")

    # -- ayudas --

    def log_message(self, formato, *args):
        """Muestra en consola lo justo para saber si el movil esta llegando."""
        try:
            codigo = int(args[1])
        except (IndexError, ValueError, TypeError):
            codigo = 0
        ruta = self.path.split("?")[0]
        interesante = codigo >= 400 or not ruta.startswith(self._RUIDO)
        if not (self.registro_detallado or interesante):
            return
        marca = "!" if codigo >= 400 else " "
        print(f"  {marca} {self.client_address[0]:<15} {self.command} {ruta} -> {codigo}",
              flush=True)

    def _es_local(self) -> bool:
        return self.client_address[0] in LOCALES

    def _token_ok(self) -> bool:
        if self._es_local():
            return True
        esperado = self.estado.cfg["token"]
        consulta = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if consulta.get("k", [None])[0] == esperado:
            return True
        if self.headers.get("X-FP-Token") == esperado:
            return True
        galleta = self.headers.get("Cookie", "")
        for parte in galleta.split(";"):
            nombre, _, valor = parte.strip().partition("=")
            if nombre == "fp_token" and valor == esperado:
                return True
        return False

    def _responder(self, codigo: int, cuerpo: bytes, tipo: str,
                   extra: dict | None = None) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(cuerpo)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, datos, codigo: int = 200) -> None:
        self._responder(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def _texto(self, codigo: int, mensaje: str) -> None:
        self._responder(codigo, mensaje.encode("utf-8"), "text/plain; charset=utf-8")

    def _leer_json(self) -> dict:
        largo = int(self.headers.get("Content-Length") or 0)
        if largo <= 0 or largo > 32 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(largo).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _estatico(self, nombre: str) -> None:
        ruta = (WEB / nombre).resolve()
        if not str(ruta).startswith(str(WEB.resolve())) or not ruta.is_file():
            self._texto(404, "No encontrado")
            return
        tipo = mimetypes.guess_type(str(ruta))[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript",):
            tipo += "; charset=utf-8"
        self._responder(200, ruta.read_bytes(), tipo)

    # -- rutas --

    def do_GET(self):
        ruta = urllib.parse.urlparse(self.path).path

        if ruta == "/":
            if not self._token_ok():
                self._responder(
                    403,
                    (WEB / "denegado.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
                return
            galleta = (f"fp_token={self.estado.cfg['token']}; Max-Age=31536000; "
                       f"Path=/; SameSite=Lax")
            self._responder(200, (WEB / "telefono.html").read_bytes(),
                            "text/html; charset=utf-8", {"Set-Cookie": galleta})
            return

        if ruta == "/panel":
            if not self._es_local():
                self._texto(403, "El panel solo se abre desde este ordenador.")
                return
            self._responder(200, (WEB / "panel.html").read_bytes(),
                            "text/html; charset=utf-8")
            return

        if ruta == "/qr.svg":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            consulta = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ip = consulta.get("ip", [ips_locales()[0]])[0]
            url = self.url_movil(ip)
            self._responder(200, qrlite.to_svg(url, "M", scale=8).encode("utf-8"),
                            "image/svg+xml; charset=utf-8")
            return

        if ruta == "/api/estado":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            datos = self.estado.resumen()
            datos["url"] = self.url_movil(datos["ips"][0])
            self._json(datos)
            return

        if ruta == "/api/eventos":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            self._sse()
            return

        if ruta == "/prueba":
            # Diagnostico: HTML minimo, sin CSS, sin JavaScript y sin token.
            # Si esto se ve en el movil, la red y el certificado estan bien.
            cuerpo = (
                "<!DOCTYPE html><html lang=es><head><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Prueba FotoPuente</title></head>"
                "<body style='font:16px sans-serif;padding:24px;line-height:1.6'>"
                "<h1 style='color:#0a7d32'>FUNCIONA</h1>"
                "<p>Tu movil ha conectado con FotoPuente correctamente.</p>"
                f"<p><b>Tu IP:</b> {self.client_address[0]}<br>"
                f"<b>Cifrado:</b> {self.estado.esquema.upper()}<br>"
                f"<b>Version:</b> {VERSION}</p>"
                "<p>Ya puedes volver a la pagina normal y enviar fotos.</p>"
                f"<p><a href='/?k={self.estado.cfg['token']}'>Ir a FotoPuente</a></p>"
                "</body></html>"
            ).encode("utf-8")
            self._responder(200, cuerpo, "text/html; charset=utf-8")
            return

        if ruta == "/api/ping":
            self._json({"ok": True,
                        "destino": etiqueta_destino(self.estado.cfg["carpeta_destino"]),
                        "formato": self.estado.cfg.get("formato", "compatible")})
            return

        if ruta.startswith("/web/"):
            self._estatico(ruta[5:])
            return

        self._texto(404, "No encontrado")

    def do_POST(self):
        ruta = urllib.parse.urlparse(self.path).path

        if ruta == "/api/plan":
            if not self._token_ok():
                self._json({"error": "no autorizado"}, 403)
                return
            self._plan()
            return

        if ruta == "/api/carpeta":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            self._cambiar_carpeta()
            return

        if ruta == "/api/formato":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            cuerpo = self._leer_json()
            valor = cuerpo.get("formato")
            if valor not in ("compatible", "original"):
                self._json({"ok": False, "error": "valor no valido"})
                return
            self.estado.cfg["formato"] = valor
            guardar_config(self.estado.cfg)
            self._json({"ok": True, "formato": valor})
            return

        if ruta == "/api/explorar":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            self._elegir_con_dialogo()
            return

        if ruta == "/api/abrir":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            destino = self.estado.cfg["carpeta_destino"]
            try:
                os.makedirs(destino, exist_ok=True)
                subprocess.Popen(["explorer", os.path.normpath(destino)])
            except OSError as e:
                self._json({"ok": False, "error": str(e)})
                return
            self._json({"ok": True})
            return

        if ruta == "/api/olvidar":
            if not self._es_local():
                self._texto(403, "Prohibido")
                return
            self.estado.indice.olvidar_todo()
            self.estado.emitir("estado", {})
            self._json({"ok": True})
            return

        self._texto(404, "No encontrado")

    def do_PUT(self):
        if urllib.parse.urlparse(self.path).path != "/api/subir":
            self._texto(404, "No encontrado")
            return
        if not self._token_ok():
            self._json({"error": "no autorizado"}, 403)
            return
        self._subir()

    # -- implementacion de cada ruta --

    def url_movil(self, ip: str) -> str:
        return (f"{self.estado.esquema}://{ip}:{self.estado.cfg['puerto']}"
                f"/?k={self.estado.cfg['token']}")

    def _sse(self):
        q = self.estado.suscribir()
        # Flujo sin longitud conocida: la conexion no se puede reutilizar.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                try:
                    mensaje = q.get(timeout=20)
                    self.wfile.write(f"data: {mensaje}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")   # mantiene viva la conexion
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.estado.desuscribir(q)

    def _plan(self):
        """El telefono manda la lista de lo que tiene; le decimos que falta."""
        cuerpo = self._leer_json()
        archivos = cuerpo.get("archivos") or []

        aptos: list[tuple[int, str, int]] = []
        descartados = 0
        for i, a in enumerate(archivos):
            nombre = nombre_seguro(str(a.get("nombre", "")))
            try:
                tamano = int(a.get("tamano") or 0)
            except (TypeError, ValueError):
                tamano = 0
            if os.path.splitext(nombre)[1].lower() not in EXT_PERMITIDAS or tamano <= 0:
                descartados += 1
                continue
            aptos.append((i, nombre, tamano))

        presentes = self.estado.indice.cuales_ya_estan([(n, t) for _, n, t in aptos])
        nuevos = [i for i, n, t in aptos if (n, t) not in presentes]
        self._json({
            "nuevos": nuevos,
            "repetidos": len(aptos) - len(nuevos),
            "descartados": descartados,
        })

    def _cambiar_carpeta(self):
        cuerpo = self._leer_json()
        nueva = str(cuerpo.get("carpeta", "")).strip().strip('"')
        if not nueva:
            self._json({"ok": False, "error": "Ruta vacia"})
            return
        try:
            os.makedirs(nueva, exist_ok=True)
            prueba = os.path.join(nueva, f".fotopuente_prueba_{secrets.token_hex(3)}")
            with open(prueba, "wb") as f:
                f.write(b"ok")
            os.remove(prueba)
        except OSError as e:
            self._json({"ok": False, "error": f"No se puede escribir ahi: {e}"})
            return

        self.estado.cfg["carpeta_destino"] = os.path.normpath(nueva)
        organizar = cuerpo.get("organizar")
        if organizar in ("plano", "anio", "anio-mes", "anio-mes-dia"):
            self.estado.cfg["organizar"] = organizar
        guardar_config(self.estado.cfg)
        self.estado.emitir("estado", {})
        self._json({"ok": True, "carpeta": self.estado.cfg["carpeta_destino"]})

    def _elegir_con_dialogo(self):
        """Abre el selector de carpetas de Windows en un proceso aparte.

        Tkinter no admite usarse desde los hilos del servidor, asi que se
        lanza como subproceso independiente.
        """
        guion = (
            "import tkinter as tk;from tkinter import filedialog;"
            "r=tk.Tk();r.withdraw();r.attributes('-topmost',True);"
            "print(filedialog.askdirectory(title='Carpeta donde guardar las fotos'))"
        )
        try:
            res = subprocess.run([sys.executable, "-c", guion],
                                 capture_output=True, text=True, timeout=180)
            carpeta = (res.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            self._json({"ok": False, "error": str(e)})
            return
        if not carpeta:
            self._json({"ok": False, "cancelado": True})
            return
        self._json({"ok": True, "carpeta": os.path.normpath(carpeta)})

    def _carpeta_para(self, fecha: dt.datetime) -> Path:
        base = Path(self.estado.cfg["carpeta_destino"])
        modo = self.estado.cfg.get("organizar", "anio-mes")
        if modo == "anio":
            return base / f"{fecha:%Y}"
        if modo == "anio-mes":
            return base / f"{fecha:%Y}" / f"{fecha:%Y-%m}"
        if modo == "anio-mes-dia":
            return base / f"{fecha:%Y}" / f"{fecha:%Y-%m}" / f"{fecha:%Y-%m-%d}"
        return base

    def _subir(self):
        est = self.estado
        cab = self.headers
        nombre = nombre_seguro(urllib.parse.unquote(cab.get("X-FP-Nombre", "")))
        ext = os.path.splitext(nombre)[1].lower()
        try:
            largo = int(cab.get("Content-Length") or 0)
        except ValueError:
            largo = 0

        if ext not in EXT_PERMITIDAS:
            self._json({"estado": "rechazado", "motivo": f"extension {ext or '?'}"}, 415)
            return
        if largo <= 0:
            self._json({"estado": "rechazado", "motivo": "archivo vacio"}, 400)
            return

        destino_base = Path(est.cfg["carpeta_destino"])
        try:
            destino_base.mkdir(parents=True, exist_ok=True)
            libre = shutil.disk_usage(destino_base).free
            if libre < largo + 50 * 1024 * 1024:
                self._json({"estado": "error",
                            "motivo": f"espacio insuficiente ({humano(libre)} libres)"}, 507)
                return
        except OSError as e:
            self._json({"estado": "error", "motivo": f"carpeta inaccesible: {e}"}, 500)
            return

        tmp_dir = destino_base / ".fotopuente"
        tmp_dir.mkdir(exist_ok=True)
        temporal = tmp_dir / f"{secrets.token_hex(8)}.part"

        # Recibe el cuerpo en bloques, calculando el hash sobre la marcha.
        sha = hashlib.sha256()
        recibido = 0
        try:
            with open(temporal, "wb") as f:
                while recibido < largo:
                    trozo = self.rfile.read(min(256 * 1024, largo - recibido))
                    if not trozo:
                        raise ConnectionError("conexion interrumpida")
                    f.write(trozo)
                    sha.update(trozo)
                    recibido += len(trozo)
        except (OSError, ConnectionError) as e:
            temporal.unlink(missing_ok=True)
            with est.lock:
                est.errores += 1
            est.emitir("archivo", {"nombre": nombre, "resultado": "error",
                                   "motivo": str(e)})
            self._json({"estado": "error", "motivo": str(e)}, 500)
            return

        digest = sha.hexdigest()

        # Ya lo teniamos con otro nombre: no duplicamos.
        existente = est.indice.por_hash(digest)
        if existente:
            temporal.unlink(missing_ok=True)
            with est.lock:
                est.duplicados += 1
            est.anotar({"nombre": nombre, "resultado": "duplicado",
                        "cuando": time.time()})
            est.emitir("archivo", {"nombre": nombre, "resultado": "duplicado"})
            self._json({"estado": "duplicado", "ruta": existente})
            return

        # Fecha: primero la del propio archivo, si no la que dijo el navegador.
        fecha = fechas.fecha_de_captura(str(temporal), nombre)
        origen_fecha = "exif"
        if fecha is None:
            origen_fecha = "navegador"
            try:
                ms = int(cab.get("X-FP-Fecha") or 0)
                fecha = dt.datetime.fromtimestamp(ms / 1000) if ms > 0 else None
            except (ValueError, OSError, OverflowError):
                fecha = None
        if fecha is None:
            origen_fecha = "ahora"
            fecha = dt.datetime.now()

        try:
            carpeta = self._carpeta_para(fecha)
            carpeta.mkdir(parents=True, exist_ok=True)
            final = ruta_libre(carpeta, nombre)
            os.replace(temporal, final)
            marca = fecha.timestamp()
            os.utime(final, (marca, marca))
        except OSError as e:
            temporal.unlink(missing_ok=True)
            with est.lock:
                est.errores += 1
            est.emitir("archivo", {"nombre": nombre, "resultado": "error",
                                   "motivo": str(e)})
            self._json({"estado": "error", "motivo": str(e)}, 500)
            return

        est.indice.registrar(digest, nombre, recibido, str(final), fecha)
        with est.lock:
            est.recibidos += 1
            est.bytes_recibidos += recibido
        entrada = {"nombre": nombre, "resultado": "guardado", "tamano": recibido,
                   "fecha": fecha.strftime("%Y-%m-%d %H:%M"), "origen": origen_fecha,
                   "ruta": str(final), "cuando": time.time()}
        est.anotar(entrada)
        est.emitir("archivo", entrada)
        self._json({"estado": "guardado", "ruta": str(final)})


# Motivos de fallo TLS traducidos: sin esto un rechazo de certificado es
# indistinguible de "no llega nada", que es justo lo que hay que diagnosticar.
_MOTIVOS_TLS = {
    "HTTP_REQUEST":
        "el cliente uso http:// en vez de https:// (usa el enlace del panel)",
    "TLSV1_ALERT_UNKNOWN_CA":
        "el movil NO acepto el certificado (toca 'visitar este sitio web')",
    "SSLV3_ALERT_CERTIFICATE_UNKNOWN":
        "el movil NO acepto el certificado (toca 'visitar este sitio web')",
    "TLSV1_ALERT_UNKNOWN_PSK_IDENTITY":
        "el movil cancelo la conexion",
    "UNSUPPORTED_PROTOCOL":
        "el cliente pide una version de TLS demasiado antigua",
    "WRONG_VERSION_NUMBER":
        "lo que llego no era TLS (¿http:// en el puerto seguro?)",
    "UNEXPECTED_EOF_WHILE_READING":
        "el cliente colgo sin completar el saludo (suele ser el certificado "
        "rechazado, o una app que solo comprueba si el puerto responde)",
    "NO_SHARED_CIPHER":
        "el cliente y el servidor no tienen ningun cifrado en comun",
}


class ServidorSeguro(ThreadingHTTPServer):
    """Servidor HTTPS que explica por que se cae una conexion en vez de callarse."""

    daemon_threads = True

    def __init__(self, direccion, handler, contexto: ssl.SSLContext):
        self.contexto = contexto
        super().__init__(direccion, handler)

    def get_request(self):
        conexion, origen = self.socket.accept()
        try:
            return self.contexto.wrap_socket(conexion, server_side=True), origen
        except ssl.SSLError as e:
            motivo = _MOTIVOS_TLS.get(getattr(e, "reason", None) or "")
            detalle = motivo or (getattr(e, "reason", None) or str(e))
            print(f"  ! {origen[0]:<15} conexion rechazada: {detalle}", flush=True)
            try:
                conexion.close()
            except OSError:
                pass
            raise            # socketserver lo trata como OSError y sigue
        except OSError:
            try:
                conexion.close()
            except OSError:
                pass
            raise

    def handle_error(self, request, client_address):
        excepcion = sys.exc_info()[1]
        # Tipico al cerrar la pestana a medias; no aporta nada verlo.
        if isinstance(excepcion, (ssl.SSLError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def preparar_tls(ips: list[str]) -> ssl.SSLContext | None:
    """Certificado y contexto TLS; None si no se puede y hay que seguir en claro."""
    try:
        crt, key = certificado.asegurar(DATOS / "certificado", ips)
    except certificado.SinCertificado as e:
        print(f"\n  No se pudo activar HTTPS.\n  {e}\n")
        return None

    try:
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(crt, key)
        return contexto
    except (ssl.SSLError, OSError) as e:
        print(f"\n  Certificado invalido ({e}); se continuara sin cifrado.\n")
        return None


# --- Arranque -----------------------------------------------------------------

def limpiar_temporales(destino: str) -> None:
    """Borra restos .part de ejecuciones anteriores interrumpidas."""
    tmp = Path(destino) / ".fotopuente"
    if not tmp.is_dir():
        return
    limite = time.time() - 3600
    for p in tmp.glob("*.part"):
        try:
            if p.stat().st_mtime < limite:
                p.unlink()
        except OSError:
            pass


def banner(cfg: dict, ips: list[str], url: str, url_panel: str, seguro: bool) -> None:
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │  FotoPuente  ·  iPhone  →  Windows por WiFi          │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    print(f"  Carpeta destino : {cfg['carpeta_destino']}")
    print(f"  Organizacion    : {cfg['organizar']}")
    print(f"  Cifrado         : {'HTTPS' if seguro else 'NO (http en claro)'}")
    print()
    print("  En el iPhone, abre esta direccion (o escanea el QR del panel):")
    print()
    print(f"      {url}")
    print()
    if seguro:
        print("  La primera vez Safari avisara de que el certificado no es de fiar.")
        print("  Es normal: lo ha creado tu propio PC. Toca 'Mostrar detalles' y")
        print("  luego 'Visitar este sitio web'. Solo se pregunta una vez.")
        print()
    if len(ips) > 1:
        print(f"  Otras IPs de este equipo: {', '.join(ips[1:])}")
        print()
    print(f"  Panel de control: {url_panel}")
    print()
    print("  El iPhone y el PC deben estar en la MISMA red WiFi.")
    print("  Para parar: Ctrl+C")
    print(flush=True)   # sin esto no se ve nada si se redirige la salida a un fichero


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pasa fotos y videos del iPhone a Windows por WiFi.")
    ap.add_argument("--carpeta", help="carpeta destino (local o \\\\servidor\\recurso)")
    ap.add_argument("--puerto", type=int, help="puerto TCP (por defecto 8765)")
    ap.add_argument("--organizar", choices=["plano", "anio", "anio-mes", "anio-mes-dia"],
                    help="como agrupar los archivos por fecha")
    ap.add_argument("--sin-panel", action="store_true",
                    help="no abrir el panel en el navegador")
    ap.add_argument("--http", action="store_true",
                    help="servir sin cifrar (Safari moderno lo rechaza)")
    ap.add_argument("--registro", action="store_true",
                    help="mostrar en consola todas las peticiones recibidas")
    args = ap.parse_args()

    cfg = cargar_config()
    if args.carpeta:
        cfg["carpeta_destino"] = os.path.normpath(args.carpeta)
    if args.puerto:
        cfg["puerto"] = args.puerto
    if args.organizar:
        cfg["organizar"] = args.organizar
    # A diferencia del resto, esta opcion vale solo para esta ejecucion.
    abrir_panel = cfg.get("abrir_panel", True) and not args.sin_panel

    try:
        os.makedirs(cfg["carpeta_destino"], exist_ok=True)
    except OSError as e:
        print(f"\n  ERROR: no se puede usar la carpeta destino:\n  {e}\n")
        print("  Cambiala con:  py -3 fotopuente.py --carpeta \"D:\\Mis Fotos\"\n")
        return 1

    guardar_config(cfg)
    limpiar_temporales(cfg["carpeta_destino"])

    ips = ips_locales()
    contexto = None if args.http else preparar_tls(ips)
    esquema = "http" if contexto is None else "https"

    indice = Indice(DATOS / "indice.db")
    Handler.estado = Estado(cfg, indice, esquema)
    Handler.registro_detallado = args.registro

    # Servidor para el movil: cifrado y accesible desde toda la red.
    try:
        if contexto is not None:
            servidor = ServidorSeguro(("0.0.0.0", cfg["puerto"]), Handler, contexto)
        else:
            servidor = ThreadingHTTPServer(("0.0.0.0", cfg["puerto"]), Handler)
            servidor.daemon_threads = True
    except OSError as e:
        print(f"\n  ERROR: no se pudo abrir el puerto {cfg['puerto']}: {e}")
        print("  Prueba con otro:  py -3 fotopuente.py --puerto 8080\n")
        return 1

    # Panel: siempre en claro y solo para este equipo, asi el navegador del PC
    # no protesta por el certificado autofirmado.
    panel = None
    puerto_panel = cfg["puerto"]
    if contexto is not None:
        for candidato in range(cfg["puerto"] + 1, cfg["puerto"] + 12):
            try:
                panel = ThreadingHTTPServer(("127.0.0.1", candidato), Handler)
                panel.daemon_threads = True
                puerto_panel = candidato
                break
            except OSError:
                continue
        if panel is None:
            print("  Aviso: no se pudo abrir el panel en claro; se usara HTTPS.")
            puerto_panel = cfg["puerto"]

    esquema_panel = "http" if panel is not None else esquema
    url_panel = f"{esquema_panel}://localhost:{puerto_panel}/panel"
    banner(cfg, ips, f"{esquema}://{ips[0]}:{cfg['puerto']}/?k={cfg['token']}",
           url_panel, contexto is not None)

    if panel is not None:
        threading.Thread(target=panel.serve_forever, daemon=True).start()

    if abrir_panel:
        threading.Timer(0.8, lambda: webbrowser.open(url_panel)).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\n  Cerrando FotoPuente...")
    finally:
        servidor.shutdown()
        servidor.server_close()
        if panel is not None:
            panel.shutdown()
            panel.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    sys.exit(main())
