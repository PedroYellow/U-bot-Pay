# Delegación de auditoría — fugas de secretos y peticiones salientes (21 Oct 2026)

## Alcance

- `/data/workspace/ubotpay/x402_client.py` — buscar secretos, claves o URLs de proveedores que se filtren al cliente, en respuestas, errores o logs.
- `/data/workspace/ubotpay/bridge.py` y `/data/workspace/ubotpay/push.py` — comprobar que no haya peticiones salientes a destinos controlados por el usuario sin allowlist.

**Fuera de alcance (no repetir, ya auditado y arreglado):**

- `/push/subscribe` — `validate_push_endpoint` ya valida el endpoint como https + host en allowlist (fix ronda 13 / `AUDIT_2026-10-20i_deploy.md`). Aquí solo se verifica que NO exista OTRA petición saliente.
- CORS wildcard, SSRF en `/push/subscribe`, pagos atascados, `chain_id` en `/turn`, USDT en bridge, allowlist de USDC divergente, nonce x402 del DataAgent, política de pagos, dual control — todos cerrados.
- `bridge.py`: `_validate_tx_target` (allowlist M5) ya rechaza `tx.to` desconocido. `/bridge/quote` no devuelve `to` fuera de allowlist. Confirmado en `api.py:1129-1136`.

---

## 1. `x402_client.py` — secretos, claves, URLs de proveedores

Fichero de 57 líneas (`/data/workspace/ubotpay/x402_client.py`). El único secreto que toca es `payer_key`, que es un argumento privado de la función; no es una constante de módulo, no se loguea, no se serializa en la respuesta.

### 1.1 Secretos hardcodeados / constantes con material sensible — NO ES UN PROBLEMA

- **Qué hace ahora**: `x402_client.py` no contiene strings hardcodeados de tipo `0x...` que parezcan claves privadas, ni tokens de API, ni endpoints de proveedores con credenciales. Las únicas constantes son la env-var name `UBOT_X402_PAYEES` (línea 22) y el helper `_payee_allowed` (líneas 27-30).
- **Por qué importa**: nada. El módulo solo lee `_ALLOWED_PAYEES` desde el entorno y no exporta más nada del sistema.
- **Severidad**: no-es-problema.

### 1.2 Fuga del payer key en logs / errores / respuestas

- **Qué hace ahora**: `pay_and_fetch` (líneas 33-57) acepta `payer_key: str` y lo pasa directamente a `chain.sign_authorization(...)` (línea 51). La clave **no se loguea**, **no se devuelve en `result`**, y **no se incluye en excepciones**: las strings de error son `f"price {amount} USDC exceeds agent budget {max_usdc}"` (línea 43) y `f"refusing to pay unapproved payee {acc.get('payTo')}"` (línea 47). El cuerpo de respuesta del 402 (línea 38, 55) lo trunca a `r.text[:300]`. `httpx.AsyncClient` (línea 35) no añade headers ni credenciales.
- **Por qué importa**: incluso un traceback en el caller (`api.py:666`) está limitado a `str(e)[:200]`. La excepción nunca podría incorporar `payer_key` porque `chain.sign_authorization` la consume como argumento posicional sin reformatearla.
- **Severidad**: no-es-problema.

### 1.3 ¿URLs del proveedor hardcodeadas que no deberían estarlo?

- **Qué hace ahora**: `pay_and_fetch` recibe `url: str` (línea 33) **del caller**. El único callsite en el repo es `api.py:663-665`:
  ```python
  url = f"{os.environ.get('DATAAGENT_URL', 'http://127.0.0.1:8091')}/report?company={urllib.parse.quote(q)}"
  r = await x402_client.pay_and_fetch(url, os.environ["UBOT_PRIVATE_KEY"], max_usdc=per_turn)
  ```
  El módulo no tiene la URL hardcodeada: la dirección de la DATAAGENT se lee del entorno (default loopback). No hay otra constante tipo `BASE_URL` ni `PROVIDER_URL`. No se envía ningún valor a un tercero que no sea el declarado por el caller.
- **Por qué importa**: una URL hardcodeada a un proveedor sería un problema (vendor lock-in, imposible de mover a self-hosted). Aquí todo viene de `.env` y los tests (`test_x402_guardrails.py`) ya cubren el gate de presupuesto y allowlist.
- **Severidad**: no-es-problema.

