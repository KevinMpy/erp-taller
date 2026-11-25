"""Producción: modelos de sofá/sillón, escandallos y órdenes que pasan por los talleres.

Dos tipos de orden:
  * stock:   pendiente -> cinchado -> corte -> costura -> tapizado -> montaje -> terminada
             Al terminar, las unidades se suman al stock de producto terminado.
  * cliente: pendiente -> ... -> terminada -> entregada
             Si hay producto terminado en stock, el pedido puede servirse directamente
             desde stock (nace en 'terminada', sin pasar por los talleres).

Al pasar a 'cinchado' (primer taller) se descuentan del almacén todos los materiales del escandallo
(en una sola transacción: o salen todos, o ninguno). Cada cambio de estado avisa
al departamento que recibe el trabajo.
"""
from __future__ import annotations

import sqlite3

from ..db import row, rows, transaction
from ..errores import ErrorNegocio, NoEncontrado
from . import almacen, imagenes, mensajes

FLUJO = ("pendiente", "cinchado", "corte", "costura", "tapizado", "montaje", "terminada", "entregada")
DEPTO_DE_ESTADO = {
    "cinchado": "cinchado",  # bastidores, rafia y cinchas: arcones, brazos y respaldos
    "corte": "corte",
    "costura": "costura",
    "tapizado": "tapizado",
    "montaje": "montaje",
    "terminada": "almacen",   # producto terminado: lo recoge almacén/expedición
    "entregada": "oficina",
}


# ---------- Productos y escandallos ----------

FICHA_CAMPOS = ("forma", "ancho_cm", "fondo_cm", "alto_cm", "alto_asiento_cm", "descripcion", "imagen")
PREPARADO_CAMPOS = ("pieza", "tipo", "material_id", "descripcion", "medidas", "cantidad", "notas", "imagen")


def crear_producto(conn, codigo: str, nombre: str, escandallo: dict[int, float] | None = None,
                   ficha: dict | None = None, preparado: list[dict] | None = None) -> dict:
    codigo, nombre = (codigo or "").strip().upper(), (nombre or "").strip()
    if not codigo or not nombre:
        raise ErrorNegocio("El modelo necesita código y nombre")
    with transaction(conn):
        try:
            cur = conn.execute("INSERT INTO productos (codigo, nombre) VALUES (?, ?)", (codigo, nombre))
        except sqlite3.IntegrityError as e:
            raise ErrorNegocio(f"Ya existe un modelo con código {codigo!r}") from e
        pid = cur.lastrowid
        for material_id, cantidad in (escandallo or {}).items():
            almacen.obtener_material(conn, material_id)
            conn.execute(
                "INSERT INTO escandallos (producto_id, material_id, cantidad) VALUES (?,?,?)",
                (pid, material_id, cantidad),
            )
        _escribir_ficha(conn, pid, ficha or {}, preparado or [])
    return obtener_producto(conn, pid)


def _escribir_ficha(conn, producto_id: int, ficha: dict, preparado: list[dict]) -> None:
    """Sin commit. Sustituye la ficha técnica y todas las líneas del preparado."""
    ficha = {**ficha, "imagen": imagenes.validar(ficha.get("imagen"))}
    valores = [ficha.get(c) for c in FICHA_CAMPOS]
    conn.execute(f"UPDATE productos SET {', '.join(f'{c} = ?' for c in FICHA_CAMPOS)} WHERE id = ?",
                 (*valores, producto_id))
    conn.execute("DELETE FROM preparado WHERE producto_id = ?", (producto_id,))
    for posicion, linea in enumerate(preparado):
        if not (linea.get("pieza") or "").strip():
            raise ErrorNegocio(f"La línea {posicion + 1} del preparado no indica la pieza (asiento, respaldo...)")
        if linea.get("material_id"):
            almacen.obtener_material(conn, linea["material_id"])
        imagenes.validar(linea.get("imagen"))
        conn.execute(
            f"INSERT INTO preparado (producto_id, posicion, {', '.join(PREPARADO_CAMPOS)}) "
            f"VALUES (?, ?, {', '.join('?' * len(PREPARADO_CAMPOS))})",
            (producto_id, posicion, *[linea.get(c) or None for c in PREPARADO_CAMPOS]),
        )


