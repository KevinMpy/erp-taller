# ERP Taller v0.2

ERP para talleres de fabricación (ejemplo: tapicería de sofás): **almacén**, **producción** y **comunicación entre trabajadores**.

## Arranque

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m erp.datos_demo        # opcional: datos de ejemplo
uvicorn erp.api:app --reload    # web: http://localhost:8000   API: /docs
pytest                                    # tests
```

## Uso en la red del taller

Un PC hace de servidor (el de oficina, por ejemplo) y el resto entra con el navegador:

```bash
uvicorn erp.api:app --host 0.0.0.0 --port 8000
```

Los demás PCs abren `http://IP-DEL-SERVIDOR:8000` (la IP se ve con `ipconfig`).
Hay que permitir el puerto 8000 en el Firewall de Windows del PC servidor.
La base de datos es el fichero `erp.db`: **haz copia de seguridad de él a diario**.

## Desde el móvil

La web se adapta sola al móvil: las pestañas se deslizan con el dedo, los formularios van a una columna
y las tablas se convierten en tarjetas. Para entrar, el móvil tiene que estar **en la misma wifi** que el
PC servidor y abrir `http://IP-DEL-SERVIDOR:8000` (con el servidor arrancado con `--host 0.0.0.0`).
En Modelos de sofás, "+ Foto" deja elegir entre la galería y **hacer la foto con la cámara** en ese momento.

## Pantallas

- **Mensajes**: bandeja personal (lo tuyo + tu departamento + general), urgentes arriba, contador de no leídos que se actualiza solo cada 30 s.
- **Stock general** (todas las secciones): todo lo que hay en la fábrica, filtrable con un clic por categoría.
  - *Materiales* (se compran): tela, goma, guata, madera, rafia, cincha, fibra, pata, fornitura, embalaje.
  - *Piezas del taller* (se fabrican): esqueleto, brazo, respaldo, cabezal, asiento, arcón, cojín.
  - Botón **+ Categoría** para añadir las que hagan falta (reposapiés, muelles…), filtro por modelo de sofá,
    buscador (compatible con lector de códigos), "solo bajo mínimo", entradas/salidas/recuentos e historial.
- **Órdenes**: crear para stock o para cliente, avisa *antes* de crear si falta material, botón para pasar al siguiente taller.
- **Productos**: escandallos y unidades terminadas en stock.
- **Compras** (solo Oficina): pedidos a proveedores.
  - *Sugerencias*: materiales bajo mínimo agrupados por proveedor, con la cantidad para reponer hasta el doble
    del mínimo (descontando lo ya pedido). Un botón crea el pedido.
  - *Pedido manual* a cualquier proveedor, con notas.
  - Ciclo: borrador → enviado → recibido (o cancelado). Al recibir se indica lo que llegó de verdad, se da
    entrada en el almacén y se avisa a Almacén (urgente si llegó incompleto). Botón **Imprimir** para mandarlo.
  - Cuando un material baja del mínimo, además de a Almacén se avisa a Oficina.
- **Modelos de sofás** (visible para Corte y Oficina): ficha técnica de cada modelo — forma, ancho, fondo,
  alto, alto de asiento, descripción — y su **preparado**: por cada pieza (asiento, respaldo, brazo, arcón…)
  qué goma, guata, tela… lleva, medidas, cantidad y notas. Se pueden añadir tantas líneas como haga falta;
  cada una puede enlazarse a un material del almacén o escribirse a mano, y desde el mismo formulario
  se puede dar de alta un material nuevo en el almacén.
  - **Fotos**: una foto del sofá completo y una por cada pieza del preparado (la goma cortada, la guata…).
    Se reducen en el navegador antes de subirlas y se ven en grande al pulsarlas.
    Se guardan en la carpeta `imagenes/` (junto a la base de datos): **inclúyela en la copia de seguridad**.

## Producción: stock y pedidos de cliente

- **Cliente vacío → orden para stock.** Al terminar, las unidades se suman al stock de producto terminado.
- **Con cliente → pedido.** Termina en *entregada*. Si ya hay sofás terminados, se puede marcar
  "Servir desde producto terminado": el pedido no pasa por los talleres ni consume material.

## Estructura

```
erp/
  db.py              Esquema SQLite y transacciones
  errores.py         Errores de negocio (-> HTTP 404/409)
  api.py             API REST (FastAPI)
  datos_demo.py      Datos de ejemplo
  servicios/
    almacen.py       Materiales, entradas/salidas, recuentos, alertas de stock
    produccion.py    Productos, escandallos, órdenes y flujo por talleres
    mensajes.py      Mensajes a persona / departamento / todos + avisos automáticos
    trabajadores.py
tests/test_erp.py
```

## Qué automatiza

| Evento | Qué hace el ERP |
|---|---|
| Un material baja de su stock mínimo | Aviso **urgente** al departamento de almacén, con proveedor |
| Se crea una orden | Aviso a almacén para preparar material |
| Una orden pasa a **cinchado** (primer taller) | Descuenta todo el escandallo del almacén (todo o nada) |
| Una orden falta material | Bloquea el avance y devuelve qué falta y cuánto |
| Cambio de estado de una orden | Aviso al taller que recibe el trabajo |

Flujo: `pendiente → cinchado → corte → costura → tapizado → montaje → terminada → entregada`

## Decisiones técnicas

- **SQLite + `sqlite3` estándar**: cero servidores que mantener. SQL portable a PostgreSQL cuando haya varias naves o muchos usuarios simultáneos.
- **Lógica en `servicios/`, no en la API**: se puede reutilizar desde una app de tablet, un bot de Telegram o scripts.
- `GET /materiales/codigo/{codigo}` pensado para lectores de código de barras/QR.
