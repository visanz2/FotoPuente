"""
qrlite - Generador de codigos QR minimo, sin dependencias externas.

Solo implementa lo que FotoPuente necesita: modo byte (UTF-8), versiones 1-10,
niveles de correccion L y M, seleccion automatica de mascara y salida SVG.
Es suficiente para URLs de LAN (hasta ~150 caracteres).

Referencia: ISO/IEC 18004.
"""

from __future__ import annotations

# --- Tablas de la norma (versiones 1..10) ------------------------------------

# Numero total de codewords (datos + correccion) por version.
_TOTAL_CODEWORDS = {
    1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
    6: 172, 7: 196, 8: 242, 9: 292, 10: 346,
}

# version -> (codewords de correccion por bloque, [(nº bloques, codewords de datos), ...])
_ECC_BLOCKS = {
    "L": {
        1: (7, [(1, 19)]),
        2: (10, [(1, 34)]),
        3: (15, [(1, 55)]),
        4: (20, [(1, 80)]),
        5: (26, [(1, 108)]),
        6: (18, [(2, 68)]),
        7: (20, [(2, 78)]),
        8: (24, [(2, 97)]),
        9: (30, [(2, 116)]),
        10: (18, [(2, 68), (2, 69)]),
    },
    "M": {
        1: (10, [(1, 16)]),
        2: (16, [(1, 28)]),
        3: (26, [(1, 44)]),
        4: (18, [(2, 32)]),
        5: (24, [(2, 43)]),
        6: (16, [(4, 27)]),
        7: (18, [(4, 31)]),
        8: (22, [(2, 38), (2, 39)]),
        9: (22, [(3, 36), (2, 37)]),
        10: (26, [(4, 43), (1, 44)]),
    },
}

# Centros de los patrones de alineacion por version.
_ALIGN_CENTERS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

# Indicador de nivel de correccion dentro de la informacion de formato.
_ECC_INDICATOR = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}


# --- Aritmetica en GF(256) ---------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D  # polinomio primitivo de QR
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    """Polinomio generador de Reed-Solomon del grado indicado."""
    # Coeficientes de mayor a menor grado; se multiplica por (x + alfa^i).
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef                              # termino en x
            nxt[j + 1] ^= _gf_mul(coef, _EXP[i])        # termino en alfa^i
        poly = nxt
    return poly


def _rs_encode(data: list[int], ec_len: int) -> list[int]:
    """Devuelve los codewords de correccion para un bloque de datos."""
    gen = _rs_generator(ec_len)
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i, coef in enumerate(gen[1:]):
            rem[i] ^= _gf_mul(coef, factor)
    return rem


# --- Codigos BCH para formato y version --------------------------------------

def _bch_format(data5: int) -> int:
    v = data5 << 10
    for i in range(4, -1, -1):
        if v & (1 << (i + 10)):
            v ^= 0b10100110111 << i
    return ((data5 << 10) | v) ^ 0b101010000010010


def _bch_version(version: int) -> int:
    v = version << 12
    for i in range(5, -1, -1):
        if v & (1 << (i + 12)):
            v ^= 0b1111100100101 << i
    return (version << 12) | v


# --- Construccion del flujo de bits ------------------------------------------

def _data_capacity(version: int, ecc: str) -> int:
    _, groups = _ECC_BLOCKS[ecc][version]
    return sum(count * size for count, size in groups)


def _pick_version(nbytes: int, ecc: str) -> int:
    for version in range(1, 11):
        # Cabecera: 4 bits de modo + indicador de longitud (8 bits hasta v9, 16 desde v10).
        count_bits = 8 if version <= 9 else 16
        needed = 4 + count_bits + nbytes * 8
        if needed <= _data_capacity(version, ecc) * 8:
            return version
    raise ValueError(
        f"Texto demasiado largo para qrlite ({nbytes} bytes, maximo version 10)"
    )


