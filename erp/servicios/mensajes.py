"""Comunicación interna: mensajes a una persona, a un departamento o a toda la fábrica.

El propio ERP también escribe aquí (autor NULL = "Sistema") para avisar de stock bajo
o de que una orden llega al siguiente puesto.
"""
from __future__ import annotations

import sqlite3

from ..db import DEPARTAMENTOS, row, rows, transaction
from ..errores import ErrorNegocio, NoEncontrado


def _insertar(conn, autor_id, departamento, destinatario_id, orden_id, prioridad, texto) -> int:
    if departamento and departamento not in DEPARTAMENTOS:
        raise ErrorNegocio(f"Departamento desconocido: {departamento}")
    if not texto or not texto.strip():
        raise ErrorNegocio("El mensaje está vacío")
    cur = conn.execute(
        """INSERT INTO mensajes (autor_id, departamento_destino, destinatario_id, orden_id, prioridad, texto)
           VALUES (?,?,?,?,?,?)""",
        (autor_id, departamento, destinatario_id, orden_id, prioridad, texto.strip()),
    )
    return cur.lastrowid


def aviso_sistema(conn, texto: str, departamento: str | None = None, prioridad: str = "normal",
                  orden_id: int | None = None) -> int:
    """Sin commit: se ejecuta dentro de la transacción de quien lo llama."""
    return _insertar(conn, None, departamento, None, orden_id, prioridad, texto)


def enviar(conn, autor_id: int, texto: str, departamento: str | None = None, destinatario_id: int | None = None,
           orden_id: int | None = None, prioridad: str = "normal") -> dict:
    with transaction(conn):
        mid = _insertar(conn, autor_id, departamento, destinatario_id, orden_id, prioridad, texto)
    return obtener(conn, mid)


def obtener(conn, mensaje_id: int) -> dict:
    m = row(conn.execute(
        """SELECT m.*, COALESCE(t.nombre, 'Sistema') AS autor
           FROM mensajes m LEFT JOIN trabajadores t ON t.id = m.autor_id WHERE m.id = ?""",
        (mensaje_id,),
    ))
    if not m:
        raise NoEncontrado(f"Mensaje {mensaje_id} no existe")
    return m


def bandeja(conn: sqlite3.Connection, trabajador_id: int, solo_no_leidos: bool = False, limite: int = 50) -> list[dict]:
    """Mensajes que ve un trabajador: los suyos, los de su departamento y los generales."""
    t = row(conn.execute("SELECT * FROM trabajadores WHERE id = ?", (trabajador_id,)))
    if not t:
        raise NoEncontrado(f"Trabajador {trabajador_id} no existe")
    sql = """
        SELECT m.*, COALESCE(a.nombre, 'Sistema') AS autor,
               (l.mensaje_id IS NOT NULL) AS leido
        FROM mensajes m
        LEFT JOIN trabajadores a ON a.id = m.autor_id
        LEFT JOIN mensajes_leidos l ON l.mensaje_id = m.id AND l.trabajador_id = :tid
        WHERE (m.destinatario_id = :tid
               OR m.departamento_destino = :depto
               OR (m.destinatario_id IS NULL AND m.departamento_destino IS NULL))
    """
    if solo_no_leidos:
        sql += " AND l.mensaje_id IS NULL"
    sql += """ ORDER BY (m.prioridad = 'urgente') DESC, m.id DESC LIMIT :limite"""
    resultado = rows(conn.execute(sql, {"tid": trabajador_id, "depto": t["departamento"], "limite": limite}))
    for m in resultado:
        m["leido"] = bool(m["leido"])
    return resultado


def marcar_leido(conn, mensaje_id: int, trabajador_id: int) -> None:
    obtener(conn, mensaje_id)
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO mensajes_leidos (mensaje_id, trabajador_id) VALUES (?, ?)",
            (mensaje_id, trabajador_id),
        )
