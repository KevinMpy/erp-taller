import pytest
from fastapi.testclient import TestClient

from erp import db
from erp.api import create_app
from erp.datos_demo import cargar


@pytest.fixture
def client(tmp_path):
    ruta = str(tmp_path / "test.db")
    app = create_app(ruta)
    with db.connect(ruta) as conn:
        cargar(conn)
    return TestClient(app)


def material(client, codigo):
    return client.get(f"/materiales/codigo/{codigo}").json()


def bandeja(client, trabajador_id):
    return client.get(f"/trabajadores/{trabajador_id}/bandeja").json()


def test_entrada_y_salida_actualizan_stock_y_quedan_en_historial(client):
    tela = material(client, "TEL-LINO-CRUDO")
    r = client.post(f"/materiales/{tela['id']}/entrada", json={"cantidad": 25, "trabajador_id": 1})
    assert r.json()["stock"] == 85
    r = client.post(f"/materiales/{tela['id']}/salida", json={"cantidad": 5, "motivo": "Muestrario"})
    assert r.json()["stock"] == 80
    hist = client.get("/movimientos", params={"material_id": tela["id"]}).json()
    assert [h["cantidad"] for h in hist] == [-5, 25]


def test_no_se_puede_sacar_mas_de_lo_que_hay(client):
    liston = material(client, "LIST-PINO")
    r = client.post(f"/materiales/{liston["id"]}/salida", json={"cantidad": 999})
    assert r.status_code == 409
    assert material(client, "LIST-PINO")["stock"] == 100


def test_bajar_del_minimo_avisa_al_almacen(client):
    lino = material(client, "TEL-LINO-CRUDO")  # stock 60, mínimo 30
    client.post(f"/materiales/{lino['id']}/salida", json={"cantidad": 35})
    avisos = [m for m in bandeja(client, 1) if "Stock bajo mínimo" in m["texto"]]  # Lucía, almacén
    assert len(avisos) == 1 and avisos[0]["prioridad"] == "urgente"
    # Seguir sacando estando ya bajo mínimo no repite el aviso
    client.post(f"/materiales/{lino['id']}/salida", json={"cantidad": 1})
    assert len([m for m in bandeja(client, 1) if "Stock bajo mínimo" in m["texto"]]) == 1
    # Y no le llega a costura
    assert not [m for m in bandeja(client, 3) if "Stock bajo mínimo" in m["texto"]]
    assert "TEL-LINO-CRUDO" in [m["codigo"] for m in client.get("/materiales", params={"bajo_minimo": True}).json()]


def test_recuento_ajusta_stock(client):
    patas = material(client, "PATA-ROBLE")
    r = client.post(f"/materiales/{patas['id']}/recuento", json={"stock_real": 36})
    assert r.json()["stock"] == 36
    assert client.get("/movimientos").json()[0]["tipo"] == "ajuste"


def test_orden_consume_escandallo_al_pasar_a_cinchado_y_avisa_a_cada_taller(client):
    orden = client.post("/ordenes", json={"producto_id": 1, "cantidad": 2, "cliente": "Muebles Sol"}).json()
    assert orden["estado"] == "pendiente"
    assert material(client, "TEL-TERC-GRIS")["stock"] == 120  # aún no se consume

    r = client.post(f"/ordenes/{orden['id']}/avanzar", json={"trabajador_id": 1})
    assert r.json()["estado"] == "cinchado"
    assert material(client, "TEL-TERC-GRIS")["stock"] == 120 - 28
    assert material(client, "PATA-ROBLE")["stock"] == 40 - 8
    assert material(client, "RAFIA")["stock"] == 150 - 12
    assert any("pasa a CINCHADO" in m["texto"] for m in bandeja(client, 7))  # Jose, cinchado
    assert not any("pasa a CINCHADO" in m["texto"] for m in bandeja(client, 2))  # Andrés (corte) aún no

    for esperado in ["corte", "costura", "tapizado", "montaje", "terminada", "entregada"]:
        assert client.post(f"/ordenes/{orden['id']}/avanzar").json()["estado"] == esperado
    assert client.post(f"/ordenes/{orden['id']}/avanzar").status_code == 409
    # El material solo se descontó una vez
    assert material(client, "TEL-TERC-GRIS")["stock"] == 92