def guardar_ficha(conn, producto_id: int, nombre: str | None, ficha: dict, preparado: list[dict]) -> dict:
    obtener_producto(conn, producto_id)
    with transaction(conn):
        if nombre and nombre.strip():
            conn.execute("UPDATE productos SET nombre = ? WHERE id = ?", (nombre.strip(), producto_id))
        _escribir_ficha(conn, producto_id, ficha, preparado)
    return obtener_producto(conn, producto_id)


def obtener_producto(conn, producto_id: int) -> dict:
    p = row(conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)))
    if not p:
        raise NoEncontrado(f"Modelo {producto_id} no existe")
    p["escandallo"] = rows(conn.execute(
        """SELECT e.material_id, m.codigo, m.nombre, e.cantidad, m.unidad
           FROM escandallos e JOIN materiales m ON m.id = e.material_id
           WHERE e.producto_id = ? ORDER BY m.nombre""",
        (producto_id,),
    ))
    p["preparado"] = rows(conn.execute(
        """SELECT p.id, p.pieza, p.tipo, p.material_id, m.codigo AS material_codigo, m.nombre AS material,
                  m.unidad, p.descripcion, p.medidas, p.cantidad, p.notas, p.imagen
           FROM preparado p LEFT JOIN materiales m ON m.id = p.material_id
           WHERE p.producto_id = ? ORDER BY p.posicion""",
        (producto_id,),
    ))
    return p


def listar_productos(conn) -> list[dict]:
    return rows(conn.execute("SELECT * FROM productos ORDER BY nombre"))


def materiales_necesarios(conn, producto_id: int, cantidad: int) -> list[dict]:
    """Necesidades vs. stock actual. Útil antes de aceptar un pedido."""
    obtener_producto(conn, producto_id)
    res = rows(conn.execute(
        """SELECT m.id AS material_id, m.codigo, m.nombre, m.unidad, m.stock,
                  e.cantidad * ? AS necesario
           FROM escandallos e JOIN materiales m ON m.id = e.material_id
           WHERE e.producto_id = ? ORDER BY m.nombre""",
        (cantidad, producto_id),
    ))
    for r in res:
        r["falta"] = max(0.0, r["necesario"] - r["stock"])
    return res


# ---------- Órdenes de producción ----------

def flujo(tipo: str) -> tuple[str, ...]:
    return FLUJO if tipo == "cliente" else FLUJO[:-1]


def crear_orden(conn, producto_id: int, cantidad: int, cliente: str | None = None, notas: str | None = None,
                desde_stock: bool = False) -> dict:
    """Orden para stock (sin cliente) o pedido de cliente (con cliente)."""
    producto = obtener_producto(conn, producto_id)
    cliente = (cliente or "").strip() or None
    tipo = "cliente" if cliente else "stock"

    if desde_stock:
        if tipo != "cliente":
            raise ErrorNegocio("Solo un pedido de cliente puede servirse desde stock")
        if producto["stock"] < cantidad:
            raise ErrorNegocio(
                f"Solo hay {producto['stock']} x {producto['nombre']} terminados; el pedido es de {cantidad}",
                {"disponible": producto["stock"], "solicitado": cantidad},
            )
    elif not producto["escandallo"]:
        raise ErrorNegocio(f"El producto {producto['nombre']} no tiene escandallo definido")

    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO ordenes (producto_id, cantidad, tipo, cliente, notas, estado) VALUES (?,?,?,?,?,?)",
            (producto_id, cantidad, tipo, cliente, notas, "terminada" if desde_stock else "pendiente"),
        )
        oid = cur.lastrowid
        if desde_stock:
            conn.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (cantidad, producto_id))
            texto = (f"Pedido #{oid} de {cliente}: {cantidad} x {producto['nombre']} servido desde stock. "
                     f"Preparar para entrega.")
        else:
            destino = f"pedido de {cliente}" if cliente else "stock"
            texto = f"Nueva orden #{oid} ({destino}): {cantidad} x {producto['nombre']}. Preparar material."
        mensajes.aviso_sistema(conn, orden_id=oid, departamento="almacen", texto=texto)
    return obtener_orden(conn, oid)


