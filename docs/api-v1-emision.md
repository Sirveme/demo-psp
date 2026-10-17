# API v1 de Emisión — Facturalo.pro

Contrato REST que consume **QueVendi** (y cualquier cliente externo) para emitir
comprobantes electrónicos SUNAT y consultar su estado.

- **Base URL (producción):** `https://facturalo.pro`
- **Prefijo:** todas las rutas cuelgan de `/api/v1`
- **Formato:** JSON (UTF-8). Request y response `application/json`, salvo el PDF (binario).
- **Emisión asíncrona:** al emitir, el comprobante queda **`encolado`** y se envía a
  SUNAT en segundo plano (Celery). El `estado` y la respuesta de SUNAT (CDR) se
  obtienen **consultando** el comprobante después (ver §6).

> Fuente: `src/api/v1/` (`router.py`, `auth.py`, `schemas.py`, `comprobantes.py`).
> Contrato estable y retrocompatible: los campos existentes no cambian de nombre/tipo.

---

## 1. Autenticación

Todas las rutas (excepto `GET /api/v1/` y `GET /api/v1/estado`) requieren **dos headers**:

| Header | Descripción |
|---|---|
| `X-API-Key` | Clave pública del emisor. Formato `fpl_<48 hex>`. |
| `X-API-Secret` | Secreto del emisor (texto plano; el servidor compara su SHA‑256). |

```http
X-API-Key: fpl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
X-API-Secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

El emisor queda identificado por la `X-API-Key`; **no se pasa RUC ni emisor_id** en el
cuerpo: el comprobante se emite siempre a nombre del emisor dueño de la credencial.

### Errores de autenticación

| HTTP | `codigo` | Causa |
|---|---|---|
| 401 | `AUTH_REQUIRED` | Falta algún header. |
| 401 | `INVALID_API_KEY` | La API Key no existe. |
| 401 | `INVALID_API_SECRET` | El secret no coincide. |
| 403 | `API_DISABLED` | La API no está activada para la cuenta. |
| 403 | `ACCOUNT_SUSPENDED` | Emisor inactivo/suspendido. |

Formato de error (igual en toda la API):

```json
{ "exito": false, "error": "API Key inválida", "codigo": "INVALID_API_KEY" }
```

---

## 2. Endpoints

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/api/v1/comprobantes` | Emitir boleta, factura, NC o ND. |
| `GET`  | `/api/v1/comprobantes/{id}` | Consultar estado + CDR de SUNAT. |
| `GET`  | `/api/v1/comprobantes/buscar/referencia/{referencia_externa}` | Buscar por tu ID de origen. |
| `GET`  | `/api/v1/comprobantes/{id}/pdf` | Descargar el PDF (A4/A5/Ticket). |
| `GET`  | `/api/v1/comprobantes/{id}/xml` | Obtener la URL del XML firmado. |
| `POST` | `/api/v1/comprobantes/anular` | Marcar un comprobante como anulado. |
| `POST` | `/api/v1/comprobantes/resumen-diario` | Enviar Resumen Diario de boletas (RC). |
| `GET`  | `/api/v1/estado` | Healthcheck (sin auth). |
| `GET`  | `/api/v1/` | Información de la API (sin auth). |

---

## 3. Emitir comprobante — `POST /api/v1/comprobantes`

### 3.1 Campos del request (comunes)

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `tipo_comprobante` | string | **sí** | `01`=Factura, `03`=Boleta, `07`=Nota de Crédito, `08`=Nota de Débito. |
| `serie` | string | no | Si se omite, se asigna automática (ver §3.5). Se normaliza a MAYÚSCULAS. |
| `cliente` | objeto | **sí** | Ver §3.2. |
| `items` | array | **sí** | Mínimo 1 ítem. Ver §3.3. |
| `observaciones` | string | no | Máx 500. |
| `codigo_matricula` | string | no | Colegiado; se guarda en observaciones (precede a `observaciones`). |
| `fecha_emision` | string | no | `YYYY-MM-DD` (usa hora Perú actual) o ISO `YYYY-MM-DDTHH:MM:SS`. Vacío = hoy (America/Lima). |
| `hora_emision` | string | no | Reservado. |
| `enviar_email` | bool | no | `false` por defecto. Envía el comprobante al email del cliente. |
| `referencia_externa` | string | no | Tu ID en origen (máx 100). Permite buscar e idempotencia del lado de QueVendi. |
| `forma_pago` | string | no | `"Contado"` (default) o `"Credito"`. |
| `cuotas` | array | no | Para crédito: `[{moneda, monto, fecha_pago}]` (persistencia de cuotas pendiente). |