def test_orden_sin_material_suficiente_no_descuenta_nada(client):
    orden = client.post("/ordenes", json={"producto_id": 1, "cantidad": 10}).json()  # listón: 10 x 12 m = 120, hay 100
    r = client.post(f"/ordenes/{orden['id']}/avanzar")
    assert r.status_code == 409
    faltan = {f["codigo"]: f["falta"] for f in r.json()["detalle"]}
    assert faltan["LIST-PINO"] == 20
    # Transacción atómica: ningún material se ha tocado
    assert material(client, "TEL-TERC-GRIS")["stock"] == 120
    assert client.get(f"/ordenes/{orden['id']}").json()["estado"] == "pendiente"


def test_mensajes_por_persona_departamento_y_general(client):
    client.post("/mensajes", json={"autor_id": 6, "texto": "Reunión a las 9", "prioridad": "baja"})
    client.post("/mensajes", json={"autor_id": 6, "texto": "Cambiar aguja máquina 2", "departamento": "costura"})
    client.post("/mensajes", json={"autor_id": 2, "texto": "Te dejo el corte", "destinatario_id": 4})

    textos_marta = {m["texto"] for m in bandeja(client, 3)}   # costura
    textos_javier = {m["texto"] for m in bandeja(client, 4)}  # tapizado
    assert {"Reunión a las 9", "Cambiar aguja máquina 2"} <= textos_marta
    assert "Te dejo el corte" not in textos_marta
    assert {"Reunión a las 9", "Te dejo el corte"} <= textos_javier

    mid = next(m["id"] for m in bandeja(client, 4) if m["texto"] == "Te dejo el corte")
    assert client.post(f"/mensajes/{mid}/leido/4").status_code == 204
    no_leidos = client.get("/trabajadores/4/bandeja", params={"no_leidos": True}).json()
    assert mid not in [m["id"] for m in no_leidos]


def test_urgentes_aparecen_primero(client):
    client.post("/mensajes", json={"autor_id": 6, "texto": "normal"})
    client.post("/mensajes", json={"autor_id": 6, "texto": "¡urgente!", "prioridad": "urgente"})
    client.post("/mensajes", json={"autor_id": 6, "texto": "otro normal"})
    assert bandeja(client, 3)[0]["texto"] == "¡urgente!"


def test_necesidades_de_material(client):
    nec = {n["codigo"]: n for n in client.get("/productos/1/necesidades", params={"cantidad": 10}).json()}
    assert nec["LIST-PINO"]["falta"] == 20
    assert nec["TEL-TERC-GRIS"]["falta"] == 20   # 10 x 14 m = 140 m, hay 120
    assert nec["GUAT-200"]["falta"] == 0         # 10 x 10 m = 100 m, hay 200


def test_orden_para_stock_suma_producto_terminado(client):
    orden = client.post("/ordenes", json={"producto_id": 1, "cantidad": 2}).json()
    assert orden["tipo"] == "stock"
    for _ in range(6):  # cinchado, corte, costura, tapizado, montaje, terminada
        r = client.post(f"/ordenes/{orden['id']}/avanzar")
    assert r.json()["estado"] == "terminada" and r.json()["siguiente"] is None
    assert client.get("/productos/1").json()["stock"] == 2
    assert client.post(f"/ordenes/{orden['id']}/avanzar").status_code == 409  # no hay "entregada"
    assert client.get("/ordenes", params={"abiertas": True}).json() == []


def test_pedido_cliente_servido_desde_stock(client):
    # Sin producto terminado no se puede servir desde stock
    r = client.post("/ordenes", json={"producto_id": 1, "cantidad": 1, "cliente": "Ana", "desde_stock": True})
    assert r.status_code == 409

    orden = client.post("/ordenes", json={"producto_id": 1, "cantidad": 3}).json()
    for _ in range(6):
        client.post(f"/ordenes/{orden['id']}/avanzar")
    tela_antes = material(client, "TEL-TERC-GRIS")["stock"]

    pedido = client.post("/ordenes", json={"producto_id": 1, "cantidad": 2, "cliente": "Ana",
                                           "desde_stock": True}).json()
    assert pedido["tipo"] == "cliente" and pedido["estado"] == "terminada"
    assert client.get("/productos/1").json()["stock"] == 1
    assert material(client, "TEL-TERC-GRIS")["stock"] == tela_antes  # no consume material
    assert client.post(f"/ordenes/{pedido['id']}/avanzar").json()["estado"] == "entregada"


