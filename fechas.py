"""
Extraccion de la fecha real de captura de fotos y videos.

Lee EXIF en JPEG y HEIC, y la caja `mvhd` en MP4/MOV, usando solo la libreria
estandar. Si no encuentra nada fiable devuelve None y el que llama usa la fecha
que reporto el navegador.
"""

from __future__ import annotations

import datetime as _dt
import os
import struct

# Rango plausible para descartar valores corruptos o marcadores vacios.
_MIN = _dt.datetime(1995, 1, 1)


def _plausible(fecha: _dt.datetime | None) -> _dt.datetime | None:
    if fecha is None:
        return None
    limite = _dt.datetime.now() + _dt.timedelta(days=2)
    return fecha if _MIN <= fecha <= limite else None


# --- EXIF en JPEG -------------------------------------------------------------

_TAG_DATETIME = 0x0132          # DateTime (IFD0)
_TAG_EXIF_IFD = 0x8769          # puntero al IFD de Exif
_TAG_ORIGINAL = 0x9003          # DateTimeOriginal
_TAG_DIGITIZED = 0x9004         # DateTimeDigitized


def _leer_ascii(blob: bytes, orden: str, tipo: int, count: int, campo: bytes) -> str | None:
    if tipo != 2 or count == 0:          # 2 = ASCII
        return None
    if count <= 4:
        crudo = campo[:count]
    else:
        (offset,) = struct.unpack(orden + "I", campo)
        if offset + count > len(blob):
            return None
        crudo = blob[offset:offset + count]
    return crudo.split(b"\x00")[0].decode("ascii", "ignore").strip() or None


def _fecha_exif_str(texto: str | None) -> _dt.datetime | None:
    if not texto:
        return None
    for formato in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(texto[:19], formato)
        except ValueError:
            continue
    return None


def _recorrer_ifd(blob: bytes, orden: str, offset: int) -> dict[int, tuple[int, int, bytes]]:
    """Devuelve {tag: (tipo, count, bytes del campo valor)}."""
    if offset + 2 > len(blob):
        return {}
    (n,) = struct.unpack_from(orden + "H", blob, offset)
    entradas: dict[int, tuple[int, int, bytes]] = {}
    for i in range(n):
        pos = offset + 2 + i * 12
        if pos + 12 > len(blob):
            break
        tag, tipo, count = struct.unpack_from(orden + "HHI", blob, pos)
        entradas[tag] = (tipo, count, blob[pos + 8:pos + 12])
    return entradas


def _fecha_tiff(blob: bytes) -> _dt.datetime | None:
    if len(blob) < 8:
        return None
    if blob[:2] == b"II":
        orden = "<"
    elif blob[:2] == b"MM":
        orden = ">"
    else:
        return None
    magia, ifd0 = struct.unpack_from(orden + "HI", blob, 2)
    if magia != 42:
        return None

    entradas = _recorrer_ifd(blob, orden, ifd0)

    # DateTimeOriginal (dentro del sub-IFD de Exif) es la fecha de captura real.
    if _TAG_EXIF_IFD in entradas:
        tipo, count, campo = entradas[_TAG_EXIF_IFD]
        if tipo in (4, 13):                       # LONG
            (sub,) = struct.unpack(orden + "I", campo)
            sub_entradas = _recorrer_ifd(blob, orden, sub)
            for tag in (_TAG_ORIGINAL, _TAG_DIGITIZED):
                if tag in sub_entradas:
                    t, c, v = sub_entradas[tag]
                    fecha = _fecha_exif_str(_leer_ascii(blob, orden, t, c, v))
                    if fecha:
                        return fecha

    # Ultimo recurso: DateTime de IFD0 (fecha de modificacion del fichero).
    if _TAG_DATETIME in entradas:
        t, c, v = entradas[_TAG_DATETIME]
        return _fecha_exif_str(_leer_ascii(blob, orden, t, c, v))

    return None


