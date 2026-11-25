"""Fotos de los modelos de sofá y de sus piezas.

Las imágenes se guardan como ficheros en una carpeta (por defecto `imagenes/`, junto a la base de
datos) con un nombre aleatorio; en la base de datos solo se guarda ese nombre.
El navegador las reduce antes de subirlas, así que una foto del móvil ocupa ~200-400 KB.
"""
from __future__ import annotations

import base64
import binascii
import re
import uuid
from pathlib import Path

from ..errores import ErrorNegocio, NoEncontrado

TIPOS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_BYTES = 8 * 1024 * 1024
NOMBRE_VALIDO = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
FIRMAS = {"jpg": (b"\xff\xd8\xff",), "png": (b"\x89PNG\r\n\x1a\n",), "webp": (b"RIFF",)}


def es_nombre_valido(nombre: str | None) -> bool:
    return nombre is None or bool(NOMBRE_VALIDO.match(nombre))


def validar(nombre: str | None) -> str | None:
    nombre = nombre or None
    if not es_nombre_valido(nombre):
        raise ErrorNegocio(f"Nombre de imagen no válido: {nombre!r}")
    return nombre


def guardar(carpeta: Path, data_url: str) -> str:
    """Recibe 'data:image/jpeg;base64,....' y devuelve el nombre del fichero guardado."""
    m = re.match(r"^data:(image/[a-z]+);base64,(.+)$", data_url or "", re.S)
    if not m or m.group(1) not in TIPOS:
        raise ErrorNegocio("Formato de imagen no admitido (usa JPG, PNG o WEBP)")
    try:
        datos = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ErrorNegocio("La imagen está dañada") from e
    if len(datos) > MAX_BYTES:
        raise ErrorNegocio("La imagen es demasiado grande (máximo 8 MB)")
    ext = TIPOS[m.group(1)]
    if not datos.startswith(FIRMAS[ext]):
        raise ErrorNegocio("El fichero no es una imagen válida")
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"{uuid.uuid4().hex}.{ext}"
    (carpeta / nombre).write_bytes(datos)
    return nombre


def ruta(carpeta: Path, nombre: str) -> Path:
    if not NOMBRE_VALIDO.match(nombre or ""):
        raise NoEncontrado("Imagen no encontrada")
    p = carpeta / nombre
    if not p.is_file():
        raise NoEncontrado("Imagen no encontrada")
    return p
