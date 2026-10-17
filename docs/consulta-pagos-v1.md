# PagoOK — API de consulta de pagos v1 (pull)

Para cuando un cliente te dice **"ya pagué"** (su suscripción o su compra) y tu
sistema necesita preguntarle a PagoOK: **¿existe este pago?**

PagoOK **no decide** quién es tu cliente ni qué vendiste. Solo responde **qué tan
seguro está de que el pago existe** en tu empresa. La identidad del comprador y
el producto los conoce tu sistema.

Es el complemento del [contrato de eventos (webhooks)](eventos-webhooks-v1.md):
el webhook **avisa** cuando entra un pago (push); esta API **responde** cuando
tú preguntas (pull).

---

## 1. Autenticación

Header **`X-API-Key`**, la misma credencial de la [API v1](API_V1.md)
(`cuenta_api`). Se emite una por sistema que consulta (Facturalo, QueVendi,
alerta.pe, Te Atiendo...), todas atadas a una empresa:

```bash
python scripts/gestion_cuentas.py crear --empresa-id <ID> --nombre "Facturalo"
```

**Por qué no el secreto del webhook:** ese secreto sirve para que **tu** sistema
verifique que un aviso viene de PagoOK. Usarlo también como credencial de entrada
mezclaría dos funciones distintas: si se filtra, servirían para firmar avisos
falsos y también para consultar. `cuenta_api` ya existe, guarda solo el hash,
tiene rate limit (120/min) y, al ser una por sistema, PagoOK sabe **quién** usó
cada pago sin depender de lo que declare el cliente.

### Regla de seguridad

**Toda consulta filtra por la empresa dueña de la credencial**, resuelta en el
backend a partir de la `X-API-Key`. El cuerpo no tiene (ni acepta) un
`empresa_id`. Con la credencial de la empresa A es imposible ver, consultar,
marcar o siquiera inferir la existencia de un pago de la empresa B: para B, un
pago de A responde exactamente igual que un pago que no existe (`sin_coincidencia`
o `404`).

---

## 2. `POST /api/v1/pagos/consultar`

### Cuerpo

```json
{
  "monto": "30.00",
  "fecha_hora_declarada": "2026-10-03T10:00:00-05:00",
  "ventana_minutos": 15,
  "canal": "yape",
  "nombre_pagador_declarado": "Juan Pérez",
  "codigo_operacion": "00123456"
}
```

| Campo | Tipo | Oblig. | Notas |
|---|---|---|---|
| `monto` | string decimal | sí | Exacto, como texto (`"30.00"`). Mayor que 0. |
| `fecha_hora_declarada` | ISO 8601 | sí | Cuándo dice el cliente que pagó. **Sin zona horaria se interpreta como hora de Lima.** |
| `ventana_minutos` | entero 1–1440 | no | Tolerancia ± alrededor de la hora declarada. Default `15`. |
| `canal` | string | no | `yape`, `plin`, `transferencia`, `tarjeta`, `cajero`, `otro`. **Filtra**: solo se buscan pagos de ese canal. |
| `nombre_pagador_declarado` | string | no | Nombre del **titular del medio de pago** que el cliente dice haber usado. **No** es el nombre del comprador (ver §4). |
| `codigo_operacion` | string | no | Si el cliente lo tiene. Es el desempate más fuerte. |

Errores de validación (monto no decimal, canal desconocido, fecha inválida,
ventana fuera de rango) → **422**. Sin credencial o credencial inválida → **401**.
Exceso de solicitudes → **429**.

### Respuesta (siempre `200`)

```json
{
  "nivel": "alta",
  "motivo": "monto_ventana_nombre",
  "accion_sugerida": "aceptar",
  "mensaje": "Pago encontrado por monto, hora y nombre del pagador.",
  "pago": {
    "pago_id": 4821,
    "monto": "30.00",
    "moneda": "PEN",
    "canal": "yape",
    "institucion": "Yape",
    "contraparte": "Juan Perez Lopez",
    "codigo_operacion": null,
    "fecha_hora": "2026-10-03T10:02:00-05:00",
    "diferencia_minutos": 2.0,
    "coincide_nombre": true
  },
  "candidatos": [],
  "pagos_ya_usados": [],
  "criterios": {
    "monto": "30.00",
    "desde": "2026-10-03T09:45:00-05:00",
    "hasta": "2026-10-03T10:15:00-05:00",
    "canal": "yape",
    "uso_codigo_operacion": true,
    "uso_nombre_pagador": true
  }
}
```

| Campo | Notas |
|---|---|
| `nivel` | `alta`, `media` o `sin_coincidencia`. |
| `motivo` | Por qué se llegó a ese nivel (ver tabla de §3). |
| `accion_sugerida` | `aceptar` / `confirmar_manualmente` / `pedir_voucher`. |
| `mensaje` | Texto en español, apto para mostrar a un operador. |
| `pago` | Solo con `alta`: el pago encontrado. `null` en los demás niveles. |
| `candidatos` | Solo con `media`: los pagos posibles, del más cercano a la hora declarada al más lejano, para que tu sistema o una persona elija. |
| `pagos_ya_usados` | Pagos que **habrían calzado** pero ya se usaron (o los reclamó otro sistema). No compiten como candidatos. Sirve para detectar un voucher reutilizado. |
| `criterios` | Eco de lo que se buscó (ventana ya calculada en hora de Lima). |

