from __future__ import annotations

from ..db import DEPARTAMENTOS, row, rows, transaction
from ..errores import ErrorNegocio, NoEncontrado


def crear(conn, nombre: str, departamento: str) -> dict:
    if departamento not in DEPARTAMENTOS:
        raise ErrorNegocio(f"Departamento desconocido: {departamento}. Válidos: {', '.join(DEPARTAMENTOS)}")
    with transaction(conn):
        cur = conn.execute("INSERT INTO trabajadores (nombre, departamento) VALUES (?, ?)", (nombre, departamento))
    return obtener(conn, cur.lastrowid)


def obtener(conn, trabajador_id: int) -> dict:
    t = row(conn.execute("SELECT * FROM trabajadores WHERE id = ?", (trabajador_id,)))
    if not t:
        raise NoEncontrado(f"Trabajador {trabajador_id} no existe")
    return t


def listar(conn, departamento: str | None = None) -> list[dict]:
    if departamento:
        return rows(conn.execute(
            "SELECT * FROM trabajadores WHERE activo = 1 AND departamento = ? ORDER BY nombre", (departamento,)))
    return rows(conn.execute("SELECT * FROM trabajadores WHERE activo = 1 ORDER BY departamento, nombre"))
