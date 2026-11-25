"""Capa de base de datos (SQLite, librería estándar).

Se usa SQLite para empezar sin servidor. El esquema está escrito en SQL estándar
para poder migrar a PostgreSQL cuando la empresa crezca.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DB_PATH = os.environ.get("ERP_DB_PATH", "erp.db")

DEPARTAMENTOS = ("almacen", "cinchado", "corte", "costura", "tapizado", "montaje", "oficina")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trabajadores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT    NOT NULL,
    departamento  TEXT    NOT NULL,
    activo        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS materiales (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo        TEXT    NOT NULL UNIQUE,
    nombre        TEXT    NOT NULL,
    categoria     TEXT    NOT NULL,           -- ver tabla categorias: goma, guata, brazo, esqueleto...
    unidad        TEXT    NOT NULL,           -- m, m2, ud, kg
    stock         REAL    NOT NULL DEFAULT 0 CHECK (stock >= 0),
    stock_minimo  REAL    NOT NULL DEFAULT 0,
    ubicacion     TEXT,                       -- p.ej. "Pasillo 3 - Estante B"
    proveedor     TEXT,
    producto_id   INTEGER REFERENCES productos(id)   -- opcional: pieza de un modelo concreto
);

-- Categorías del stock general. 'material' = se compra (goma, guata, tela...);
-- 'pieza' = se fabrica en el taller (esqueletos, brazos, respaldos, cabezales, arcones...)
CREATE TABLE IF NOT EXISTS categorias (
    nombre    TEXT PRIMARY KEY,
    grupo     TEXT NOT NULL CHECK (grupo IN ('material', 'pieza')),
    posicion  INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS movimientos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id    INTEGER NOT NULL REFERENCES materiales(id),
    tipo           TEXT    NOT NULL CHECK (tipo IN ('entrada', 'salida', 'ajuste')),
    cantidad       REAL    NOT NULL,          -- con signo: + entra, - sale
    motivo         TEXT,
    trabajador_id  INTEGER REFERENCES trabajadores(id),
    orden_id       INTEGER REFERENCES ordenes(id),
    fecha          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS productos (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo  TEXT NOT NULL UNIQUE,
    nombre  TEXT NOT NULL,                    -- p.ej. "Sofá Nórdico 3 plazas"
    stock   INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),  -- unidades terminadas en almacén
    -- Ficha técnica del modelo (la rellena corte)
    forma            TEXT,                    -- recto, chaise longue, rinconera, curvo...
    ancho_cm         REAL,
    fondo_cm         REAL,
    alto_cm          REAL,
    alto_asiento_cm  REAL,
    descripcion      TEXT,
    imagen           TEXT                     -- foto del sofá completo (nombre de fichero)
);

-- Preparado de un modelo: qué goma, guata, tela... lleva cada pieza y con qué medidas.
-- material_id es opcional: se puede enlazar a un material del almacén o escribirlo a mano.
CREATE TABLE IF NOT EXISTS preparado (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    posicion     INTEGER NOT NULL DEFAULT 0,
    pieza        TEXT    NOT NULL,            -- asiento, respaldo, brazo, arcón, cojín...
    tipo         TEXT,                        -- goma, guata, tela, madera, rafia...
    material_id  INTEGER REFERENCES materiales(id),
    descripcion  TEXT,                        -- "HR35 densidad 35", "guata 200 g doble"...
    medidas      TEXT,                        -- "60 x 55 x 10 cm"
    cantidad     REAL,
    notas        TEXT,
    imagen       TEXT                         -- foto de la pieza cortada (goma, guata...)
);

-- Escandallo: materiales necesarios para fabricar 1 unidad de producto
CREATE TABLE IF NOT EXISTS escandallos (
    producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    material_id  INTEGER NOT NULL REFERENCES materiales(id),
    cantidad     REAL    NOT NULL CHECK (cantidad > 0),
    PRIMARY KEY (producto_id, material_id)
);

CREATE TABLE IF NOT EXISTS ordenes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id     INTEGER NOT NULL REFERENCES productos(id),
    cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
    tipo            TEXT    NOT NULL DEFAULT 'stock' CHECK (tipo IN ('stock', 'cliente')),
    cliente         TEXT,
    notas           TEXT,
    estado          TEXT    NOT NULL DEFAULT 'pendiente',
    fecha_creacion  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_estado    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Pedidos a proveedores (los gestiona oficina)
CREATE TABLE IF NOT EXISTS pedidos_compra (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor        TEXT NOT NULL,
    estado           TEXT NOT NULL DEFAULT 'borrador'
                     CHECK (estado IN ('borrador', 'enviado', 'recibido', 'cancelado')),
    notas            TEXT,
    creado_por       INTEGER REFERENCES trabajadores(id),
    fecha_creacion   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_envio      TEXT,
    fecha_recepcion  TEXT
);

CREATE TABLE IF NOT EXISTS lineas_compra (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id          INTEGER NOT NULL REFERENCES pedidos_compra(id) ON DELETE CASCADE,
    material_id        INTEGER NOT NULL REFERENCES materiales(id),
    cantidad           REAL NOT NULL CHECK (cantidad > 0),
    cantidad_recibida  REAL,
    UNIQUE (pedido_id, material_id)
);

CREATE TABLE IF NOT EXISTS mensajes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    autor_id               INTEGER REFERENCES trabajadores(id),   -- NULL = sistema
    departamento_destino   TEXT,                                  -- NULL + destinatario NULL = todos
    destinatario_id        INTEGER REFERENCES trabajadores(id),
    orden_id               INTEGER REFERENCES ordenes(id),
    prioridad              TEXT NOT NULL DEFAULT 'normal' CHECK (prioridad IN ('baja', 'normal', 'urgente')),
    texto                  TEXT NOT NULL,
    fecha                  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS mensajes_leidos (
    mensaje_id     INTEGER NOT NULL REFERENCES mensajes(id) ON DELETE CASCADE,
    trabajador_id  INTEGER NOT NULL REFERENCES trabajadores(id),
    fecha          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (mensaje_id, trabajador_id)
);

CREATE INDEX IF NOT EXISTS idx_mov_material ON movimientos(material_id);
CREATE INDEX IF NOT EXISTS idx_ordenes_estado ON ordenes(estado);
CREATE INDEX IF NOT EXISTS idx_mensajes_depto ON mensajes(departamento_destino);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


CATEGORIAS_INICIALES = {
    "material": ["tela", "goma", "guata", "madera", "rafia", "cincha", "fibra", "pata", "fornitura", "embalaje"],
    "pieza": ["esqueleto", "brazo", "respaldo", "cabezal", "asiento", "arcón", "cojín"],
}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrar(conn)
    for grupo, nombres in CATEGORIAS_INICIALES.items():
        for posicion, nombre in enumerate(nombres):
            conn.execute("INSERT OR IGNORE INTO categorias (nombre, grupo, posicion) VALUES (?,?,?)",
                         (nombre, grupo, posicion))
    # Categorías escritas a mano en materiales antiguos
    conn.execute("""INSERT OR IGNORE INTO categorias (nombre, grupo)
                    SELECT DISTINCT categoria, 'material' FROM materiales""")
    conn.commit()


def _migrar(conn: sqlite3.Connection) -> None:
    """Añade columnas nuevas a bases de datos creadas con versiones anteriores."""
    def columnas(tabla: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}

    nuevas_productos = {
        "stock": "INTEGER NOT NULL DEFAULT 0", "forma": "TEXT", "ancho_cm": "REAL", "fondo_cm": "REAL",
        "alto_cm": "REAL", "alto_asiento_cm": "REAL", "descripcion": "TEXT", "imagen": "TEXT",
    }
    if "imagen" not in columnas("preparado"):
        conn.execute("ALTER TABLE preparado ADD COLUMN imagen TEXT")
    if "producto_id" not in columnas("materiales"):
        conn.execute("ALTER TABLE materiales ADD COLUMN producto_id INTEGER REFERENCES productos(id)")
    existentes = columnas("productos")
    for col, tipo in nuevas_productos.items():
        if col not in existentes:
            conn.execute(f"ALTER TABLE productos ADD COLUMN {col} {tipo}")
    if "tipo" not in columnas("ordenes"):
        conn.execute("ALTER TABLE ordenes ADD COLUMN tipo TEXT NOT NULL DEFAULT 'stock'")
        conn.execute("UPDATE ordenes SET tipo = 'cliente' WHERE cliente IS NOT NULL")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Todo o nada: si algo falla, no se toca el stock a medias."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def row(cursor: sqlite3.Cursor) -> dict | None:
    r = cursor.fetchone()
    return dict(r) if r else None