**Campos solo para Nota de Crédito/Débito (`07`/`08`):**

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `documento_ref_tipo` | string | **sí** | Tipo del comprobante que se modifica (`01`/`03`). |
| `documento_ref_serie` | string | **sí** | Serie del documento de referencia (ej. `F001`). |
| `documento_ref_numero` | int | **sí** | Número correlativo del documento de referencia. |
| `motivo_nota` | string | sí (NC/ND) | Código de motivo SUNAT (catálogo 09 para NC: `01`..`07`). |

### 3.2 `cliente`

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `tipo_documento` | string | **sí** | `0`=Sin doc (solo boleta < S/700), `1`=DNI, `4`=CE, `6`=RUC, `7`=Pasaporte. |
| `numero_documento` | string | **sí** | Se limpian espacios automáticamente. RUC=11 díg, DNI=8 díg (validado). |
| `razon_social` | string | **sí** | Nombre o razón social. |
| `direccion` | string | no | Dirección fiscal. |
| `email` | string | no | Destino si `enviar_email=true`. |

### 3.3 `items[]`

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `descripcion` | string | **sí** | Detalle del producto/servicio. |
| `cantidad` | number | no | Default `1`, `> 0`. |
| `unidad_medida` | string | no | Default `ZZ`. Catálogo SUNAT: `NIU`, `ZZ`, `KGM`, etc. |
| `precio_unitario` | number | **sí** | **SIN IGV**, `> 0`. |
| `tipo_afectacion_igv` | string | no | Default `10`. `10`=Gravado, `20`=Exonerado, `30`=Inafecto. |
| `descuento` | number | no | En soles, `>= 0`, por línea. |
| `codigo` | string | no | `codigo_interno` del catálogo; si se envía, habilita el descuento de stock. |

**Cálculo de importes (servidor):**
`base = cantidad * precio_unitario − descuento`; si `tipo_afectacion_igv="10"` →
`igv = base * 0.18`, si no `igv = 0`; `total = base + igv`. El `subtotal`/`igv`/`total`
del comprobante son la suma de líneas (redondeo a 2 decimales). Moneda: `PEN`.

### 3.4 Particularidades por tipo

- **Boleta (`03`)**: cliente puede ser DNI, sin documento (ventas pequeñas) u otros.
  Para aparecer ante SUNAT, las boletas se informan además vía **Resumen Diario**
  (`POST /comprobantes/resumen-diario`, §8).
- **Factura (`01`)**: el cliente **debe** ser RUC (`tipo_documento="6"`, 11 dígitos).
  Si no, error `FACTURA_REQUIERE_RUC`.
- **Nota de Crédito (`07`)** y **Nota de Débito (`08`)**: requieren
  `documento_ref_tipo`, `documento_ref_serie`, `documento_ref_numero` y `motivo_nota`.
  Si falta alguno → `REFERENCIA_REQUERIDA`.

### 3.5 Series automáticas (si no envías `serie`)

| Tipo | Serie auto |
|---|---|
| `01` Factura | `F001` |
| `03` Boleta | `B001` |
| `07` NC sobre **factura** / sobre **boleta** | `FC01` / `BC01` |
| `08` ND sobre **factura** / sobre **boleta** | `FD01` / `BD01` |

El correlativo (`numero`) se asigna por `(emisor, serie)` de forma incremental.

### 3.6 Ejemplos de request

**Boleta:**
```json
{
  "tipo_comprobante": "03",
  "cliente": { "tipo_documento": "1", "numero_documento": "12345678", "razon_social": "JUAN PÉREZ GARCÍA" },
  "items": [ { "descripcion": "Gaseosa 500ml", "cantidad": 2, "unidad_medida": "NIU", "precio_unitario": 3.50, "tipo_afectacion_igv": "10" } ],
  "enviar_email": false,
  "referencia_externa": "QV-VENTA-000123"
}
```

