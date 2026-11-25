"""Almacén: materiales, entradas, salidas, ajustes de inventario y alertas de stock."""
from __future__ import annotations

import sqlite3

from ..db import row, rows, transaction
from ..errores import ErrorNegocio, NoEncontrado
from . import mensajes


SELECT_MATERIAL = """
    SELECT m.*, m.stock < m.stock_minimo AS bajo_minimo,
           COALESCE(c.grupo, 'material') AS grupo, p.nombre AS modelo
    FROM materiales m
    LEFT JOIN categorias c ON c.nombre = m.categoria
    LEFT JOIN productos p ON p.id = m.producto_id
"""


# ---------- Categorías ----------

def listar_categorias(conn) -> list[dict]:
    """Categorías con cuántos artículos tienen y cuántos están bajo mínimo (para los filtros)."""
    return rows(conn.execute("""
        SELECT c.nombre, c.grupo,
               COUNT(m.id) AS articulos,
               COALESCE(SUM(m.stock < m.stock_minimo), 0) AS bajo_minimo
        FROM categorias c LEFT JOIN materiales m ON m.categoria = c.nombre
        GROUP BY c.nombre ORDER BY c.grupo, c.posicion, c.nombre
    """))


def crear_categoria(conn, nombre: str, grupo: str) -> dict:
    nombre = (nombre or "").strip().lower()
    if not nombre:
        raise ErrorNegocio("La categoría necesita un nombre")
    if grupo not in ("material", "pieza"):
        raise ErrorNegocio("El grupo debe ser 'material' o 'pieza'")
    with transaction(conn):
        try:
            conn.execute("INSERT INTO categorias (nombre, grupo) VALUES (?, ?)", (nombre, grupo))
        except sqlite3.IntegrityError as e:
            raise ErrorNegocio(f"La categoría {nombre!r} ya existe") from e
    return {"nombre": nombre, "grupo": grupo}


# ---------- Materiales y piezas ----------

def crear_material(conn: sqlite3.Connection, **datos) -> dict:
    campos = ("codigo", "nombre", "categoria", "unidad", "stock", "stock_minimo", "ubicacion", "proveedor",
              "producto_id")
    valores = {k: datos.get(k) for k in campos}
    valores["stock"] = valores["stock"] or 0
    valores["stock_minimo"] = valores["stock_minimo"] or 0
    valores["categoria"] = (valores["categoria"] or "").strip().lower()
    if not valores["categoria"]:
        raise ErrorNegocio("Indica la categoría (goma, guata, brazo, esqueleto...)")
    if valores["producto_id"] and not conn.execute(
            "SELECT 1 FROM productos WHERE id = ?", (valores["producto_id"],)).fetchone():
        raise NoEncontrado(f"Modelo {valores['producto_id']} no existe")
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO categorias (nombre, grupo) VALUES (?, 'material')",
                     (valores["categoria"],))
        try:
            cur = conn.execute(
                f"INSERT INTO materiales ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                tuple(valores.values()),
            )
        except sqlite3.IntegrityError as e:
            raise ErrorNegocio(f"Ya existe un material con código {valores['codigo']!r}") from e
    return obtener_material(conn, cur.lastrowid)


def obtener_material(conn: sqlite3.Connection, material_id: int) -> dict:
    m = row(conn.execute(SELECT_MATERIAL + " WHERE m.id = ?", (material_id,)))
    if not m:
        raise NoEncontrado(f"Material {material_id} no existe")
    m["bajo_minimo"] = bool(m["bajo_minimo"])
    return m


def buscar_por_codigo(conn: sqlite3.Connection, codigo: str) -> dict:
    """Pensado para lectores de código de barras / QR en el almacén."""
    m = row(conn.execute("SELECT id FROM materiales WHERE codigo = ?", (codigo,)))
    if not m:
        raise NoEncontrado(f"No hay ningún material con código {codigo!r}")
    return obtener_material(conn, m["id"])