def test_desde_stock_requiere_cliente(client):
    r = client.post("/ordenes", json={"producto_id": 1, "cantidad": 1, "desde_stock": True})
    assert r.status_code == 409


def test_pagina_web(client):
    r = client.get("/")
    assert r.status_code == 200 and "ERP Taller" in r.text


def test_modelo_con_ficha_y_preparado(client):
    goma = material(client, "ESP-HR35-10")
    r = client.post("/productos", json={
        "codigo": "sof-chaise-3p", "nombre": "Sofá Chaise 3 plazas", "forma": "Chaise longue",
        "ancho_cm": 270, "fondo_cm": 160, "alto_asiento_cm": 44,
        "preparado": [
            {"pieza": "Asiento", "tipo": "Goma", "material_id": goma["id"], "medidas": "60 x 55 x 10 cm", "cantidad": 3},
            {"pieza": "Chaise", "tipo": "Goma", "descripcion": "Goma D30 (no está en almacén)", "medidas": "150 x 70 x 12 cm"},
        ]})
    assert r.status_code == 201
    m = r.json()
    assert m["codigo"] == "SOF-CHAISE-3P" and m["forma"] == "Chaise longue" and m["alto_cm"] is None
    assert [l["pieza"] for l in m["preparado"]] == ["Asiento", "Chaise"]
    assert m["preparado"][0]["material"] == "Goma HR35 10 cm"
    assert m["preparado"][1]["material_id"] is None


def test_editar_ficha_sustituye_el_preparado(client):
    r = client.put("/productos/1/ficha", json={
        "nombre": "Sofá Nórdico 3 plazas (v2)", "forma": "Recto", "ancho_cm": 220,
        "preparado": [{"pieza": "Respaldo", "tipo": "Guata", "descripcion": "Guata 300 g"}]})
    m = r.json()
    assert m["nombre"] == "Sofá Nórdico 3 plazas (v2)" and m["ancho_cm"] == 220
    assert len(m["preparado"]) == 1 and m["preparado"][0]["descripcion"] == "Guata 300 g"
    assert len(m["escandallo"]) == 8  # el escandallo de consumo no se toca


def test_preparado_valida_lineas(client):
    sin_pieza = client.put("/productos/1/ficha", json={"preparado": [{"tipo": "Goma"}]})
    assert sin_pieza.status_code == 422
    vacia = client.put("/productos/1/ficha", json={"preparado": [{"pieza": "  "}]})
    assert vacia.status_code == 409
    material_inexistente = client.put("/productos/1/ficha", json={"preparado": [{"pieza": "Asiento", "material_id": 999}]})
    assert material_inexistente.status_code == 404
    # Ningún intento fallido ha borrado el preparado original
    assert len(client.get("/productos/1").json()["preparado"]) == 4


def test_migracion_de_base_de_datos_antigua(tmp_path):
    import sqlite3
    ruta = str(tmp_path / "antigua.db")
    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY, codigo TEXT UNIQUE, nombre TEXT)")
    c.execute("INSERT INTO productos (codigo, nombre) VALUES ('X', 'Viejo')")
    c.commit(); c.close()
    cliente = TestClient(create_app(ruta))
    p = cliente.get("/productos/1").json()
    assert p["stock"] == 0 and p["forma"] is None and p["preparado"] == []


def codigos(respuesta):
    return {m["codigo"] for m in respuesta.json()}


def test_stock_general_filtra_por_categoria_grupo_y_modelo(client):
    assert codigos(client.get("/materiales", params={"categoria": "goma"})) == {"ESP-HR35-10", "GOMA-D25-5"}
    assert codigos(client.get("/materiales", params={"categoria": "guata"})) == {"GUAT-200"}
    assert codigos(client.get("/materiales", params={"categoria": "Brazo"})) == {"BRZ-NORD"}
    assert codigos(client.get("/materiales", params={"categoria": "arcón"})) == {"ARC-ABAT-140"}
    piezas = codigos(client.get("/materiales", params={"grupo": "pieza"}))
    assert piezas == {"ESQ-NORD-3P", "BRZ-NORD", "RESP-NORD", "CAB-RECL", "ARC-ABAT-140"}
    assert codigos(client.get("/materiales", params={"producto_id": 1})) == {"ESQ-NORD-3P", "BRZ-NORD", "RESP-NORD"}
    assert codigos(client.get("/materiales", params={"q": "nave 2"})) == {"ESP-HR35-10", "GOMA-D25-5", "GUAT-200"}
    bajo = codigos(client.get("/materiales", params={"grupo": "pieza", "bajo_minimo": True}))
    assert bajo == {"RESP-NORD"}
    brazo = material(client, "BRZ-NORD")
    assert brazo["grupo"] == "pieza" and brazo["modelo"] == "Sofá Nórdico 3 plazas"