**Factura (cliente con RUC):**
```json
{
  "tipo_comprobante": "01",
  "cliente": { "tipo_documento": "6", "numero_documento": "20123456789", "razon_social": "EMPRESA SAC", "direccion": "Av. Principal 123" },
  "items": [ { "descripcion": "Servicio mensual", "cantidad": 1, "unidad_medida": "ZZ", "precio_unitario": 100.00, "tipo_afectacion_igv": "10" } ],
  "forma_pago": "Contado",
  "referencia_externa": "QV-FAC-000045"
}
```

**Nota de Crédito (anula/corrige una factura):**
```json
{
  "tipo_comprobante": "07",
  "cliente": { "tipo_documento": "6", "numero_documento": "20123456789", "razon_social": "EMPRESA SAC" },
  "items": [ { "descripcion": "Anulación de la operación", "cantidad": 1, "precio_unitario": 100.00, "tipo_afectacion_igv": "10" } ],
  "documento_ref_tipo": "01",
  "documento_ref_serie": "F001",
  "documento_ref_numero": 45,
  "motivo_nota": "01"
}
```

### 3.7 Respuesta de emisión (HTTP 200)

```json
{
  "exito": true,
  "comprobante": {
    "id": "e2f8…-uuid",
    "tipo": "03",
    "serie": "B001",
    "numero": 124,
    "numero_formato": "B001-00000124",
    "fecha_emision": "2026-10-16",
    "cliente_documento": "12345678",
    "cliente_nombre": "JUAN PÉREZ GARCÍA",
    "subtotal": 7.00,
    "igv": 1.26,
    "total": 8.26,
    "estado": "encolado",
    "hash_cpe": null,
    "codigo_sunat": null,
    "mensaje_sunat": "Comprobante encolado para envío a SUNAT"
  },
  "archivos": {
    "pdf_url": "https://facturalo.pro/api/v1/comprobantes/{id}/pdf",
    "xml_url": "https://facturalo.pro/api/v1/comprobantes/{id}/xml",
    "cdr_url": "https://facturalo.pro/api/v1/comprobantes/{id}/cdr"
  },
  "mensaje": "Comprobante encolado. Consulte estado con GET /api/v1/comprobantes/{id}"
}
```

> En la emisión, `estado` es **`encolado`** y `hash_cpe`/`codigo_sunat` vienen `null`:
> el envío a SUNAT ocurre en background. Guarda el `id` (y/o tu `referencia_externa`)
> y **consulta el estado** para obtener el CDR (§6).

> ⚠️ **Pendiente:** `archivos.cdr_url` se devuelve en el response pero **el endpoint
> `GET /api/v1/comprobantes/{id}/cdr` no está implementado** (no existe la ruta en
> `src/api/v1/`). No dependas de esa URL todavía; el resultado de SUNAT se obtiene con
> `GET /api/v1/comprobantes/{id}` (`codigo_sunat`/`mensaje_sunat`). Ver §11.

### 3.8 Errores de emisión

| HTTP | `codigo` | Causa |
|---|---|---|
| 400 | `FACTURA_REQUIERE_RUC` | Factura sin cliente RUC. |
| 400 | `RUC_INVALIDO` | RUC ≠ 11 dígitos. |
| 400 | `DNI_INVALIDO` | DNI ≠ 8 dígitos. |
| 400 | `REFERENCIA_REQUERIDA` | NC/ND sin documento de referencia completo. |
| 500 | `INTERNAL_ERROR` | Error interno (incluye `detalle`). |

---

## 4. PDF — `GET /api/v1/comprobantes/{id}/pdf`

Devuelve el **PDF binario** (`application/pdf`, `Content-Disposition: inline`).

| Query param | Valores | Default |
|---|---|---|
| `formato` | `A4`, `A5`, `TICKET` | `A4` |