def obtener_orden(conn, orden_id: int) -> dict:
    o = row(conn.execute(
        """SELECT o.*, p.codigo AS producto_codigo, p.nombre AS producto
           FROM ordenes o JOIN productos p ON p.id = o.producto_id WHERE o.id = ?""",
        (orden_id,),
    ))
    if not o:
        raise NoEncontrado(f"Orden {orden_id} no existe")
    return _con_siguiente(o)


def _con_siguiente(o: dict) -> dict:
    pasos = flujo(o["tipo"])
    idx = pasos.index(o["estado"])
    o["siguiente"] = pasos[idx + 1] if idx + 1 < len(pasos) else None
    return o


def listar_ordenes(conn, estado: str | None = None, tipo: str | None = None, abiertas: bool = False) -> list[dict]:
    sql = """SELECT o.*, p.nombre AS producto FROM ordenes o JOIN productos p ON p.id = o.producto_id WHERE 1=1"""
    params: list = []
    if estado:
        sql += " AND o.estado = ?"
        params.append(estado)
    if tipo:
        sql += " AND o.tipo = ?"
        params.append(tipo)
    if abiertas:
        sql += " AND NOT (o.estado = 'entregada' OR (o.tipo = 'stock' AND o.estado = 'terminada'))"
    sql += " ORDER BY o.id DESC"
    return [_con_siguiente(o) for o in rows(conn.execute(sql, params))]


def avanzar_orden(conn, orden_id: int, trabajador_id: int | None = None) -> dict:
    """Pasa la orden al siguiente puesto del taller."""
    orden = obtener_orden(conn, orden_id)
    nuevo = orden["siguiente"]
    if nuevo is None:
        fin = "entregada" if orden["tipo"] == "cliente" else "terminada y en stock"
        raise ErrorNegocio(f"La orden #{orden_id} ya está {fin}")

    with transaction(conn):
        if orden["estado"] == "pendiente":  # primer paso de taller: sale el material
            faltantes = [m for m in materiales_necesarios(conn, orden["producto_id"], orden["cantidad"]) if m["falta"] > 0]
            if faltantes:
                raise ErrorNegocio(
                    f"No se puede iniciar la orden #{orden_id}: falta material",
                    [{"codigo": f["codigo"], "nombre": f["nombre"], "falta": f["falta"], "unidad": f["unidad"]}
                     for f in faltantes],
                )
            for m in materiales_necesarios(conn, orden["producto_id"], orden["cantidad"]):
                almacen._aplicar_movimiento(
                    conn, m["material_id"], "salida", -m["necesario"],
                    f"Consumo orden #{orden_id}", trabajador_id, orden_id,
                )
        conn.execute(
            "UPDATE ordenes SET estado = ?, fecha_estado = datetime('now', 'localtime') WHERE id = ?",
            (nuevo, orden_id),
        )
        texto = f"Orden #{orden_id} ({orden['cantidad']} x {orden['producto']}) pasa a {nuevo.upper()}."
        if nuevo == "terminada" and orden["tipo"] == "stock":
            conn.execute("UPDATE productos SET stock = stock + ? WHERE id = ?",
                         (orden["cantidad"], orden["producto_id"]))
            texto = (f"Orden #{orden_id} terminada: entran {orden['cantidad']} x {orden['producto']} "
                     f"en el almacén de producto terminado.")
        elif nuevo == "terminada":
            texto += f" Pedido de {orden['cliente']} listo para entregar."
        mensajes.aviso_sistema(conn, orden_id=orden_id, departamento=DEPTO_DE_ESTADO[nuevo], texto=texto)
    return obtener_orden(conn, orden_id)
