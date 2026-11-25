"""Carga datos de ejemplo de un taller de tapizado.  Uso:  python -m erp.datos_demo"""
from . import db
from .servicios import almacen, produccion, trabajadores


def cargar(conn) -> None:
    if conn.execute("SELECT COUNT(*) FROM materiales").fetchone()[0]:
        print("La base de datos ya tiene datos; no se carga la demo.")
        return

    for nombre, depto in [("Lucía", "almacen"), ("Andrés", "corte"), ("Marta", "costura"),
                          ("Javier", "tapizado"), ("Rosa", "montaje"), ("Carmen", "oficina"),
                          ("Jose", "cinchado")]:
        trabajadores.crear(conn, nombre, depto)

    M = {}
    for datos in [
        dict(codigo="TEL-TERC-GRIS", nombre="Terciopelo gris perla", categoria="tela", unidad="m",
             stock=120, stock_minimo=40, ubicacion="Rollos - A1", proveedor="Textiles Ejemplo"),
        dict(codigo="TEL-LINO-CRUDO", nombre="Lino crudo", categoria="tela", unidad="m",
             stock=60, stock_minimo=30, ubicacion="Rollos - A2", proveedor="Textiles Ejemplo"),
        dict(codigo="ESP-HR35-10", nombre="Goma HR35 10 cm", categoria="goma", unidad="ud",
             stock=25, stock_minimo=10, ubicacion="Nave 2", proveedor="Espumas Ejemplo"),
        dict(codigo="GOMA-D25-5", nombre="Goma blanda D25 5 cm", categoria="goma", unidad="ud",
             stock=6, stock_minimo=8, ubicacion="Nave 2", proveedor="Espumas Ejemplo"),
        dict(codigo="GUAT-200", nombre="Guata 200 g", categoria="guata", unidad="m",
             stock=200, stock_minimo=50, ubicacion="Nave 2", proveedor="Espumas Ejemplo"),
        # Cinchado: aquí se hacen los bastidores y se les pone la rafia
        dict(codigo="LIST-PINO", nombre="Listón pino 45x25 mm", categoria="madera", unidad="m",
             stock=100, stock_minimo=40, ubicacion="Cinchado", proveedor="Maderas Ejemplo"),
        dict(codigo="RAFIA", nombre="Rafia tejida", categoria="rafia", unidad="m",
             stock=150, stock_minimo=50, ubicacion="Cinchado", proveedor="Fornituras Ejemplo"),
        dict(codigo="GRAPA-80", nombre="Grapa 80/10 (caja 10.000)", categoria="fornitura", unidad="ud",
             stock=12, stock_minimo=4, ubicacion="Cinchado", proveedor="Fornituras Ejemplo"),
        dict(codigo="CINCHA-50", nombre="Cincha elástica 50 mm", categoria="cincha", unidad="m",
             stock=300, stock_minimo=100, ubicacion="Estante C3", proveedor="Fornituras Ejemplo"),
        dict(codigo="PATA-ROBLE", nombre="Pata roble 15 cm", categoria="pata", unidad="ud",
             stock=40, stock_minimo=16, ubicacion="Estante C1", proveedor="Maderas Ejemplo"),
    ]:
        M[datos["codigo"]] = almacen.crear_material(conn, **datos)["id"]

    produccion.crear_producto(conn, "SOF-NORD-3P", "Sofá Nórdico 3 plazas", {
        M["TEL-TERC-GRIS"]: 14, M["ESP-HR35-10"]: 3, M["GUAT-200"]: 10,
        M["LIST-PINO"]: 12, M["RAFIA"]: 6, M["GRAPA-80"]: 0.1,
        M["CINCHA-50"]: 18, M["PATA-ROBLE"]: 4,
    }, ficha=dict(forma="Recto", ancho_cm=210, fondo_cm=92, alto_cm=85, alto_asiento_cm=45,
                  descripcion="Brazo recto, respaldo fijo, cojines de asiento desenfundables."),
    preparado=[
        dict(pieza="Asiento", tipo="Goma", material_id=M["ESP-HR35-10"], descripcion="HR35, densidad 35",
             medidas="62 x 58 x 10 cm", cantidad=3, notas="Canto redondeado"),
        dict(pieza="Asiento", tipo="Guata", material_id=M["GUAT-200"], descripcion="Guata 200 g, una capa",
             medidas="64 x 130 cm", cantidad=3),
        dict(pieza="Respaldo", tipo="Goma", descripcion="Goma blanda D25 (pedir)", medidas="190 x 45 x 5 cm"),
        dict(pieza="Brazo", tipo="Guata", material_id=M["GUAT-200"], descripcion="Guata 200 g doble",
             medidas="90 x 60 cm", cantidad=2),
    ])

    # Piezas fabricadas en el taller (stock general -> grupo "pieza")
    nordico = conn.execute("SELECT id FROM productos WHERE codigo = 'SOF-NORD-3P'").fetchone()[0]
    for datos in [
        dict(codigo="ESQ-NORD-3P", nombre="Esqueleto Nórdico 3 plazas (cinchado)", categoria="esqueleto",
             unidad="ud", stock=3, stock_minimo=2, ubicacion="Cinchado", producto_id=nordico),
        dict(codigo="BRZ-NORD", nombre="Brazo Nórdico", categoria="brazo", unidad="ud",
             stock=6, stock_minimo=4, ubicacion="Tapizado", producto_id=nordico),
        dict(codigo="RESP-NORD", nombre="Respaldo Nórdico 3 plazas", categoria="respaldo", unidad="ud",
             stock=1, stock_minimo=2, ubicacion="Tapizado", producto_id=nordico),
        dict(codigo="CAB-RECL", nombre="Cabezal reclinable con herraje", categoria="cabezal", unidad="ud",
             stock=10, stock_minimo=6, ubicacion="Estante D2"),
        dict(codigo="ARC-ABAT-140", nombre="Arcón abatible 140 cm", categoria="arcón", unidad="ud",
             stock=4, stock_minimo=2, ubicacion="Nave 3"),
    ]:
        almacen.crear_material(conn, **datos)
    print("Datos de demo cargados: 7 trabajadores, 10 materiales, 5 piezas, 1 modelo.")


if __name__ == "__main__":
    with db.connect() as conn:
        db.init_db(conn)
        cargar(conn)
