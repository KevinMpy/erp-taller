"""Pedidos a proveedores (oficina).

Ciclo de un pedido:  borrador -> enviado -> recibido   (o cancelado antes de recibirse)

* Sugerencias: materiales bajo mínimo agrupados por proveedor, con la cantidad que falta para
  llegar al stock objetivo (2 x mínimo), descontando lo que ya está pedido y sin recibir.
* Al recibir un pedido se da entrada en el almacén a lo que ha llegado (todo o parcial) y se
  avisa a almacén.
"""
from __future__ import annotations

from ..db import row, rows, transaction
from ..errores import ErrorNegocio, NoEncontrado
from . import almacen, mensajes

FACTOR_REPOSICION = 2  # reponer hasta el doble del stock mínimo
ABIERTOS = ("borrador", "enviado")


def _pendiente_por_material(conn) -> dict[int, float]:
    """Cantidad ya pedida (borrador o enviado) y aún no recibida, por material."""
    return {r["material_id"]: r["pendiente"] for r in rows(conn.execute(
        f"""SELECT l.material_id, SUM(l.cantidad) AS pendiente
            FROM lineas_compra l JOIN pedidos_compra p ON p.id = l.pedido_id
            WHERE p.estado IN ({','.join('?' * len(ABIERTOS))}) GROUP BY l.material_id""",
        ABIERTOS,
    ))}


def sugerencias(conn) -> list[dict]:
    """Qué habría que pedir, agrupado por proveedor."""
    pendiente = _pendiente_por_material(conn)
    grupos: dict[str, dict] = {}
    for m in almacen.listar_materiales(conn, solo_bajo_minimo=True):
        if m["grupo"] == "pieza":
            continue  # las piezas se fabrican, no se compran
        ya_pedido = pendiente.get(m["id"], 0)
        sugerida = m["stock_minimo"] * FACTOR_REPOSICION - m["stock"] - ya_pedido
        if sugerida <= 0:
            continue
        proveedor = m["proveedor"] or "Sin proveedor asignado"
        grupos.setdefault(proveedor, {"proveedor": proveedor, "lineas": []})["lineas"].append({
            "material_id": m["id"], "codigo": m["codigo"], "nombre": m["nombre"], "unidad": m["unidad"],
            "stock": m["stock"], "stock_minimo": m["stock_minimo"], "ya_pedido": ya_pedido,
            "cantidad_sugerida": round(sugerida, 2),
        })
    return sorted(grupos.values(), key=lambda g: g["proveedor"])


def proveedores(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        """SELECT proveedor FROM materiales WHERE proveedor IS NOT NULL AND proveedor <> ''
           UNION SELECT proveedor FROM pedidos_compra ORDER BY 1""")]


def crear_pedido(conn, proveedor: str, lineas: list[dict], notas: str | None = None,
                 trabajador_id: int | None = None) -> dict:
    proveedor = (proveedor or "").strip()
    if not proveedor:
        raise ErrorNegocio("Indica el proveedor")
    lineas = [l for l in lineas if l.get("cantidad")]
    if not lineas:
        raise ErrorNegocio("El pedido no tiene ninguna línea con cantidad")
    ids = [l["material_id"] for l in lineas]
    if len(ids) != len(set(ids)):
        raise ErrorNegocio("Hay un material repetido en el pedido")
    with transaction(conn):
        cur = conn.execute("INSERT INTO pedidos_compra (proveedor, notas, creado_por) VALUES (?,?,?)",
                           (proveedor, notas, trabajador_id))
        pid = cur.lastrowid
        for l in lineas:
            almacen.obtener_material(conn, l["material_id"])
            if l["cantidad"] <= 0:
                raise ErrorNegocio("Las cantidades deben ser positivas")
            conn.execute("INSERT INTO lineas_compra (pedido_id, material_id, cantidad) VALUES (?,?,?)",
                         (pid, l["material_id"], l["cantidad"]))
    return obtener_pedido(conn, pid)