def listar_materiales(conn: sqlite3.Connection, categoria: str | None = None, solo_bajo_minimo: bool = False,
                      grupo: str | None = None, producto_id: int | None = None, q: str | None = None) -> list[dict]:
    """Stock general con filtros: categoría (goma, brazo...), grupo (material/pieza), modelo y texto."""
    sql = SELECT_MATERIAL + " WHERE 1=1"
    params: list = []
    if categoria:
        sql += " AND m.categoria = ?"
        params.append(categoria.lower())
    if grupo:
        sql += " AND COALESCE(c.grupo, 'material') = ?"
        params.append(grupo)
    if producto_id:
        sql += " AND m.producto_id = ?"
        params.append(producto_id)
    if q:
        sql += " AND (m.codigo LIKE ? OR m.nombre LIKE ? OR m.ubicacion LIKE ? OR p.nombre LIKE ?)"
        params += [f"%{q}%"] * 4
    if solo_bajo_minimo:
        sql += " AND m.stock < m.stock_minimo"
    sql += " ORDER BY COALESCE(c.grupo, 'material'), COALESCE(c.posicion, 100), m.categoria, m.nombre"
    resultado = rows(conn.execute(sql, params))
    for m in resultado:
        m["bajo_minimo"] = bool(m["bajo_minimo"])
    return resultado


def _aplicar_movimiento(
    conn: sqlite3.Connection,
    material_id: int,
    tipo: str,
    delta: float,
    motivo: str | None,
    trabajador_id: int | None,
    orden_id: int | None = None,
) -> None:
    """Aplica un movimiento SIN hacer commit (lo usa también producción dentro de su transacción)."""
    m = obtener_material(conn, material_id)
    nuevo = m["stock"] + delta
    if nuevo < 0:
        raise ErrorNegocio(
            f"Stock insuficiente de {m['nombre']}: hay {m['stock']} {m['unidad']}, se piden {-delta}",
            {"material_id": material_id, "disponible": m["stock"], "solicitado": -delta},
        )
    conn.execute("UPDATE materiales SET stock = ? WHERE id = ?", (nuevo, material_id))
    conn.execute(
        "INSERT INTO movimientos (material_id, tipo, cantidad, motivo, trabajador_id, orden_id) VALUES (?,?,?,?,?,?)",
        (material_id, tipo, delta, motivo, trabajador_id, orden_id),
    )
    # Aviso automático al almacén solo en el momento en que se cruza el mínimo
    if m["stock"] >= m["stock_minimo"] > nuevo:
        texto = (f"Stock bajo mínimo: {m['nombre']} ({m['codigo']}) quedan {nuevo:g} {m['unidad']} "
                 f"(mínimo {m['stock_minimo']:g}).")
        mensajes.aviso_sistema(conn, departamento="almacen", prioridad="urgente",
                               texto=f"{texto} Proveedor: {m['proveedor'] or 'sin asignar'}.")
        if m["grupo"] != "pieza":
            mensajes.aviso_sistema(conn, departamento="oficina", prioridad="urgente",
                                   texto=f"{texto} Está en Compras → Sugerencias para pedirlo.")


def registrar_entrada(conn, material_id: int, cantidad: float, motivo: str | None = None, trabajador_id: int | None = None) -> dict:
    if cantidad <= 0:
        raise ErrorNegocio("La cantidad de una entrada debe ser positiva")
    with transaction(conn):
        _aplicar_movimiento(conn, material_id, "entrada", cantidad, motivo or "Recepción de proveedor", trabajador_id)
    return obtener_material(conn, material_id)


def registrar_salida(conn, material_id: int, cantidad: float, motivo: str | None = None, trabajador_id: int | None = None,
                     orden_id: int | None = None) -> dict:
    if cantidad <= 0:
        raise ErrorNegocio("La cantidad de una salida debe ser positiva")
    with transaction(conn):
        _aplicar_movimiento(conn, material_id, "salida", -cantidad, motivo, trabajador_id, orden_id)
    return obtener_material(conn, material_id)


def ajustar_inventario(conn, material_id: int, stock_real: float, trabajador_id: int | None = None) -> dict:
    """Recuento físico: deja el stock en lo contado y registra la diferencia."""
    if stock_real < 0:
        raise ErrorNegocio("El stock contado no puede ser negativo")
    m = obtener_material(conn, material_id)
    delta = stock_real - m["stock"]
    if delta:
        with transaction(conn):
            _aplicar_movimiento(conn, material_id, "ajuste", delta, "Recuento de inventario", trabajador_id)
    return obtener_material(conn, material_id)


def historial(conn, material_id: int | None = None, limite: int = 100) -> list[dict]:
    sql = """
        SELECT mv.*, m.codigo, m.nombre AS material, t.nombre AS trabajador
        FROM movimientos mv
        JOIN materiales m ON m.id = mv.material_id
        LEFT JOIN trabajadores t ON t.id = mv.trabajador_id
    """
    params: list = []
    if material_id:
        sql += " WHERE mv.material_id = ?"
        params.append(material_id)
    sql += " ORDER BY mv.id DESC LIMIT ?"
    params.append(limite)
    return rows(conn.execute(sql, params))