Nombre de archivo: `{ruc}_{serie}-{numero}_{YYYYMMDD}.pdf`.
El PDF se puede generar aunque el comprobante aún esté en cola; para el documento
válido ante SUNAT, espera a que el estado sea `aceptado` (el PDF incluye el QR/estado).

---

## 5. XML — `GET /api/v1/comprobantes/{id}/xml`

```json
{ "exito": true, "comprobante_id": "{id}", "numero": "B001-00000124", "xml_url": "/storage/xml/{id}.xml" }
```

Devuelve la **URL** del XML firmado (no el XML en el cuerpo).

---

## 6. Consultar estado y CDR — `GET /api/v1/comprobantes/{id}`

Es el endpoint para hacer **polling** tras emitir (el comprobante encolado).

```json
{
  "exito": true,
  "comprobante": {
    "id": "e2f8…-uuid",
    "tipo": "03",
    "serie": "B001",
    "numero": 124,
    "numero_formato": "B001-00000124",
    "fecha_emision": "2026-10-16",
    "cliente": { "tipo_documento": "1", "numero_documento": "12345678", "razon_social": "JUAN PÉREZ GARCÍA", "email": null },
    "items": [ { "descripcion": "Gaseosa 500ml", "cantidad": 2.0, "unidad_medida": "NIU", "precio_unitario": 3.5, "subtotal": 7.0, "igv": 1.26, "total": 8.26 } ],
    "subtotal": 7.00,
    "igv": 1.26,
    "total": 8.26,
    "estado": "aceptado",
    "hash_cpe": "aBcD123…",
    "codigo_sunat": "0",
    "mensaje_sunat": "La Boleta numero B001-124, ha sido aceptada",
    "referencia_externa": "QV-VENTA-000123"
  }
}
```

### 6.1 Campos clave de la respuesta

| Campo | Significado |
|---|---|
| `estado` | Estado del comprobante en Facturalo (ciclo de vida, ver §6.2). |
| `hash_cpe` | DigestValue del XML firmado (disponible una vez enviado). |
| `codigo_sunat` | **Código del CDR** de SUNAT (`respuesta.codigo_cdr`). `0` = aceptado; `2xxx`/`3xxx` = rechazo/observación. `null` mientras no haya CDR. |
| `mensaje_sunat` | **Descripción del CDR** (`respuesta.descripcion`): texto devuelto por SUNAT. |
| `referencia_externa` | El ID que enviaste al emitir. |

### 6.2 Ciclo de vida de `estado`

```
encolado → enviando → aceptado
                   └→ aceptado_con_observaciones
                   └→ rechazado
```

- `encolado`: creado por la API, pendiente de envío.
- `enviando`: tarea en curso contra SUNAT.
- `aceptado` / `aceptado_con_observaciones`: **aceptado por SUNAT** (CDR con código `0`;
  estos dos son los estados "válidos" que usa el sistema para habilitar el PDF final).
- `rechazado`: SUNAT lo rechazó (ver `codigo_sunat`/`mensaje_sunat`).
- `anulado`: anulado vía `POST /comprobantes/anular`.
- `en_resumen`: boleta incluida en un Resumen Diario enviado.

**Recomendación de polling:** consulta cada ~5 s hasta que `estado` sea
`aceptado`, `aceptado_con_observaciones` o `rechazado` (estados terminales). Para la
mayoría de comprobantes el CDR llega en segundos.

### 6.3 Buscar por tu ID — `GET /api/v1/comprobantes/buscar/referencia/{referencia_externa}`

Útil si perdiste el `id` o para evitar duplicados: busca por el `referencia_externa`
que enviaste.

```json
{ "exito": true, "comprobante": { "id": "…", "numero_formato": "B001-00000124", "estado": "aceptado", "total": 8.26, "referencia_externa": "QV-VENTA-000123" } }
```

Si no existe → `404 { "exito": false, "codigo": "NOT_FOUND" }`.

---

## 7. Anular — `POST /api/v1/comprobantes/anular`

```json
{ "comprobante_id": "e2f8…-uuid", "motivo": "Error en el monto" }
```

Respuesta:
```json
{ "exito": true, "comprobante_id": "…", "estado": "anulado", "mensaje": "Comprobante anulado correctamente" }
```