Cada pago trae: `pago_id`, `monto` (texto), `moneda`, `canal`, `institucion`,
`contraparte` (**titular del medio de pago**), `codigo_operacion`, `fecha_hora`
(ISO con zona de Lima), `diferencia_minutos` respecto a la hora declarada
(negativo = antes) y, si mandaste nombre, `coincide_nombre`
(`true` / `false` / `null` = no concluyente).

En `pagos_ya_usados` cada pago trae además `estado`: `usado` (con
`uso.sistema`, `uso.referencia_externa`, `uso.usado_en`) o
`reclamado_por_otro_sistema`.

> `fecha_hora` es hoy la hora en que el servidor de PagoOK recibió la
> notificación (segundos después del pago). Misma deuda conocida que
> `fecha_hora_banco` en los webhooks.

---

## 3. Los tres niveles y la jerarquía de desempate

Siempre se busca dentro de la empresa de la credencial, solo **ingresos**
(nunca egresos ni transferencias entre cuentas propias confirmadas) y solo
pagos **disponibles** (no usados ni reclamados por otro sistema).

De más fuerte a más débil:

| # | Regla | Nivel | `motivo` |
|---|---|---|---|
| 1 | `codigo_operacion` exacto + monto exacto → un único pago. **No depende de la ventana**: el código manda aunque el cliente recuerde mal la hora. | **alta** | `codigo_operacion` |
| 1b | Mismo código en más de un pago (raro) | media | `codigo_operacion_repetido` |
| 2 | Monto exacto + dentro de la ventana + **solo uno** cuyo pagador coincide con `nombre_pagador_declarado` | **alta** | `monto_ventana_nombre` |
| 2b | Un único candidato, pero su pagador **no** coincide con el nombre declarado | media | `nombre_no_coincide` |
| 3 | Monto exacto + ventana, **candidato único** (sin nombre, o nombre no concluyente) | **alta** | `candidato_unico` |
| 4 | Monto exacto + ventana, **varios candidatos** sin desempate | **media** | `varios_candidatos` |
| 5 | Ningún pago disponible calza | **sin_coincidencia** | `sin_pagos` |
| 5b | Solo calzan pagos ya usados | **sin_coincidencia** | `ya_usado` |

Detalles:

- Si mandas `codigo_operacion` y no aparece, se sigue con las reglas 2–5, pero
  se **descartan** los candidatos que tienen **otro** código (contradicción).
  Los que no tienen código capturado siguen siendo candidatos (Yape y Plin a
  veces no lo incluyen en la notificación).
- **Comparación de nombres** ("coincide razonablemente"): sin tildes ni
  mayúsculas, por palabras; acepta nombres truncados por la billetera
  (`Pere*` ≈ `Perez`) e iniciales (`Juan P.` ≈ `Juan Pérez`). Exige al menos dos
  palabras en común, una de ellas completa. Si solo se puede comparar un nombre
  de pila (`Juan` vs `Juan Pérez`), el resultado es **no concluyente**: no suma
  ni resta.
- `media` nunca se convierte sola en `alta`. Es la señal de **confirmar
  manualmente**.
- `sin_coincidencia` es la señal para pedir **"sube tu voucher"**.

---

## 4. Comprador vs pagador

Son cosas distintas y pueden ser **personas distintas**.

| | Quién es | ¿Lo conoce PagoOK? |
|---|---|---|
| **Pagador** | El **titular del medio de pago**: el nombre que sale en la notificación ("Juan Perez te yapeó S/ 30"). | **Sí.** Es lo que PagoOK devuelve como `contraparte`. |
| **Comprador** | Quien compra o se suscribe: RUC, razón social, cliente de tu sistema. | **No, nunca.** Lo sabe tu sistema porque el cliente se lo declaró. |

Ejemplo típico: **Bodega San Martín SAC** (comprador, RUC 20...) paga su
suscripción con el **Yape personal de su dueña, María Quispe** (pagadora).

- Correcto: `"nombre_pagador_declarado": "María Quispe"`.
- Incorrecto: `"nombre_pagador_declarado": "Bodega San Martín SAC"`. Nunca va a
  coincidir y bajará la confianza a `media`.

En tu formulario de "ya pagué", pregunta: **"¿A nombre de quién está el Yape /
la cuenta con la que pagaste?"**, no "¿cuál es tu nombre?".

---

## 5. Idempotencia de uso: `POST /api/v1/pagos/{pago_id}/marcar-usado`

Un mismo pago **no puede confirmar dos cosas** (dos suscripciones, dos pedidos).
Cuando tu sistema acepta un pago, márcalo como usado:

```json
{ "referencia_externa": "SUSC-2026-00481", "sistema": "Facturalo suscripciones" }
```