### 1.4 Identificadores internos expuestos al cliente

- **Qué hace ahora**: `pay_and_fetch` devuelve `payee`, `service`, `price_usdc`, `latency_s`, `body`, `status` (línea 56-57). `payee` es la dirección EVM del destinatario (información pública en chain), `service` viene del 402 remoto (`acc.get("description")`) y es lo que el propio servicio publica. No se filtran chain IDs internos, cuentas internas, ni rutas internas.
- **Por qué importa**: nada; el receptor del 402 ve exactamente lo que el servicio quiere publicar.
- **Severidad**: no-es-problema.

### 1.5 Envío de valores a un tercero sin necesidad

- **Qué hace ahora**: el primer `GET url` (línea 36) pasa solo la URL al servicio. El `r = await c.get(url, headers={"X-PAYMENT": x_payment})` (línea 54) adjunta **solo** el header `X-PAYMENT`, que es la autorización EIP-3009 — eso es el contrato de x402.
- **Por qué importa**: nada; `X-PAYMENT` es el único canal que el protocolo x402 espera. No se añade `User-Agent`, `Authorization`, ni cookies.
- **Severidad**: no-es-problema.

### 1.6 Logs que registran material sensible

- **Qué hace ahora**: el módulo **no usa el módulo `logging`** (no hay `import logging`, no hay `log.info`/`log.warning`). No escribe a stdout. Sus únicos efectos son la respuesta en `r` y el dict devuelto.
- **Por qué importa**: cero superficie de log sobre la que filtrar. Los logs que sí se producen están en el caller (`api.py:1125` para bridge), no en x402.
- **Severidad**: no-es-problema.

---

## 2. `bridge.py` y `push.py` — peticiones salientes a destinos user-controlled

### 2.1 `bridge.py` — superficies de red salientes

Inventario completo de las **únicas** llamadas salientes del fichero:

1. **Línea 135-139**: `_http_get_json(url, timeout=12)` hace `urllib.request.urlopen(req)`. Se invoca desde:
   - `quote()` → `LIFI_BASE + "/quote?..."` con la URL construida en la línea **211** a partir de constantes. **No contiene entrada del usuario que pueda controlar el host.** Los params son chain ids, tokens y addresses (todos validados por `Quote` en `api.py:1064-1069`, patrón `^0x[a-fA-F0-9]{40}$` y regex Solana). El host es SIEMPRE `li.quest`.
   - `get_status()` → `LIFI_BASE + "/status?txHash=..."` (línea 277). `bridge_id` viene del campo `tx_hash` almacenado, no del request crudo: `api.py:1420` (`_bridge.get_status(br["tx_hash"])`) y `_bridge.get_status(...)` se llama solo desde el flujo server-to-server.
2. **Línea 388-392**: `urllib.request.urlopen` directo a `ACROSS_BASE + "?..."` (línea 386). Constante hardcodeada. Params validados igual que arriba.

**¿Hay ALGUNA urlopen/fetch/POST hacia un destino user-controlled?**

- **No.** Los dos hosts son constantes (`LIFI_BASE = "https://li.quest/v1"` línea 22, `ACROSS_BASE = "https://app.across.to/api/suggested-fees"` línea 291). Las direcciones del usuario se incluyen **como query params**, no como host. La validación de EVM/Solana en `api.py:1085-1091` bloquea que una dirección malformada llegue al proveedor.
- Adicionalmente, `api.py:1133-1136` aplica `_validate_tx_target` (allowlist M5, líneas 140-144) sobre el `tx.to` que viene en la respuesta de LI.FI — esto cubre el caso "proveedor comprometido devuelve un `to` arbitrario", que NO es user-controlled pero protege la capa aguas arriba.

**Conclusión bridge.py**: la única petición saliente no validada en rondas anteriores y cubierta por la lógica de providers es la ya gestionada (LI.FI + Across con allowlist de `tx.to`). **No hay nuevos destinos user-controlled.**

- **Severidad**: no-es-problema.

### 2.2 `push.py` — superficies de red salientes

Inventario completo:

1. **`urlparse`** (línea 14, 60) — solo para validar.
2. **`validate_push_endpoint` (líneas 49-77)** — pura validación, sin red.
3. **`webpush_async(...)` en `send_push` (línea 141)** — recibe `subscription_info: dict` cuya clave `endpoint` ya pasó `validate_push_endpoint` (líneas 121-124). Es la única petición saliente real, y **sí está cubierta por allowlist**: https + host en `_ALLOWED_PUSH_HOST_SUFFIXES` (Mozilla, Apple, Edge, FCM, `updates.push.services.mozilla.com`).
4. **`fan_out_push` (líneas 160-174)** — solo dispatch asíncrono sobre `send_push`; no hace red por sí mismo.

**¿Hay OTRA petición saliente en `push.py`?**

- **No.** Verificado con `search_files` por `urllib|httpx|requests|fetch|urlopen|session\.|post\(|get\(`: solo aparece `webpush_async`. No hay `urllib.request.urlopen`, no hay `requests.post`, no hay httpx.
- El comentario en `push.py:33-37` documenta precisamente la única superficie (el `session.post(endpoint)` interno de `pywebpush`), y por eso la allowlist existe.

**Conclusión push.py**: ningún nuevo hallazgo. La única ruta de red está validada en suscripción y de nuevo en envío.

- **Severidad**: no-es-problema.

---

## 3. Notas cruzadas (no son hallazgos nuevos)

- `api.py:1121-1127` ya trunca `str(e)` y lo loguea solo server-side, no lo devuelve al cliente. Esto responde a `httpx/lifi exceptions carry the full URL, sometimes headers` (ronda 4). No es un problema hoy.
- `api.py:666-670` aplica `str(e)[:200]` y un `r.get("body") or r.get("error")` para x402; ya está mitigado.
- `x402_client.py` ni siquiera importa `logging`, lo que elimina una clase entera de fugas accidentales.
- `VAPID_PRIVATE_KEY` (push.py:24) se lee del entorno y se pasa a `vapid_private_key=` de `webpush_async` (línea 144). NO se loguea, NO se expone en `/push/vapid-key` (que solo devuelve la pública, `api.py:1577`).
- `VAPID_SUBJECT` (push.py:25) sigue siendo `mailto:admin@ubotpay.com` por defecto. La auditoría `AUDIT_2026-10-20g_x402.md` no la tocó y `/push/vapid-key` deliberadamente la omite (comentario `audit H6` en `api.py:1573-1575`). No es un secreto, pero conviene que producción la cambie a un alias de abuse gestionado.

---

## 4. Resumen ejecutivo

| Hallazgo | Fichero:línea | Severidad |
|---|---|---|
| 1.1 Sin secretos hardcodeados | `x402_client.py` (global) | no-es-problema |
| 1.2 `payer_key` no se filtra en logs/errores/respuestas | `x402_client.py:33-57` | no-es-problema |
| 1.3 Sin URLs de proveedor hardcodeadas; `url` viene del caller | `x402_client.py:33` | no-es-problema |
| 1.4 Sin identificadores internos en respuesta | `x402_client.py:56-57` | no-es-problema |
| 1.5 Headers salientes mínimos (solo `X-PAYMENT`) | `x402_client.py:54` | no-es-problema |
| 1.6 Módulo sin logging | `x402_client.py` (global) | no-es-problema |
| 2.1 Únicas salidas bridge.py son LI.FI + Across con allowlist de params y de `tx.to` | `bridge.py:22, 135, 211, 277, 291, 388` | no-es-problema |
| 2.2 Única salida push.py es `webpush_async` tras `validate_push_endpoint` | `push.py:121, 141` | no-es-problema |

**Veredicto**: no hay fugas de secretos, claves, URLs de proveedores ni identificadores internos explotables en los tres ficheros auditados. No hay nuevas peticiones salientes a destinos controlados por el usuario sin validar. Lo único que sigue dependiendo de configuración es `UBOT_X402_PAYEES` (allowlist opcional, vacía = demo) y el `VAPID_SUBJECT` (no secreto, pero conviene un alias de abuse en producción), que ya están documentados como "pendiente de configurar en `.env`" en `AUDIT_2026-10-20g_x402.md` y `api.py:1573`.

**Tests**: `python3 test_x402_guardrails.py` → 22/22 PASS. No se modificó ningún fichero del proyecto.