Errores: `404 NOT_FOUND`, `400 ALREADY_VOIDED`.

> ⚠️ **Pendiente:** hoy este endpoint **solo marca** `estado="anulado"` en Facturalo
> (`# TODO: Generar comunicación de baja real` en el código). **No envía la Comunicación
> de Baja a SUNAT** ni genera la Nota de Crédito. Para dar de baja formalmente un
> comprobante ante SUNAT, coordinar el flujo correspondiente (baja para facturas, NC para
> boletas/correcciones). Ver §11.

---

## 8. Resumen Diario de boletas — `POST /api/v1/comprobantes/resumen-diario`

Las boletas (`03`) se informan a SUNAT por **Resumen Diario (RC)**.

Request:
```json
{ "fecha": "2026-10-16" }
```

Reúne las boletas del emisor de esa fecha en estado `aceptado`/`pendiente` (no incluidas
antes), genera el XML `SummaryDocuments`, lo firma y lo envía; devuelve un **ticket** y
consulta el CDR en background. Las boletas incluidas pasan a `en_resumen`.

Respuesta:
```json
{ "ok": true, "ticket": "…", "zip_name": "…", "boletas_incluidas": 12, "resumen_id": 34, "fecha_referencia": "2026-10-16", "correlativo": 1, "mensaje": "Resumen Diario enviado. Consultando ticket en background." }
```

Si no hay boletas elegibles: `{ "ok": false, "mensaje": "No hay boletas elegibles…", "boletas_incluidas": 0 }`.

---

## 9. Healthcheck e info (sin auth)

- `GET /api/v1/estado` → `{ "exito": true, "servicio": "facturalo.pro API", "version": "1.0.0", "estado": "operativo", "timestamp": "…", "documentacion": "/docs" }`
- `GET /api/v1/` → nombre, versión, lista de endpoints y esquema de autenticación.

---

## 10. Flujo recomendado para QueVendi

1. **Emitir** con `POST /api/v1/comprobantes` enviando siempre un `referencia_externa`
   único (tu ID de venta). Guarda el `id` del response.
2. El comprobante vuelve `encolado`. **Haz polling** a `GET /api/v1/comprobantes/{id}`
   cada ~5 s hasta estado terminal.
3. Al llegar a `aceptado`/`aceptado_con_observaciones`: muestra/entrega el **PDF**
   (`/{id}/pdf`) y guarda `codigo_sunat`/`mensaje_sunat`/`hash_cpe`.
4. Si `rechazado`: muestra `mensaje_sunat`, corrige y reemite (nueva emisión).
5. Para boletas, al cierre del día envía el **Resumen Diario** de esa fecha.

> **Idempotencia:** la API no deduplica por `referencia_externa` al emitir; usa
> `GET /comprobantes/buscar/referencia/{referencia_externa}` antes de reintentar para
> no emitir dos veces la misma venta.

---

## 11. Pendientes / limitaciones conocidas

Verificado contra `src/api/v1/` (al 2026-10-16). Estos puntos están **documentados pero
no implementados**; tenerlos en cuenta en la integración:

| # | Punto | Estado en el código | Impacto para QueVendi |
|---|---|---|---|
| 1 | **Endpoint CDR** `GET /api/v1/comprobantes/{id}/cdr` | **No existe la ruta.** `cdr_url` solo se arma como string en el response de emisión (`comprobantes.py`, response de `POST /comprobantes`). | No usar `cdr_url`. Para el resultado de SUNAT, consultar `GET /api/v1/comprobantes/{id}` (`codigo_sunat`, `mensaje_sunat`). |
| 2 | **Anulación ante SUNAT** `POST /api/v1/comprobantes/anular` | Solo marca `estado="anulado"` localmente (`# TODO: Generar comunicación de baja real`). | La anulación **no llega a SUNAT**. Para baja formal, usar el flujo correspondiente (Comunicación de Baja para facturas; Nota de Crédito para boletas/correcciones). |

> Nota: este doc describe el **contrato vigente**. Cuando estos pendientes se
> implementen, actualizar esta sección y las §4/§7 correspondientes.
