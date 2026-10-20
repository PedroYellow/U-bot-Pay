# Audit — 20 Oct 2026, cuarta ronda (bridge, rate limits, superficie)

Ronda centrada en la superficie de **cross-chain** y en **qué pasa cuando alguien
no es el usuario legítimo**. Sin acceso SSH, la config de nginx se leyó del repo
(`deploy/nginx.conf`) y los endpoints se probaron en producción.

## 🟠 ALTO — `/bridge/confirm` acepta cualquier `tx_hash` sin validar nada

`POST /bridge/{bridge_id}/confirm` (api.py:994):

```python
tx_hash = str(body.get("tx_hash") or "").strip()
if not tx_hash:
    raise HTTPException(400, "tx_hash required")
core.update_bridge(bridge_id, tx_hash=tx_hash, status="submitted")
```

Solo comprueba que el puente exista y sea tuyo. **No valida** que el hash tenga
forma de hash, ni que la transacción exista en la cadena, ni que su `from` sea tu
dirección. Cualquier cadena de texto sirve.

Consecuencias:
1. La DB queda con un `tx_hash` inventado y el puente pasa a `submitted`.
2. `/bridge/{id}/status` consulta a LI.FI con ese hash. Si LI.FI no lo conoce, el
   `except` (api.py:1068) se traga el error y devuelve el puente tal cual — se
   queda en `submitted` para siempre, sin avisar al usuario de que su dinero
   quizá no se movió.
3. Un usuario confuso (o un front con un bug) puede marcar su puente como
   enviado sin haber firmado nada, y la app miente sobre el estado.

**Fix**: validar el formato (`0x` + 64 hex), comprobar que la tx existe vía
`eth_getTransactionByHash`, y que `tx["from"]` coincide con
`br["from_address"]`. Solo entonces `submitted`.

## 🟠 ALTO — `/bridge/quote` es público, sin rate limit, y gasta cuota de terceros

Probado en producción: **12/12 peticiones seguidas devolvieron 200**.

Cada petición pega contra LI.FI o Across. Es un endpoint abierto para que un
tercero consuma tu cuota y, de paso, descubra qué rutas y partners tienes
configurados. El rate limit de nginx solo cubre `/turn` (ver más abajo).

**Fix**: añadir `/bridge/quote` a la zona de `limit_req`, y cachear cotizaciones
por `(from,to,amount)` unos segundos — la cotización no cambia en 30s.

## 🟠 MEDIO — La notificación de "bridge done" reporta el mínimo como si fuera lo recibido

api.py:201-209:

```python
delivered = int(bridge.get("to_amount_min") or 0) / 1e6
text = f"✅ Bridge done!\n  Recipient received <b>{delivered:.2f} USDC</b>"
```

`to_amount_min` es el **peor caso garantizado**, no lo entregado. En producción,
un bridge de 10 USDC a Base devuelve `to_amount_min_human: 0.500076` — así que el
usuario recibe un Telegram diciendo "recibió 0.50 USDC" cuando lo real son 9.99.
El mensaje es sistemáticamente pesimista y, si el bridge entrega de menos del
mínimo, seguiría diciendo el mínimo en vez de la cifra real.

**Fix**: leer el `to_amount` real del status de LI.FI y usar ese.

## 🟡 MEDIO — `/docs` y `/openapi.json` públicos: mapa de 33 endpoints

Ambos devuelven 200 sin auth. Describen los 33 endpoints, mencionan `x-api-key`
y `Bearer`, pero **no definen `securitySchemes`**, así que ni siquiera documentan
cómo autenticarse. Para quien explorase el servicio, es un índice completo.

No es una vulnerabilidad por sí solo (todo lo sensible ya exige auth), pero
facilita la enumeración. Decisión: mantenerlo tras flag `UBOT_DOCS_PUBLIC=1`, o
protegerlo con la API key y desactivarlo en producción.

## 🟡 MEDIO — Rate limit: solo `/turn`

De `deploy/nginx.conf`, el único `limit_req` del sitio:

```nginx
location = /turn {
    limit_req zone=ubotturn burst=8 nodelay;
    limit_req_status 429;
}
```

**Todo lo demás está abierto**, incluidos `/bridge/`, `/auth/wallet/nonce` (genera
nonces), `/push/vapid-key` y los endpoints con API key. El audit del 15 Oct
anotaba que el limit cubría "`/turn` y `/agent/`" — la config actual **solo**
cubre `/turn`. Puede ser una regresión o una nota errónea del audit anterior.