def test_categorias_con_recuentos_y_alta_de_nuevas(client):
    cats = {c["nombre"]: c for c in client.get("/categorias").json()}
    assert cats["goma"]["articulos"] == 2 and cats["goma"]["bajo_minimo"] == 1
    assert cats["cabezal"]["grupo"] == "pieza" and cats["cojín"]["articulos"] == 0

    assert client.post("/categorias", json={"nombre": "Reposapiés", "grupo": "pieza"}).status_code == 201
    assert client.post("/categorias", json={"nombre": "reposapiés", "grupo": "pieza"}).status_code == 409
    r = client.post("/materiales", json={"codigo": "REP-01", "nombre": "Reposapiés cuadrado", "categoria": "reposapiés",
                                         "unidad": "ud", "stock": 2})
    assert r.status_code == 201 and r.json()["grupo"] == "pieza"
    assert codigos(client.get("/materiales", params={"categoria": "reposapiés"})) == {"REP-01"}


def test_categoria_nueva_al_dar_de_alta_un_material(client):
    r = client.post("/materiales", json={"codigo": "MUELLE-01", "nombre": "Muelle zig-zag", "categoria": "Muelles",
                                         "unidad": "ud"})
    assert r.json()["categoria"] == "muelles" and r.json()["grupo"] == "material"
    assert "muelles" in {c["nombre"] for c in client.get("/categorias").json()}
    assert client.post("/materiales", json={"codigo": "X", "nombre": "X", "categoria": "goma", "unidad": "ud",
                                            "producto_id": 999}).status_code == 404


# ---------- Compras (oficina) ----------

def test_sugerencias_de_compra_por_proveedor(client):
    sug = {g["proveedor"]: g for g in client.get("/compras/sugerencias").json()}
    # Goma D25: stock 6, mínimo 8 -> reponer hasta 16 = pedir 10. Las piezas (respaldo) no se compran.
    assert list(sug) == ["Espumas Ejemplo"]
    linea = sug["Espumas Ejemplo"]["lineas"][0]
    assert linea["codigo"] == "GOMA-D25-5" and linea["cantidad_sugerida"] == 10


def test_lo_ya_pedido_no_se_vuelve_a_sugerir(client):
    goma = material(client, "GOMA-D25-5")
    client.post("/compras/pedidos", json={"proveedor": "Espumas Ejemplo",
                                          "lineas": [{"material_id": goma["id"], "cantidad": 4}]})
    linea = client.get("/compras/sugerencias").json()[0]["lineas"][0]
    assert linea["ya_pedido"] == 4 and linea["cantidad_sugerida"] == 6
    client.post("/compras/pedidos", json={"proveedor": "Espumas Ejemplo",
                                          "lineas": [{"material_id": goma["id"], "cantidad": 6}]})
    assert client.get("/compras/sugerencias").json() == []


def test_ciclo_pedido_compra_y_recepcion_completa(client):
    goma = material(client, "GOMA-D25-5")
    p = client.post("/compras/pedidos", json={"proveedor": "Espumas Ejemplo", "trabajador_id": 6,
                                              "lineas": [{"material_id": goma["id"], "cantidad": 10}]}).json()
    assert p["estado"] == "borrador" and p["creado_por_nombre"] == "Carmen"
    # No se puede recibir sin enviar
    assert client.post(f"/compras/pedidos/{p['id']}/recibir").status_code == 409
    assert client.post(f"/compras/pedidos/{p['id']}/enviar").json()["estado"] == "enviado"
    r = client.post(f"/compras/pedidos/{p['id']}/recibir", json={"trabajador_id": 1}).json()
    assert r["estado"] == "recibido" and r["lineas"][0]["cantidad_recibida"] == 10
    assert material(client, "GOMA-D25-5")["stock"] == 16
    assert client.get("/movimientos").json()[0]["motivo"].startswith(f"Pedido de compra #{p['id']}")
    assert any("Ha llegado el pedido" in m["texto"] for m in bandeja(client, 1))  # almacén
    assert client.post(f"/compras/pedidos/{p['id']}/cancelar").status_code == 409