def _build_codewords(data: bytes, version: int, ecc: str) -> list[int]:
    capacity = _data_capacity(version, ecc)
    count_bits = 8 if version <= 9 else 16

    bits: list[int] = []

    def push(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)              # modo byte
    push(len(data), count_bits)
    for byte in data:
        push(byte, 8)

    # Terminador (hasta 4 bits) y relleno hasta byte completo.
    push(0, min(4, capacity * 8 - len(bits)))
    if len(bits) % 8:
        push(0, 8 - len(bits) % 8)

    codewords = [
        int("".join(str(b) for b in bits[i:i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    # Bytes de relleno alternos definidos por la norma, empezando por 0xEC.
    pad = (0xEC, 0x11)
    n = 0
    while len(codewords) < capacity:
        codewords.append(pad[n % 2])
        n += 1

    return codewords


def _interleave(codewords: list[int], version: int, ecc: str) -> list[int]:
    ec_len, groups = _ECC_BLOCKS[ecc][version]

    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    pos = 0
    for count, size in groups:
        for _ in range(count):
            block = codewords[pos:pos + size]
            pos += size
            data_blocks.append(block)
            ec_blocks.append(_rs_encode(block, ec_len))

    result: list[int] = []
    for i in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(ec_len):
        for block in ec_blocks:
            result.append(block[i])
    return result


# --- Matriz -------------------------------------------------------------------

def _new_matrix(version: int):
    size = version * 4 + 17
    matrix = [[0] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]
    return matrix, reserved, size


def _draw_finder(matrix, reserved, size, top, left) -> None:
    """Dibuja un patron localizador 7x7 con su separador."""
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = top + r, left + c
            if not (0 <= rr < size and 0 <= cc < size):
                continue
            inside = 0 <= r < 7 and 0 <= c < 7
            dark = inside and (
                r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
            )
            matrix[rr][cc] = 1 if dark else 0
            reserved[rr][cc] = True


def _draw_function_patterns(matrix, reserved, size, version) -> None:
    _draw_finder(matrix, reserved, size, 0, 0)
    _draw_finder(matrix, reserved, size, 0, size - 7)
    _draw_finder(matrix, reserved, size, size - 7, 0)

    # Patrones de sincronizacion (fila y columna 6).
    for i in range(size):
        if not reserved[6][i]:
            matrix[6][i] = 1 if i % 2 == 0 else 0
            reserved[6][i] = True
        if not reserved[i][6]:
            matrix[i][6] = 1 if i % 2 == 0 else 0
            reserved[i][6] = True

    # Patrones de alineacion (no se dibujan sobre los localizadores).
    centers = _ALIGN_CENTERS[version]
    for r in centers:
        for c in centers:
            if (r, c) in ((6, 6), (6, centers[-1]), (centers[-1], 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dark = max(abs(dr), abs(dc)) != 1
                    matrix[r + dr][c + dc] = 1 if dark else 0
                    reserved[r + dr][c + dc] = True

    # Modulo oscuro fijo.
    matrix[size - 8][8] = 1
    reserved[size - 8][8] = True

    # Zonas reservadas para la informacion de formato.
    for i in range(9):
        for rr, cc in ((8, i), (i, 8)):
            if not reserved[rr][cc]:
                reserved[rr][cc] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True

    # Zonas reservadas para la informacion de version (a partir de la 7).
    if version >= 7:
        for i in range(6):
            for j in range(3):
                reserved[size - 11 + j][i] = True
                reserved[i][size - 11 + j] = True


def _place_data(matrix, reserved, bits, size) -> None:
    col = size - 1
    upward = True
    idx = 0
    while col > 0:
        if col == 6:  # la columna de sincronizacion se salta
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not reserved[r][c]:
                    matrix[r][c] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        upward = not upward
        col -= 2


_MASKS = (
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0,
)


def _apply_mask(matrix, reserved, size, mask):
    rule = _MASKS[mask]
    out = [row[:] for row in matrix]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and rule(r, c):
                out[r][c] ^= 1
    return out


def _draw_format_info(matrix, size, ecc, mask) -> None:
    value = _bch_format((_ECC_INDICATOR[ecc] << 3) | mask)

    def bit(i: int) -> int:
        return (value >> i) & 1

    # Copia 1: columna 8 hacia abajo, luego fila 8 hacia la izquierda.
    for i in range(6):
        matrix[i][8] = bit(i)
    matrix[7][8] = bit(6)
    matrix[8][8] = bit(7)
    matrix[8][7] = bit(8)
    for i in range(9, 15):
        matrix[8][14 - i] = bit(i)

    # Copia 2: fila 8 por la derecha y columna 8 por abajo.
    # No toca el modulo oscuro fijo de (size-8, 8).
    for i in range(8):
        matrix[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        matrix[size - 15 + i][8] = bit(i)


def _draw_version_info(matrix, size, version) -> None:
    if version < 7:
        return
    value = _bch_version(version)
    for i in range(18):
        bit = (value >> i) & 1
        r, c = i // 3, i % 3
        matrix[size - 11 + c][r] = bit
        matrix[r][size - 11 + c] = bit


# --- Puntuacion de mascaras ---------------------------------------------------

def _penalty(matrix, size) -> int:
    score = 0

    # Regla 1: rachas de 5 o mas modulos del mismo color.
    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        run, prev = 1, line[0]
        for value in line[1:]:
            if value == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, value
        if run >= 5:
            score += 3 + (run - 5)

    # Regla 2: bloques 2x2 del mismo color.
    for r in range(size - 1):
        for c in range(size - 1):
            v = matrix[r][c]
            if v == matrix[r][c + 1] == matrix[r + 1][c] == matrix[r + 1][c + 1]:
                score += 3

    # Regla 3: patrones que imitan a un localizador.
    p1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    p2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        for i in range(size - 10):
            window = list(line[i:i + 11])
            if window == p1 or window == p2:
                score += 40

    # Regla 4: desequilibrio entre modulos claros y oscuros.
    dark = sum(sum(row) for row in matrix)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)

    return score


# --- API publica --------------------------------------------------------------

def encode(text: str, ecc: str = "M") -> list[list[int]]:
    """Codifica `text` y devuelve la matriz de modulos (1 = oscuro)."""
    if ecc not in ("L", "M"):
        raise ValueError("qrlite solo implementa los niveles L y M")

    data = text.encode("utf-8")
    version = _pick_version(len(data), ecc)

    codewords = _build_codewords(data, version, ecc)
    final = _interleave(codewords, version, ecc)

    bits: list[int] = []
    for byte in final:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)

    matrix, reserved, size = _new_matrix(version)
    _draw_function_patterns(matrix, reserved, size, version)
    _place_data(matrix, reserved, bits, size)

    best = None
    for mask in range(8):
        candidate = _apply_mask(matrix, reserved, size, mask)
        _draw_format_info(candidate, size, ecc, mask)
        _draw_version_info(candidate, size, version)
        score = _penalty(candidate, size)
        if best is None or score < best[0]:
            best = (score, candidate)

    return best[1]


def to_svg(text: str, ecc: str = "M", quiet_zone: int = 4, scale: int = 8) -> str:
    """Devuelve el QR como SVG independiente (sin recursos externos)."""
    matrix = encode(text, ecc)
    size = len(matrix)
    total = size + quiet_zone * 2
    dim = total * scale

    # Cada fila oscura se emite como un unico `path` para mantener el SVG pequeno.
    parts = []
    for r, row in enumerate(matrix):
        c = 0
        while c < size:
            if row[c]:
                start = c
                while c < size and row[c]:
                    c += 1
                parts.append(
                    f"M{(start + quiet_zone) * scale},{(r + quiet_zone) * scale}"
                    f"h{(c - start) * scale}v{scale}h-{(c - start) * scale}z"
                )
            else:
                c += 1

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges" role="img" '
        f'aria-label="Codigo QR">'
        f'<rect width="{dim}" height="{dim}" fill="#ffffff"/>'
        f'<path d="{"".join(parts)}" fill="#000000"/>'
        f"</svg>"
    )


def to_ascii(text: str, ecc: str = "M", quiet_zone: int = 2) -> str:
    """Version para terminal, usando medios bloques Unicode."""
    matrix = encode(text, ecc)
    size = len(matrix)
    grid = [[0] * (size + quiet_zone * 2) for _ in range(quiet_zone)]
    for row in matrix:
        grid.append([0] * quiet_zone + list(row) + [0] * quiet_zone)
    grid.extend([[0] * (size + quiet_zone * 2) for _ in range(quiet_zone)])
    if len(grid) % 2:
        grid.append([0] * len(grid[0]))

    lines = []
    for r in range(0, len(grid), 2):
        line = []
        for c in range(len(grid[0])):
            top, bottom = grid[r][c], grid[r + 1][c]
            # El modulo oscuro se pinta como espacio en blanco sobre fondo claro.
            line.append(" ▄▀█"[(top << 1) | bottom])
        lines.append("".join(line))
    return "\n".join(lines)