Lo que sí tiene límite a nivel de aplicación: `/bridge/analytics`, 30 req/min por
usuario (para que un JWT robado no allows enumerar). Es el único.

## 🟡 MEDIO — Error message de cotizaciones filtra detalle interno

```python
except Exception as e:
    log.warning("bridge quote failed: %s", e)
    raise HTTPException(502, f"bridge quote failed: {e}")
```

El `str(e)` de una excepción de httpx/lifi puede incluir la URL completa con
query params, nombres de host internos y a veces cabeceras. Se devuelve al
llamante. Debería ser un mensaje genérico y el detalle quedarse en el log.

## ✅ Revisado y OK

- **`bridge.py` no toca la private key.** El usuario firma desde su wallet
  (MetaMask), el backend solo cotiza y sigue estados. El diseño correcto.
- **Allowlist de contratos de bridge** (audit M5): 15 direcciones canónicas
  (LI.FI diamond, CCTP, Stargate, Across, Squid). Si el `tx.to` del partner no
  está en la lista, la cotización se rechaza con 502. Buena defensa.
- **Ownership de puentes**: `bridge_start`, `confirm` y `status` comparan
  `br["user_id"] != user["id"]` → 403. Y los cuatro tienen el guard
  `if err or user is None` correcto (arreglado en ronda 2).
- **Acotado a EVM**: los parámetros de chain_id y las direcciones pasan por
  `Query(..., ge=1, le=999999)` y `pattern=r"^0x[a-fA-F0-9]{40}$"`.
- **Límites en `/bridge/start`**: amount 0.01–1.000.000, from ≠ to, ambas
  direcciones con formato válido.
- **`/report` del DataAgent** sigue en 402 (paga por x402 antes de responder).

## Orden de arreglo

1. Validar `tx_hash` en `/bridge/{id}/confirm` (formato + existe en cadena +
   `from` coincide). Sin esto la app puede mentir sobre el estado de un pago.
2. Rate limit + caché en `/bridge/quote`.
3. Arreglar el mensaje de "bridge done" para usar lo realmente entregado.
4. `/docs` tras flag, y limpiar el `str(e)` de las 502.
5. Revisar si `/agent/` perdió su rate limit respecto al audit del 15 Oct.

---

# ✅ Fixed — 1 y 3 (y un bonus que salió por el camino)

## 1. `/bridge/{id}/confirm` valida la transacción

Tres capas, de la más barata a la más cara:

```python
if br["status"] in ("done", "failed"):
    raise HTTPException(409, ...)          # no se puede rebobinar un puente cerrado
if not chain.is_tx_hash(tx_hash):
    raise HTTPException(400, ...)          # 0x + 64 hex, sin ida y vuelta
check = chain.verify_bridge_tx(tx_hash, br["from_address"], br["from_chain"])
```

`chain.verify_bridge_tx()` consulta la cadena y exige que `tx.from` coincida con
`from_address` del puente. Devuelve un motivo legible:

| Situación | Respuesta |
|---|---|
| tx del dueño del puente | 200 `verified: true` |
| hash mal formado | 400 |
| tx de otra dirección | 400 `sender_mismatch` |
| hash que nadie ha visto | 400 `tx_not_found` |
| **nuestro RPC caído** | **503**, no 400 |

Ese último caso importa: si el nodo está caído **no** se le dice al usuario que
su transacción es inválida. Se distingue un fallo nuestro de una tx falsa, que es
justo el error que habría hecho que alguien reintentara o se preocupara sin motivo.

## 3. El mensaje de "done" ya no reporta el mínimo

Nueva columna `bridges.delivered_usdc`, rellenada con lo que el partner dice que
llegó. La notificación usa, en este orden: lo entregado → el `to_amount` de la
cotización → (nunca) el mínimo.

```python
delivered = bridge.get("delivered_usdc") or _expected_amount(bridge)
```

Y si de verdad entrega por debajo del mínimo garantizado, lo dice:

```
✅ Bridge done!
  Recipient received 9.99 USDC on Base
⚠️ Below the 0.50 USDC guaranteed minimum      ← solo si ocurre
```

## 🎁 Bonus: `update_bridge` estaba rota desde siempre

Al probar la migración encontré que `core.update_bridge()` leía `cc.rowcount`
sobre la **Connection** ya cerrada del `with`:

```python
with conn() as cc:
    cc.execute("UPDATE bridges SET ...")
return cc.rowcount > 0        # AttributeError: Connection has no rowcount
```

El UPDATE sí se commiteaba, pero la función **lanzaba siempre**. Todos los
llamadores lo tenían en un `except` que se tragaba el error.

Traducción práctica: cada transición de estado de puente (`submitted`,
`confirming`, `done`, `failed`) escribía bien en la DB pero la app nunca lo
sabía — y `mark()`-equivalents devolvían basura. El caller's `except` lo
escondía. **`rowcount` vive en el Cursor**, no en la Connection.

Auditado el resto del fichero: las otras tres apariciones (`lastrowid`,
`rowcount`) ya usaban `cur` correctamente. Solo esta estaba mal.

## Tests

```bash
python3 test_bridge_tx.py   # 37/37
```

Cubre el formato (incluidos payloads de inyección SQL), los cinco desenlaces de
la verificación, la extracción del amount entregado y el mensaje de éxito — con
el caso exacto que fallaba antes (bridge de 10 USDC diciendo 0.50).

Verificado además contra una DB legacy: la migración añade `delivered_usdc` sin
perder datos, y `update_bridge` devuelve `True`/`False` correctamente.

## ✅ 2. Rate limit en `/bridge/quote`, en dos capas

**Edge (nginx)** — zona nueva `ubotquote`, mismo presupuesto 20r/m pero cubo
separado para que un cotizador ruidoso no deja el endpoint de conversación
sin presupuesto:

```nginx
location = /bridge/quote {
    limit_req zone=ubotquote burst=10 nodelay;
    limit_req_status 429;
    ...
}
```

Es `location =` exacto a propósito: el resto de `/bridge/` (history, analytics,
start, confirm) es autenticado por usuario y no debe compartir ese presupuesto por
IP.

**Aplicación** — token bucket por IP en la API, `UBOT_QUOTE_RATE_PER_MIN=20`,
devolviendo **429** (problema del cliente), no 5xx. Cubos acotados a 20.000 con GC
oportunista para que un `X-Forwarded-For` falsificado no crezca el dict sin límite.

### El detalle que hacía inútil el rate limit

`api._client_ip()` lee `X-Forwarded-For` — y **`deploy/nginx.conf` no lo
configuraba en ningún `proxy_pass`**. Sin él, `request.client.host` es siempre
`127.0.0.1` detrás del proxy, o sea que **todos los visitantes habrían
compartido un único cubo de 20/min**: el límite no protegía nada, sólo habría
provocado un corte de servicio.

Añadido `X-Forwarded-For $proxy_add_x_forwarded_for` y `X-Real-IP $remote_addr`
a los 21 `proxy_pass` de la API. Verificado que quedan dentro de sus
`location` y que las llaves siguen balanceadas.

El XFF es controlable por el atacante, así que se usa **solo como clave de
diccionario**: falsificarlo puede como mucho elegir un cubo vacío, nunca
conceder presupuesto extra a su IP real. Cubierto por test.

## ✅ 4. `/docs` tras flag, y 502 sin fuga de detalle

`/docs`, `/redoc` y `/openapi.json` ahora dependen de `UBOT_DOCS_PUBLIC=1`. Por
defecto **desactivados**. Para un hackathon donde enseñar la API es la gracia,
se enciende con una variable.

Las 502 de cotización ya no devuelven `str(e)` al llamante — esas excepciones de
httpx cargan la URL completa y a veces cabeceras. El detalle se queda en el log
del servidor; el cliente recibe un mensaje genérico.

## Test

```bash
python3 test_quote_ratelimit.py   # 18/18
```

Cubre el límite por IP, que las IPs son independientes, que la ventana desliza,
que un actor ruidoso no deja sin servicio a los demás, que un XFF falsificado sólo
elige cubo, y que el exceso es 429 y no 5xx.

## ⚠️ Pendiente: la pregunta del rate limit de `/agent/`

El audit del 15 Oct anotó que el limit cubría "`/turn` **y** `/agent/`". La config
del repo **sólo**Limitaba `/turn` antes de este cambio. No puedo confirmar si la
config desplegada en el VPS difiere del repo — no hay acceso SSH. Si quieres,
cuando despliegues:

```bash
sudo nginx -T | grep -A5 "agent/"
```

Si `/agent/` no tiene `limit_req`, es una regresión y hay que añadirlo.