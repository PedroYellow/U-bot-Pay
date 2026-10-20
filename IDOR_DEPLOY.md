# IDOR fix — qué cambió y qué tienes que hacer al desplegar

Commit: `fix(security): IDOR on /turn` (ver `git log`).

## El problema

`POST /turn` era público y tomaba `user_key` del cuerpo. `resolve_user()`
acepta un `telegram_id` o una dirección `0x`, así que cualquiera podía:

- leer el saldo, facturas, historial y contactos de otro usuario
- (vía `agent_query`) gastar `UBOT_PRIVATE_KEY` en nombre de quien nombrara

## El arreglo

El usuario se resuelve **de las credenciales del llamante**, nunca del cuerpo.

| Credencial | Quién | Puede elegir user_key | Puede gastar |
|---|---|---|---|
| `Authorization: Bearer <JWT>` | web, wallet conectada | ❌ 403 si nombra otro | ✅ solo sí misma |
| `x-api-key: <UBOT_API_KEY>` | bot Telegram, workers | ✅ | ✅ |
| `x-ubot-channel: <UBOT_VOICE_SECRET>` | webhook ElevenLabs | ❌ (fijado a demo) | ❌ read-only |
| ninguna | el público | — | **401** |

Además:
- `agent_query` exige `trusted` (o sea: sesión o API key) y tiene techo
  `UBOT_AGENT_MAX_USDC` (default 1 USDC). `0` desactiva la función.
- `UBOT_VOICE_SECRET` separado del API key: puedes rotar o revocar la voz sin
  tocar Telegram. Si está vacío, ese camino **falla cerrado** (401).

## ⚠️ Lo que necesitas hacer al desplegar

### 1. Telegram — no tocar
`tg.py` ya manda `x-api-key`. Sigue funcionando.

### 2. ElevenLabs — CAMBIAR
El agente tenía `/turn` como webhook tool. Si no añade el header, el widget de
voz devolverá **401** y se rompe la demo de voz.

En el dashboard de ElevenLabs, en el tool que apunta a `/turn`, añade un header:

```
x-ubot-channel: <el valor que pongas en UBOT_VOICE_SECRET>
```

Genera un valor nuevo (no reutilices el API key):

```bash
openssl rand -hex 32
```

Y en `/opt/ubotpay/.env` del VPS:

```bash
UBOT_VOICE_SECRET=<el valor de arriba>
UBOT_AGENT_MAX_USDC=1
```

Reinicia: `systemctl restart ubotpay`

**Nota sobre la voz**: el webhook queda pineado al usuario demo y en modo
read-only. Es decir, la voz puede consultar saldo/facturas del demo pero no
puede lanzar `agent_query` (el gasto con la private key). Si en la demo quieres
que la voz sí lance research, el camino correcto no es darle `trusted` al webhook
sino mover el research a un worker autenticado. Dime si lo quieres así y lo
hago.

### 3. El widget web
`index.html` ya mandaba el JWT vía `__ubotpayAuth()`, así que funciona sin
cambios. Lo único nuevo es que si no hay sesión conectada, muestra un aviso
"conecta tu wallet" en vez de un error genérico.

## Verificar

```bash
# debe ser 401 (antes devolvía los datos de quien le pasaras)
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.ubotpay.com/turn \
  -H 'Content-Type: application/json' \
  -d '{"user_key":"demo","text":"how much do I have?"}'

# el bot de Telegram debe seguir respondiendo
# (mismo curl pero con x-api-key válido → 200)

# voz: con x-ubot-channel correcto → 200; sin él → 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.ubotpay.com/turn \
  -H 'Content-Type: application/json' \
  -H "x-ubot-channel: $UBOT_VOICE_SECRET" \
  -d '{"text":"how much do I have?"}'
```

## Tests

```bash
python3 test_turn_authz.py      # 9/9 — reglas de identidad de /turn
python3 test_brain_fallback.py  # 26/26 — el /turn nunca da 500
```

Los dos corren sin dependencias ni red.

---

# Añadido en las rondas 5 y 6 (20 Oct)

## `UBOT_JWT_SECRET` — ponlo ahora

```bash
openssl rand -hex 32     # → /opt/ubotpay/.env  como UBOT_JWT_SECRET=...
```

Sin esto, cada reinicio genera un secreto nuevo e invalida todas las sesiones.

## `UBOT_DOCS_PUBLIC`

Vacío = `/docs` y `/openapi.json` devuelven 404. Pon `1` si en la demo quieres
enseñar la API.

## El webhook de voz y el demo

Recuerda: `x-ubot-channel: <UBOT_VOICE_SECRET>` en el tool de ElevenLabs. Sin
ese header el widget de voz da 401. Está pineado al usuario demo y en read-only.

## Co-signer

El segundo firmante tiene que ser **otra persona**. Si el co-signer elegido
nunca ha hablado con el bot, `/cosigner` lo rechaza con un mensaje que explica
qué hacer. Tenlo en cuenta para la demo: que el co-signer mande `/start` al bot
antes.
