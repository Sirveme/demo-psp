# demo.perusistemas.pro — "Paga y recibe tu comprobante"

Demo pública de **PagoOK + Facturalo.pro**: la persona paga S/ 1.00 por Yape o Plin al 938 246 208 y recibe una
boleta o factura real de **Perú Sistemas Pro E.I.R.L. (RUC 20615446565)**, aceptada por SUNAT.

App independiente: consume las APIs de Facturalo y PagoOK como cualquier cliente. Contratos en `docs/`.

## Candado de S/ 1.00

La credencial de PagoOK ve todos los pagos de Perú Sistemas Pro. Por eso la demo **solo consulta y marca como
usados pagos de exactamente S/ 1.00** (`app/servicios/pagook.py`): cualquier otro monto se rechaza antes de llamar
a PagoOK. La credencial es propia ("Demo PSP"), separada de la de MITA.

## Cómo funciona

1. **Tus datos**: boleta (DNI opcional) o factura (RUC), titular del Yape/Plin y billetera. Siempre S/ 1.00. Se guarda `registrado_en`.
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
| `/clinica` | Clínica Demo: Caja Principal, Caja Farmacia y Contabilidad (ver abajo) |
| `/sala?k=SALA_CLAVE` | Pantalla para proyector (se actualiza cada 3 s) |
| `/healthz` | Estado: qué integraciones están configuradas y consultas a PagoOK del último minuto |

## Rubros

Se editan en `rubros.json` (`slug`, `nombre`, `icono`, `concepto`, `que_destraba`). El servidor relee el archivo
cuando cambia; en Railway, edítalo en el repo y haz deploy. El `slug` es la URL.

## Clínica Demo (`/clinica`)

- Dos monitores de caja y un tercero, «Contabilidad — todas las sedes», con los pagos de ambas y el total del día.
  Los monitores no muestran nombres: monto, hora, medio y «✓ Confirmado en X s».
- El visitante elige una caja, escribe DNI (boleta) o RUC (factura) —el nombre se completa solo— y el titular del
  Yape/Plin, y toca **«Voy a pagar»**: la caja queda **reservada 3 minutos** con un contador. Una caja reservada
  muestra «Caja ocupada, intenta en un momento».
- Vale el **primer pago de S/ 1.00 con hora dentro de la reserva** cuyo nombre no contradiga al titular declarado
  (así dos cajas que esperan a la vez no se cruzan los pagos). Se marca usado con `DEMO-{id}` y se emite la boleta
  o factura a nombre del visitante. Si pasan los 3 minutos, la caja se libera.
- **«Clínicas que confían en PagoOK»**: oculta mientras `clinicas.json` esté vacío. Cada clínica necesita
  `nombre`, `autorizado_por` y `autorizado_el`; sin las tres no se muestra. Solo con autorización escrita.
- **WhatsApp «Pago confirmado»**: apagado. Se activa con `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID` y
  `WA_PLANTILLA_PAGO` (plantilla de UTILIDAD aprobada en Meta, ver `app/servicios/whatsapp.py`); entonces el
  formulario pide el celular (opcional).

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

Sin servidor: `python tests/test_reglas.py` y `python tests/test_clinica.py` (cajas, reservas, candado de S/ 1.00).

## Límites conocidos

- PagoOK limita a 120 consultas/min por credencial. La demo usa como máximo `PAGOOK_MAX_POR_MIN`
  (60 por defecto); con muchas demos a la vez, la vigilancia se espacia sola.
- Los pagos al 938 246 208 son todos de Perú Sistemas Pro. Cuando la demo tenga su propio número, pasará a su
  propia empresa en PagoOK (separación completa).
- Las boletas se informan a SUNAT por Resumen Diario: eso lo hace Facturalo/PSP, no esta app.