def _fecha_jpeg(ruta: str) -> _dt.datetime | None:
    with open(ruta, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return None
        # Recorre los segmentos de cabecera hasta encontrar APP1/Exif.
        for _ in range(64):
            cab = f.read(2)
            if len(cab) < 2 or cab[0] != 0xFF:
                return None
            marcador = cab[1]
            if marcador in (0xD8, 0xD9, 0xDA):    # SOI / EOI / inicio de imagen
                return None
            largo_raw = f.read(2)
            if len(largo_raw) < 2:
                return None
            largo = struct.unpack(">H", largo_raw)[0]
            if largo < 2:
                return None
            datos = f.read(largo - 2)
            if marcador == 0xE1 and datos[:6] == b"Exif\x00\x00":
                return _fecha_tiff(datos[6:])
    return None


# --- MP4 / MOV ----------------------------------------------------------------

_EPOCA_QT = _dt.datetime(1904, 1, 1)


def _buscar_caja(f, fin: int, objetivo: bytes, profundidad: int = 0):
    """Busca recursivamente una caja ISO-BMFF y deja el fichero en su contenido."""
    if profundidad > 4:
        return None
    while f.tell() < fin - 8:
        inicio = f.tell()
        cab = f.read(8)
        if len(cab) < 8:
            return None
        tam, tipo = struct.unpack(">I4s", cab)
        if tam == 1:                       # tamano extendido de 64 bits
            ext = f.read(8)
            if len(ext) < 8:
                return None
            tam = struct.unpack(">Q", ext)[0]
            cuerpo = inicio + 16
        elif tam == 0:                     # se extiende hasta el final
            tam = fin - inicio
            cuerpo = inicio + 8
        else:
            cuerpo = inicio + 8
        if tam < 8:
            return None

        if tipo == objetivo:
            f.seek(cuerpo)
            return inicio + tam
        if tipo in (b"moov", b"trak", b"mdia"):
            f.seek(cuerpo)
            hallado = _buscar_caja(f, inicio + tam, objetivo, profundidad + 1)
            if hallado:
                return hallado
        f.seek(inicio + tam)
    return None


def _fecha_mp4(ruta: str) -> _dt.datetime | None:
    total = os.path.getsize(ruta)
    with open(ruta, "rb") as f:
        if not _buscar_caja(f, total, b"mvhd"):
            return None
        cabecera = f.read(4)
        if len(cabecera) < 4:
            return None
        version = cabecera[0]
        crudo = f.read(8 if version == 1 else 4)
        if len(crudo) < (8 if version == 1 else 4):
            return None
        segundos = struct.unpack(">Q" if version == 1 else ">I", crudo)[0]
        if segundos == 0:
            return None
        try:
            return _EPOCA_QT + _dt.timedelta(seconds=segundos)
        except OverflowError:
            return None


# --- HEIC / HEIF --------------------------------------------------------------
# El HEIC guarda el mismo Exif que un JPEG, pero dentro de la caja `meta` del
# contenedor ISO-BMFF. Hay que localizar el "item" de tipo Exif (via iinf/iloc)
# y desde su posicion saltar al bloque TIFF, que ya sabemos interpretar.


def _iter_cajas(f, inicio: int, fin: int):
    """Recorre las cajas hijas entre [inicio, fin); cede (tipo, cuerpo, final)."""
    f.seek(inicio)
    while f.tell() < fin - 8:
        pos = f.tell()
        cab = f.read(8)
        if len(cab) < 8:
            return
        tam, tipo = struct.unpack(">I4s", cab)
        if tam == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            tam = struct.unpack(">Q", ext)[0]
            cuerpo = pos + 16
        elif tam == 0:
            tam = fin - pos
            cuerpo = pos + 8
        else:
            cuerpo = pos + 8
        if tam < 8 or pos + tam > fin:
            return
        yield tipo, cuerpo, pos + tam
        f.seek(pos + tam)


def _exif_item_id(f, meta_ini: int, meta_fin: int) -> int | None:
    """ID del item Exif dentro de la caja `iinf`."""
    for tipo, sci, scf in _iter_cajas(f, meta_ini, meta_fin):
        if tipo != b"iinf":
            continue
        f.seek(sci)
        ver = f.read(4)[0]                         # iinf es FullBox
        f.read(2 if ver == 0 else 4)               # entry_count
        for t2, ici, _ in _iter_cajas(f, f.tell(), scf):
            if t2 != b"infe":
                continue
            f.seek(ici)
            iver = f.read(4)[0]
            if iver < 2:
                continue
            iid = struct.unpack(">H", f.read(2))[0] if iver == 2 else \
                struct.unpack(">I", f.read(4))[0]
            f.read(2)                              # item_protection_index
            if f.read(4) == b"Exif":
                return iid
    return None


def _exif_extent(f, meta_ini: int, meta_fin: int, item_id: int):
    """Posicion y longitud del dato del item pedido, segun la caja `iloc`."""
    for tipo, sci, _ in _iter_cajas(f, meta_ini, meta_fin):
        if tipo != b"iloc":
            continue
        f.seek(sci)
        ver = f.read(1)[0]
        f.read(3)                                  # flags
        b = f.read(2)
        offset_size, length_size = (b[0] >> 4) & 0xF, b[0] & 0xF
        base_offset_size, index_size = (b[1] >> 4) & 0xF, b[1] & 0xF
        count = struct.unpack(">H", f.read(2))[0] if ver < 2 else \
            struct.unpack(">I", f.read(4))[0]

        def leer(n):
            return int.from_bytes(f.read(n), "big") if n else 0

        for _ in range(count):
            iid = struct.unpack(">H", f.read(2))[0] if ver < 2 else \
                struct.unpack(">I", f.read(4))[0]
            if ver in (1, 2):
                f.read(2)                          # construction_method
            f.read(2)                              # data_reference_index
            base = leer(base_offset_size)
            for _e in range(struct.unpack(">H", f.read(2))[0]):
                if ver in (1, 2) and index_size:
                    leer(index_size)
                off, ln = leer(offset_size), leer(length_size)
                if iid == item_id:
                    return base + off, ln
        return None
    return None


def _fecha_heic(ruta: str) -> _dt.datetime | None:
    total = os.path.getsize(ruta)
    with open(ruta, "rb") as f:
        meta = None
        for tipo, ci, cf in _iter_cajas(f, 0, total):
            if tipo == b"meta":
                meta = (ci + 4, cf)                # meta es FullBox (4 bytes)
                break
        if not meta:
            return None

        item_id = _exif_item_id(f, *meta)
        if item_id is None:
            return None
        extent = _exif_extent(f, *meta, item_id)
        if not extent:
            return None

        offset, largo = extent
        f.seek(offset)
        crudo = f.read(min(largo, 128 * 1024))
        if len(crudo) < 4:
            return None
        # Los primeros 4 bytes indican cuanto saltar hasta la cabecera TIFF.
        salto = struct.unpack(">I", crudo[:4])[0]
        return _fecha_tiff(crudo[4 + salto:])


# --- API publica --------------------------------------------------------------

_JPEG = (".jpg", ".jpeg", ".jpe")
_HEIC = (".heic", ".heif")
_VIDEO = (".mp4", ".mov", ".m4v", ".3gp")


def fecha_de_captura(ruta: str, nombre: str) -> _dt.datetime | None:
    """Fecha de captura leida del propio archivo, o None si no se puede saber."""
    minusculas = nombre.lower()
    try:
        if minusculas.endswith(_JPEG):
            return _plausible(_fecha_jpeg(ruta))
        if minusculas.endswith(_HEIC):
            return _plausible(_fecha_heic(ruta))
        if minusculas.endswith(_VIDEO):
            return _plausible(_fecha_mp4(ruta))
    except (OSError, struct.error, ValueError):
        return None
    return None