def obtener_pedido(conn, pedido_id: int) -> dict:
    p = row(conn.execute(
        """SELECT p.*, t.nombre AS creado_por_nombre FROM pedidos_compra p
           LEFT JOIN trabajadores t ON t.id = p.creado_por WHERE p.id = ?""", (pedido_id,)))
    if not p:
        raise NoEncontrado(f"Pedido de compra {pedido_id} no existe")
    p["lineas"] = rows(conn.execute(
        """SELECT l.material_id, m.codigo, m.nombre, m.unidad, l.cantidad, l.cantidad_recibida
           FROM lineas_compra l JOIN materiales m ON m.id = l.material_id
           WHERE l.pedido_id = ? ORDER BY m.nombre""", (pedido_id,)))
    return p


def listar_pedidos(conn, estado: str | None = None) -> list[dict]:
    sql = "SELECT id FROM pedidos_compra"
    params: list = []
    if estado == "abiertos":
        sql += f" WHERE estado IN ({','.join('?' * len(ABIERTOS))})"
        params += ABIERTOS
    elif estado:
        sql += " WHERE estado = ?"
        params.append(estado)
    sql += " ORDER BY id DESC"
    return [obtener_pedido(conn, r[0]) for r in conn.execute(sql, params)]


def _cambiar_estado(conn, pedido_id: int, desde: tuple[str, ...], hacia: str, campo_fecha: str | None = None):
    p = obtener_pedido(conn, pedido_id)
    if p["estado"] not in desde:
        raise ErrorNegocio(f"El pedido #{pedido_id} está {p['estado']}; no se puede pasar a {hacia}")
    extra = f", {campo_fecha} = datetime('now', 'localtime')" if campo_fecha else ""
    conn.execute(f"UPDATE pedidos_compra SET estado = ?{extra} WHERE id = ?", (hacia, pedido_id))
    return p


def marcar_enviado(conn, pedido_id: int) -> dict:
    with transaction(conn):
        _cambiar_estado(conn, pedido_id, ("borrador",), "enviado", "fecha_envio")
    return obtener_pedido(conn, pedido_id)


def cancelar(conn, pedido_id: int) -> dict:
    with transaction(conn):
        _cambiar_estado(conn, pedido_id, ABIERTOS, "cancelado")
    return obtener_pedido(conn, pedido_id)


def recibir(conn, pedido_id: int, recibido: dict[int, float] | None = None,
            trabajador_id: int | None = None) -> dict:
    """Da entrada en almacén. `recibido` = {material_id: cantidad}; si no se indica, llegó todo."""
    with transaction(conn):
        p = _cambiar_estado(conn, pedido_id, ("enviado",), "recibido", "fecha_recepcion")
        resumen = []
        for l in p["lineas"]:
            cantidad = l["cantidad"] if recibido is None else recibido.get(l["material_id"], 0)
            if cantidad < 0:
                raise ErrorNegocio("La cantidad recibida no puede ser negativa")
            conn.execute("UPDATE lineas_compra SET cantidad_recibida = ? WHERE pedido_id = ? AND material_id = ?",
                         (cantidad, pedido_id, l["material_id"]))
            if cantidad:
                almacen._aplicar_movimiento(conn, l["material_id"], "entrada", cantidad,
                                            f"Pedido de compra #{pedido_id} ({p['proveedor']})", trabajador_id)
                resumen.append(f"{cantidad:g} {l['unidad']} de {l['nombre']}")
        incompleto = any((recibido or {}).get(l["material_id"], l["cantidad"]) < l["cantidad"]
                         for l in p["lineas"]) if recibido is not None else False
        mensajes.aviso_sistema(
            conn, departamento="almacen",
            texto=f"Ha llegado el pedido #{pedido_id} de {p['proveedor']}: " + (", ".join(resumen) or "nada")
                  + (". ATENCIÓN: llegó incompleto." if incompleto else "."),
            prioridad="urgente" if incompleto else "normal",
        )
    return obtener_pedido(conn, pedido_id)