| Campo | Oblig. | Notas |
|---|---|---|
| `referencia_externa` | sí | Para qué lo usaste: id de suscripción, pedido o comprobante en **tu** sistema (máx. 150). |
| `sistema` | no | Nombre legible; por defecto, el nombre de la credencial. |

| Código | Significado |
|---|---|
| `200` | Marcado. Si repites la llamada con **la misma credencial y la misma `referencia_externa`**, también `200` con `ya_estaba_usado: true` (seguro para reintentar). |
| `409` | El pago ya se usó para **otra** referencia (aunque sea de tu mismo sistema) o lo usó/reclamó **otro** sistema. `detail` dice quién y para qué. |
| `404` | El pago no existe o no es de tu empresa. |

Respuesta `200`:

```json
{
  "pago_id": 4821,
  "sistema": "Facturalo suscripciones",
  "referencia_externa": "SUSC-2026-00481",
  "usado_en": "2026-10-03T10:05:12-05:00",
  "usado": true,
  "ya_estaba_usado": false
}
```

**Decisión de diseño.** Se guarda en una tabla aparte, `pago_uso`, con
`UNIQUE(pago_id)`. Esa restricción de la base de datos es la que garantiza que
no haya doble cobro, incluso si dos sistemas marcan el mismo pago en el mismo
instante: solo una fila puede existir. Además, marcar como usado **reclama** el
pago (mismo mecanismo que `POST /pagos/{id}/reclamar`), así deja de aparecer
como disponible en `GET /api/v1/pagos` para los demás sistemas. A la inversa,
un pago reclamado por otra credencial tampoco se puede marcar como usado.

Después de marcarlo, cualquier consulta posterior (de cualquier sistema de la
empresa) ya no lo ofrece como candidato y lo muestra en `pagos_ya_usados`.

---

## 6. Flujo recomendado

```
cliente: "ya pagué S/ 30 por Yape a las 10:00"
        │
        ▼
POST /pagos/consultar ──► alta ──► POST /pagos/{id}/marcar-usado ──► 200 → activar suscripción
        │                                                        └─► 409 → otro ya lo usó: pedir voucher
        ├──────────────► media ──► mostrar candidatos a un operador → elige → marcar-usado
        └──────────────► sin_coincidencia ──► "sube tu voucher"
                          (si pagos_ya_usados no está vacío: posible voucher reutilizado)
```

**Siempre** llama a `marcar-usado` antes de dar el servicio, aunque el nivel sea
`alta`: es el paso que impide el doble uso.

---

## 7. Ejemplos

### curl

```bash
curl -X POST https://pagook.pro/api/v1/pagos/consultar \
  -H "X-API-Key: $PAGOOK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"monto":"30.00","fecha_hora_declarada":"2026-10-03T10:00:00-05:00",
       "canal":"yape","nombre_pagador_declarado":"María Quispe"}'

curl -X POST https://pagook.pro/api/v1/pagos/4821/marcar-usado \
  -H "X-API-Key: $PAGOOK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"referencia_externa":"SUSC-2026-00481"}'
```

### Python

```python
import os
import httpx

PAGOOK = "https://pagook.pro/api/v1"
H = {"X-API-Key": os.environ["PAGOOK_API_KEY"]}

def verificar_pago(monto: str, cuando_iso: str, pagador: str | None, codigo: str | None,
                   referencia: str) -> str:
    r = httpx.post(f"{PAGOOK}/pagos/consultar", headers=H, timeout=10, json={
        "monto": monto,
        "fecha_hora_declarada": cuando_iso,
        "nombre_pagador_declarado": pagador,
        "codigo_operacion": codigo,
    })
    r.raise_for_status()
    res = r.json()

    if res["nivel"] == "alta":
        u = httpx.post(f"{PAGOOK}/pagos/{res['pago']['pago_id']}/marcar-usado",
                       headers=H, timeout=10, json={"referencia_externa": referencia})
        if u.status_code == 200:
            return "confirmado"
        if u.status_code == 409:
            return "pedir_voucher"          # otro sistema/pedido ya lo usó
        u.raise_for_status()

    if res["nivel"] == "media":
        return "revision_manual"            # mostrar res["candidatos"] a un operador
    return "pedir_voucher"
```

---

## 8. Operación (equipo PagoOK)

1. Aplicar `sql/consulta_pagos.sql` en PGAdmin (una sola vez, idempotente;
   requiere `sql/api_v1.sql`). `pago_detectado` no cambia.
2. Emitir una credencial por sistema con `scripts/gestion_cuentas.py`.
3. Revisar usos:
   ```sql
   SELECT u.*, p.monto, p.titular
   FROM pago_uso u JOIN pago_detectado p ON p.id = u.pago_id
   ORDER BY u.usado_en DESC LIMIT 50;
   ```

Código: `app/api/publica_v1_consulta.py` (endpoints) y
`app/services/conciliacion.py` (lógica de coincidencia).

**Fuera de alcance por ahora:** pedido pendiente pre-registrado (conciliación
automática en mostrador), multi-empresa completo y panel visual de conciliación.