def test_recepcion_parcial_avisa_urgente(client):
    goma, guata = material(client, "GOMA-D25-5"), material(client, "GUAT-200")
    p = client.post("/compras/pedidos", json={"proveedor": "Espumas Ejemplo", "lineas": [
        {"material_id": goma["id"], "cantidad": 10}, {"material_id": guata["id"], "cantidad": 50}]}).json()
    client.post(f"/compras/pedidos/{p['id']}/enviar")
    client.post(f"/compras/pedidos/{p['id']}/recibir", json={"lineas": [
        {"material_id": goma["id"], "cantidad": 10}, {"material_id": guata["id"], "cantidad": 30}]})
    assert material(client, "GUAT-200")["stock"] == 230
    aviso = next(m for m in bandeja(client, 1) if "Ha llegado el pedido" in m["texto"])
    assert aviso["prioridad"] == "urgente" and "incompleto" in aviso["texto"]


def test_pedido_compra_validaciones(client):
    goma = material(client, "GOMA-D25-5")
    assert client.post("/compras/pedidos", json={"proveedor": "X", "lineas": []}).status_code == 422
    assert client.post("/compras/pedidos", json={"proveedor": "X", "lineas": [
        {"material_id": 999, "cantidad": 1}]}).status_code == 404
    assert client.post("/compras/pedidos", json={"proveedor": "X", "lineas": [
        {"material_id": goma["id"], "cantidad": 1}, {"material_id": goma["id"], "cantidad": 2}]}).status_code == 409
    p = client.post("/compras/pedidos", json={"proveedor": "X", "lineas": [
        {"material_id": goma["id"], "cantidad": 1}]}).json()
    assert client.post(f"/compras/pedidos/{p['id']}/cancelar").json()["estado"] == "cancelado"
    assert client.get("/compras/pedidos", params={"estado": "abiertos"}).json() == []


def test_bajo_minimo_avisa_tambien_a_oficina(client):
    lino = material(client, "TEL-LINO-CRUDO")
    client.post(f"/materiales/{lino['id']}/salida", json={"cantidad": 35})
    assert any("Compras" in m["texto"] for m in bandeja(client, 6))  # Carmen, oficina
    resp = material(client, "BRZ-NORD")  # pieza: no se compra, no avisa a oficina
    client.post(f"/materiales/{resp['id']}/salida", json={"cantidad": 3})
    assert not any("Brazo" in m["texto"] for m in bandeja(client, 6))


# ---------- Fotos de modelos y piezas ----------

PNG_1PX = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_subir_foto_y_asignarla_al_modelo_y_a_una_pieza(client):
    r = client.post("/imagenes", json={"data": PNG_1PX})
    assert r.status_code == 201
    nombre = r.json()["nombre"]
    foto = client.get(r.json()["url"])
    assert foto.status_code == 200 and foto.content.startswith(b"\x89PNG")

    m = client.put("/productos/1/ficha", json={"imagen": nombre, "preparado": [
        {"pieza": "Asiento", "tipo": "Goma", "medidas": "60 x 55 x 10 cm", "imagen": nombre},
        {"pieza": "Brazo", "tipo": "Guata"}]}).json()
    assert m["imagen"] == nombre
    assert [l["imagen"] for l in m["preparado"]] == [nombre, None]
    # Quitar la foto
    assert client.put("/productos/1/ficha", json={"imagen": ""}).json()["imagen"] is None


def test_fotos_no_validas(client):
    assert client.post("/imagenes", json={"data": "data:image/gif;base64,R0lGOD"}).status_code == 409
    assert client.post("/imagenes", json={"data": "data:image/png;base64,no-es-base64!!"}).status_code == 409
    falsa = "data:image/jpeg;base64,SG9sYQ=="  # "Hola" disfrazado de JPEG
    assert client.post("/imagenes", json={"data": falsa}).status_code == 409
    assert client.put("/productos/1/ficha", json={"imagen": "../erp.db"}).status_code == 409
    assert client.get("/imagenes/..%2Ferp.db").status_code == 404
    assert client.get("/imagenes/" + "0" * 32 + ".jpg").status_code == 404
