"""API REST del ERP. Documentación interactiva en /docs al arrancar el servidor."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import db
from .errores import ErrorNegocio, NoEncontrado
from .servicios import almacen, compras, imagenes, mensajes, produccion, trabajadores

WEB_DIR = Path(__file__).parent / "web"

Departamento = Literal["almacen", "cinchado", "corte", "costura", "tapizado", "montaje", "oficina"]
Prioridad = Literal["baja", "normal", "urgente"]


# ---------- Esquemas de entrada ----------

class TrabajadorIn(BaseModel):
    nombre: str = Field(min_length=1)
    departamento: Departamento


class MaterialIn(BaseModel):
    codigo: str = Field(min_length=1, examples=["TEL-TERC-GRIS"])
    nombre: str = Field(min_length=1, examples=["Terciopelo gris perla"])
    categoria: str = Field(examples=["tela"])
    unidad: str = Field(examples=["m"])
    stock: float = Field(0, ge=0)
    stock_minimo: float = Field(0, ge=0)
    ubicacion: str | None = None
    proveedor: str | None = None
    producto_id: int | None = Field(None, description="Opcional: modelo al que pertenece la pieza")


class LineaCompra(BaseModel):
    material_id: int
    cantidad: float = Field(gt=0)


class PedidoCompraIn(BaseModel):
    proveedor: str = Field(min_length=1)
    lineas: list[LineaCompra] = Field(min_length=1)
    notas: str | None = None
    trabajador_id: int | None = None


class LineaRecibida(BaseModel):
    material_id: int
    cantidad: float = Field(ge=0)


class RecepcionIn(BaseModel):
    trabajador_id: int | None = None
    lineas: list[LineaRecibida] | None = Field(None, description="Vacío = ha llegado todo lo pedido")


class CategoriaIn(BaseModel):
    nombre: str = Field(min_length=1, examples=["reposapiés"])
    grupo: Literal["material", "pieza"]


class MovimientoIn(BaseModel):
    cantidad: float = Field(gt=0)
    motivo: str | None = None
    trabajador_id: int | None = None


class RecuentoIn(BaseModel):
    stock_real: float = Field(ge=0)
    trabajador_id: int | None = None


class LineaEscandallo(BaseModel):
    material_id: int
    cantidad: float = Field(gt=0)


class LineaPreparado(BaseModel):
    pieza: str = Field(examples=["Asiento"])
    tipo: str | None = Field(None, examples=["Goma"])
    material_id: int | None = Field(None, description="Opcional: material del almacén")
    descripcion: str | None = Field(None, examples=["HR35, densidad 35"])
    medidas: str | None = Field(None, examples=["60 x 55 x 10 cm"])
    cantidad: float | None = Field(None, ge=0)
    notas: str | None = None
    imagen: str | None = Field(None, description="Foto de la pieza (nombre devuelto por POST /imagenes)")


class FichaModelo(BaseModel):
    forma: str | None = Field(None, examples=["Chaise longue"])
    ancho_cm: float | None = Field(None, ge=0)
    fondo_cm: float | None = Field(None, ge=0)
    alto_cm: float | None = Field(None, ge=0)
    alto_asiento_cm: float | None = Field(None, ge=0)
    descripcion: str | None = None
    imagen: str | None = Field(None, description="Foto del sofá completo (nombre devuelto por POST /imagenes)")
    preparado: list[LineaPreparado] = []


class ImagenIn(BaseModel):
    data: str = Field(description="data URL: 'data:image/jpeg;base64,...'")


class ProductoIn(FichaModelo):
    codigo: str
    nombre: str
    escandallo: list[LineaEscandallo] = []


class FichaIn(FichaModelo):
    nombre: str | None = None


class OrdenIn(BaseModel):
    producto_id: int
    cantidad: int = Field(gt=0)
    cliente: str | None = Field(None, description="Vacío = fabricación para stock")
    notas: str | None = None
    desde_stock: bool = Field(False, description="Servir el pedido con producto ya terminado")


class AvanceIn(BaseModel):
    trabajador_id: int | None = None


class MensajeIn(BaseModel):
    autor_id: int
    texto: str = Field(min_length=1)
    departamento: Departamento | None = None
    destinatario_id: int | None = None
    orden_id: int | None = None
    prioridad: Prioridad = "normal"


# ---------- Aplicación ----------

def create_app(db_path: str | None = None, imagenes_dir: str | None = None) -> FastAPI:
    ruta = db_path or db.DB_PATH
    carpeta_imagenes = Path(imagenes_dir or os.environ.get("ERP_IMAGENES_DIR")
                            or Path(ruta).resolve().parent / "imagenes")
    with db.connect(ruta) as c:
        db.init_db(c)

    app = FastAPI(title="ERP Taller", version="0.1.0",
                  description="Almacén, producción y comunicación interna del taller.")

    def get_conn() -> Iterator:
        conn = db.connect(ruta)
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(ErrorNegocio)
    async def _error_negocio(_: Request, exc: ErrorNegocio):
        status = 404 if isinstance(exc, NoEncontrado) else 409
        return JSONResponse(status_code=status, content={"error": exc.mensaje, "detalle": exc.detalle})

    Conn = Depends(get_conn)

    # --- Trabajadores ---
    @app.post("/trabajadores", status_code=201, tags=["Trabajadores"])
    def alta_trabajador(body: TrabajadorIn, conn=Conn):
        return trabajadores.crear(conn, body.nombre, body.departamento)

    @app.get("/trabajadores", tags=["Trabajadores"])
    def lista_trabajadores(departamento: Departamento | None = None, conn=Conn):
        return trabajadores.listar(conn, departamento)

    # --- Almacén ---
    @app.post("/materiales", status_code=201, tags=["Almacén"])
    def alta_material(body: MaterialIn, conn=Conn):
        return almacen.crear_material(conn, **body.model_dump())

    @app.get("/materiales", tags=["Almacén"])
    def lista_materiales(categoria: str | None = None, bajo_minimo: bool = False,
                         grupo: Literal["material", "pieza"] | None = None, producto_id: int | None = None,
                         q: str | None = None, conn=Conn):
        return almacen.listar_materiales(conn, categoria, bajo_minimo, grupo, producto_id, q)

    # --- Imágenes (fotos de modelos y piezas) ---
    @app.post("/imagenes", status_code=201, tags=["Modelos de sofás"], summary="Subir una foto")
    def subir_imagen(body: ImagenIn):
        nombre = imagenes.guardar(carpeta_imagenes, body.data)
        return {"nombre": nombre, "url": f"/imagenes/{nombre}"}

    @app.get("/imagenes/{nombre}", tags=["Modelos de sofás"])
    def ver_imagen(nombre: str):
        return FileResponse(imagenes.ruta(carpeta_imagenes, nombre),
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # --- Compras (oficina) ---
    @app.get("/compras/sugerencias", tags=["Compras"], summary="Qué pedir, agrupado por proveedor")
    def sugerencias_compra(conn=Conn):
        return compras.sugerencias(conn)

    @app.get("/compras/proveedores", tags=["Compras"])
    def proveedores(conn=Conn):
        return compras.proveedores(conn)

    @app.get("/compras/pedidos", tags=["Compras"])
    def lista_pedidos_compra(estado: Literal["abiertos", "borrador", "enviado", "recibido", "cancelado"] | None = None,
                             conn=Conn):
        return compras.listar_pedidos(conn, estado)

    @app.post("/compras/pedidos", status_code=201, tags=["Compras"])
    def alta_pedido_compra(body: PedidoCompraIn, conn=Conn):
        return compras.crear_pedido(conn, body.proveedor, [l.model_dump() for l in body.lineas],
                                    body.notas, body.trabajador_id)

    @app.get("/compras/pedidos/{pedido_id}", tags=["Compras"])
    def ver_pedido_compra(pedido_id: int, conn=Conn):
        return compras.obtener_pedido(conn, pedido_id)

    @app.post("/compras/pedidos/{pedido_id}/enviar", tags=["Compras"], summary="Marcar como enviado al proveedor")
    def enviar_pedido_compra(pedido_id: int, conn=Conn):
        return compras.marcar_enviado(conn, pedido_id)

    @app.post("/compras/pedidos/{pedido_id}/recibir", tags=["Compras"], summary="Ha llegado: dar entrada en almacén")
    def recibir_pedido_compra(pedido_id: int, body: RecepcionIn | None = None, conn=Conn):
        recibido = {l.material_id: l.cantidad for l in body.lineas} if body and body.lineas is not None else None
        return compras.recibir(conn, pedido_id, recibido, body.trabajador_id if body else None)

    @app.post("/compras/pedidos/{pedido_id}/cancelar", tags=["Compras"])
    def cancelar_pedido_compra(pedido_id: int, conn=Conn):
        return compras.cancelar(conn, pedido_id)

    @app.get("/categorias", tags=["Almacén"], summary="Categorías del stock general con recuentos")
    def lista_categorias(conn=Conn):
        return almacen.listar_categorias(conn)

    @app.post("/categorias", status_code=201, tags=["Almacén"])
    def alta_categoria(body: CategoriaIn, conn=Conn):
        return almacen.crear_categoria(conn, body.nombre, body.grupo)

    @app.get("/materiales/codigo/{codigo}", tags=["Almacén"], summary="Buscar por código (escáner)")
    def material_por_codigo(codigo: str, conn=Conn):
        return almacen.buscar_por_codigo(conn, codigo)

    @app.get("/materiales/{material_id}", tags=["Almacén"])
    def ver_material(material_id: int, conn=Conn):
        return almacen.obtener_material(conn, material_id)

    @app.post("/materiales/{material_id}/entrada", tags=["Almacén"])
    def entrada(material_id: int, body: MovimientoIn, conn=Conn):
        return almacen.registrar_entrada(conn, material_id, body.cantidad, body.motivo, body.trabajador_id)

    @app.post("/materiales/{material_id}/salida", tags=["Almacén"])
    def salida(material_id: int, body: MovimientoIn, conn=Conn):
        return almacen.registrar_salida(conn, material_id, body.cantidad, body.motivo, body.trabajador_id)

    @app.post("/materiales/{material_id}/recuento", tags=["Almacén"])
    def recuento(material_id: int, body: RecuentoIn, conn=Conn):
        return almacen.ajustar_inventario(conn, material_id, body.stock_real, body.trabajador_id)

    @app.get("/movimientos", tags=["Almacén"])
    def movimientos(material_id: int | None = None, limite: int = 100, conn=Conn):
        return almacen.historial(conn, material_id, limite)

    # --- Producción ---
    @app.post("/productos", status_code=201, tags=["Producción"])
    def alta_producto(body: ProductoIn, conn=Conn):
        return produccion.crear_producto(
            conn, body.codigo, body.nombre, {l.material_id: l.cantidad for l in body.escandallo},
            ficha=body.model_dump(include=set(produccion.FICHA_CAMPOS)),
            preparado=[l.model_dump() for l in body.preparado])

    @app.put("/productos/{producto_id}/ficha", tags=["Modelos de sofás"],
             summary="Guardar ficha técnica y preparado (goma, guata, medidas, forma...)")
    def guardar_ficha(producto_id: int, body: FichaIn, conn=Conn):
        return produccion.guardar_ficha(
            conn, producto_id, body.nombre,
            body.model_dump(include=set(produccion.FICHA_CAMPOS)),
            [l.model_dump() for l in body.preparado])

    @app.get("/productos", tags=["Producción"])
    def lista_productos(conn=Conn):
        return produccion.listar_productos(conn)

    @app.get("/productos/{producto_id}", tags=["Producción"])
    def ver_producto(producto_id: int, conn=Conn):
        return produccion.obtener_producto(conn, producto_id)

    @app.get("/productos/{producto_id}/necesidades", tags=["Producción"],
             summary="¿Hay material para fabricar N unidades?")
    def necesidades(producto_id: int, cantidad: int = 1, conn=Conn):
        return produccion.materiales_necesarios(conn, producto_id, cantidad)

    @app.post("/ordenes", status_code=201, tags=["Producción"])
    def alta_orden(body: OrdenIn, conn=Conn):
        return produccion.crear_orden(conn, body.producto_id, body.cantidad, body.cliente, body.notas,
                                      body.desde_stock)

    @app.get("/ordenes", tags=["Producción"])
    def lista_ordenes(estado: str | None = None, tipo: Literal["stock", "cliente"] | None = None,
                      abiertas: bool = False, conn=Conn):
        return produccion.listar_ordenes(conn, estado, tipo, abiertas)

    @app.get("/ordenes/{orden_id}", tags=["Producción"])
    def ver_orden(orden_id: int, conn=Conn):
        return produccion.obtener_orden(conn, orden_id)

    @app.post("/ordenes/{orden_id}/avanzar", tags=["Producción"], summary="Pasar al siguiente puesto")
    def avanzar(orden_id: int, body: AvanceIn | None = None, conn=Conn):
        return produccion.avanzar_orden(conn, orden_id, body.trabajador_id if body else None)

    # --- Mensajes ---
    @app.post("/mensajes", status_code=201, tags=["Comunicación"])
    def enviar_mensaje(body: MensajeIn, conn=Conn):
        trabajadores.obtener(conn, body.autor_id)
        if body.destinatario_id:
            trabajadores.obtener(conn, body.destinatario_id)
        return mensajes.enviar(conn, body.autor_id, body.texto, body.departamento, body.destinatario_id,
                               body.orden_id, body.prioridad)

    @app.get("/trabajadores/{trabajador_id}/bandeja", tags=["Comunicación"])
    def bandeja(trabajador_id: int, no_leidos: bool = False, conn=Conn):
        return mensajes.bandeja(conn, trabajador_id, no_leidos)

    @app.post("/mensajes/{mensaje_id}/leido/{trabajador_id}", status_code=204, tags=["Comunicación"])
    def leido(mensaje_id: int, trabajador_id: int, conn=Conn):
        mensajes.marcar_leido(conn, mensaje_id, trabajador_id)

    @app.get("/", include_in_schema=False)
    def inicio():
        return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
