# demo.perusistemas.pro — "Paga y recibe tu comprobante"

Demo pública de **PagoOK + Facturalo.pro**: la persona paga S/ 1 o más por Yape al 938 246 208 y recibe una
boleta o factura real de **Perú Sistemas Pro E.I.R.L. (RUC 20615446565)**, aceptada por SUNAT.

App independiente: consume las APIs de Facturalo y PagoOK como cualquier cliente. Contratos en `docs/`.

## Cómo funciona

1. **Tus datos**: boleta (DNI opcional) o factura (RUC), titular del Yape/Plin, monto y billetera. Se guarda `registrado_en`.
2. **Paga**: el servidor consulta `POST /pagos/consultar` cada 3 s (luego cada 5 s) durante 10 min.
   Acepta si `nivel=alta`, el nombre es compatible y el pago es posterior a `registrado_en − 60 s`.
   Si sale `media`, pide la hora del voucher (ventana de 3 min). Siempre hace `marcar-usado` con `DEMO-{id}`.
3. **Comprobante**: `POST /comprobantes` con la leyenda en `observaciones`, y `GET /comprobantes/{id}` cada 1 s hasta `aceptado`.

Cada demo es una fila en `demos` y avanza en su propia tarea; si el servicio se reinicia, se reanuda.

## Rutas

| Ruta | Para qué |
|---|---|
| `/` | Portada con los rubros |
| `/ecommerce`, `/restaurante`, `/servicios`, `/salud`, `/educacion`, `/eventos`, `/otro` | Enlace directo por rubro |
| `/sala?k=SALA_CLAVE` | Pantalla para proyector (se actualiza cada 3 s) |
| `/healthz` | Estado: qué integraciones están configuradas y consultas a PagoOK del último minuto |

## Rubros

Se editan en `rubros.json` (`slug`, `nombre`, `icono`, `concepto`, `que_destraba`). El servidor relee el archivo
cuando cambia; en Railway, edítalo en el repo y haz deploy. El `slug` es la URL.

## Interesados

Tabla `interesados` en la base de Railway (PGAdmin o la pestaña Data):

```sql
SELECT i.creado_en - interval '5 hours' AS hora_lima, i.nombre, i.celular, i.ruc, i.rubro, d.segundos
FROM interesados i LEFT JOIN demos d ON d.id = i.demo_id
ORDER BY i.creado_en DESC;
```

## QR de Yape (opcional)

Si colocas el QR oficial del Yape 938246208 en `static/img/yape-qr.png`, se muestra en el paso 2.
Sin él, se muestra el número con el botón "Copiar".

## Prueba local con simuladores

```bash
pip install -r requirements.txt
DEMO_MOCK=1 SALA_CLAVE=prueba DATABASE_URL=sqlite:///./prueba.db uvicorn app.main:app --port 8765
python tests/prueba_local.py
```

Cubre boleta sin DNI, boleta con DNI, factura, dos demos simultáneas, desempate por hora del voucher,
pago anterior al registro, interesados y `/sala`. `DEMO_MOCK=1` nunca debe estar en Railway.

## Límites conocidos

- La credencial de PagoOK se comparte con MITA (límite 120 consultas/min). La demo usa como máximo
  `PAGOOK_MAX_POR_MIN` (60 por defecto); con muchas demos a la vez, la vigilancia se espacia sola.
- Facturalo guarda `precio_unitario` (sin IGV) con 2 decimales, así que algunos totales no cuadran al
  céntimo (p. ej. S/ 2.00 o S/ 10.00). La demo los rechaza y sugiere el monto más cercano que sí cuadra.
- Las boletas se informan a SUNAT por Resumen Diario: eso lo hace Facturalo/PSP, no esta app.
