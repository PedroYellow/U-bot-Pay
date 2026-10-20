# Audit — 20 Oct 2026, quinta ronda (superficie web, sesiones, PWA)

Ronda sobre lo que se sirve al público y la gestión de sesiones. Sin SSH, la
superficie HTTP se probó contra producción y el código se leyó del repo.

## 🔴 ALTO — El nonce de inicio de sesión nunca caduca (la función que lo valida está muerta)

`core.py` tiene la comprobación correcta:

```python
def consume_wallet_nonce(nonce):
    ...
    if not user.get("jwt_exp") or user["jwt_exp"] < _time.time():
        return None          # ← caducado
    return user
```

**Nadie la llama.** `grep` la encuentra solo en su propia definición. Y
`POST /auth/wallet/verify` reimplementa la búsqueda por dirección y nunca mira
`jwt_exp`:

```python
cur = core.get_user_by_wallet(req.address, req.chain_id)
if not cur or not cur.get("jwt_nonce"):     # ¿existe un nonce? sí/no
    raise HTTPException(400, ...)
# ← no hay ninguna comprobación de expiración
```

`new_wallet_nonce()` sí calcula `exp = now + 600` y lo guarda, y `/auth/wallet/nonce`
lo anuncia como "Codes expire in 10 minutes". Pero ese límite **no se aplica**:
un nonce es válido indefinidamente hasta que se gaste.

**Impacto**: un nonce capturado del tráfico (o de los logs) sirve para firmar
sesiones cuando se quiera, no en 10 minutos. La promesa de "expira en 10 min" que
hace la UI no es real.

**Fix**: usar `consume_wallet_nonce()`, que ya hace lo correcto.

## 🟠 MEDIO — `UBOT_JWT_SECRET` no está documentado en ningún sitio

`api.py:812-817` genera un secreto aleatorio **en cada arranque** si no viene del
entorno:

```python
if not JWT_SECRET or JWT_SECRET == "auto":
    JWT_SECRET = _s.token_urlsafe(48)
    log.warning("UBOT_JWT_SECRET not set — generated a fresh one.")
```

El warning dice "persist it in /opt/ubotpay/.env", pero **`UBOT_JWT_SECRET` no
aparece en `.env.example`**, ni en README, ni en DEMO.md, ni en
`deploy/ubotpay.service`, ni en docs/. Si el VPS no lo tiene puesto, cada
`systemctl restart` invalida todas las sesiones y todos los JWT emitidos: el
usuario tendría que reconectar la wallet de nuevo cada vez que se despliegue.

No es una vulnerabilidad — es un secreto rotatorio, que es lo correcto por
defecto — pero es una trampa de despliegue. Basta documentarlo.

## 🟡 MEDIO — `.well-known/security.txt` devuelve el HTML de la home

```
GET /.well-known/security.txt  ->  200, pero el body es index.html
```

Devuelve 200 con el HTML del sitio, no un `security.txt`. Peor que no tenerlo:
quien lo compruebe verá un 200 y lo dará por válido. El nginx `try_files`
convierte cualquier ruta en la SPA.

## 🟡 MEDIO — `GET /balance` se cachea en el service worker

En `web/sw.js`, `/balance` está en la lista network-first: va a la red, pero
**guarda la respuesta en la caché** (`c.put(req, copy)`).

`/balance` devuelve la wallet del usuario autenticado. En un dispositivo
compartido — un móvil prestado, un navegador compartido en una demo, un
portátil en la mesa de un investors — el saldo queda en el Cache Storage, legible
por cualquiera que abra las herramientas de desarrollo, y sobrevive al logout
(no se borra la caché en `renderDisconnected()`).

El propio fichero dice "NEVER cache anything that carries user data", pero
`/balance` es exactamente eso.

**Fix**: sacar `/balance` de la lista cacheable, o cachear solo cuando la
respuesta sea un 4xx de "no authenticated" y nunca un 200 con saldo.

## ✅ Revisado y OK

- **No hay fuga de ficheros sensibles.** `/.env`, `/.git/HEAD`, `/api.py`,
  `/Dockerfile`, `/requirements.txt`, `/../.env` devuelven todos 200 — pero el
  body es **byte a byte el index.html** (93.469 bytes, mismo sha256). Es el
  `try_files` de la SPA, no una exposición. Comprobado con hash, no a ojo.
- **JWT bien construido**: HMAC-SHA256, `hmac.compare_digest` para la firma,
  `exp` verificado. TTL de 7 días.
- **El nonce se rota tras usarse** (`jwt_nonce = NULL`), así que no hay replay
  de la misma firma.
- **La firma se recupera y se compara** con la dirección del usuario antes de
  emitir el token. `recover_message` + comparación case-insensitive.
- **Service worker bien pensado en general**: devuelve temprano si el hostname
  no es el suyo (así la API nunca pasa por él), y excluye de caché todo lo
  transaccional (`/turn`, `/bridge/*`, `/approve`, `/agent/`, `/auth/wallet/`)
  y todo lo que lleva `Authorization` o `x-api-key`.
- **manifest.json** sirve correctamente como `application/json`.

## Orden de arreglar

1. Que `verify` use `consume_wallet_nonce` — el nonce tiene que caducar. ✅
2. Documentar `UBOT_JWT_SECRET` en `.env.example`. ✅
3. Sacar `/balance` de la caché del service worker. ✅
4. Servir un `security.txt` real o quitar la ruta. ✅

---

# ✅ Fixed

## 1. El nonce ya caduca

`/auth/wallet/verify` comprueba `jwt_exp` antes de tocar la firma, y limpia la
fila para que no quede un nonce muerto:

```python
if not cur.get("jwt_exp") or cur["jwt_exp"] < time.time():
    with core.conn() as cc:
        cc.execute("UPDATE users SET jwt_nonce = NULL, jwt_exp = NULL WHERE id = ?", ...)
    raise HTTPException(400, "nonce expired — call /auth/wallet/nonce again")
```

Falla cerrado: sin `jwt_exp` se rechaza, así que un `NULL` accidental no extiende
la vida de un nonce.

De paso, el `except` de la firma ya no devuelve `str(e)` al llamante — esas
excepciones de `eth_account` llevan internos de la librería. Se quedan en el log.

## 2. `UBOT_JWT_SECRET` documentado

En `.env.example`, con la instrucción de generarlo una vez (`openssl rand -hex 32`)
y el porqué: si falta, cada arranque genera uno nuevo e invalida todas las
sesiones.

**Acción pendiente para el VPS**: comprobar si ya está puesto. Si no, generar y
añadirlo a `/opt/ubotpay/.env`.

## 3. `/balance` fuera de la caché del service worker

Sacado de la lista network-first y con una regla explícita: nada de alcance de
usuario se cachea.

```js
// Anything user-scoped goes straight to the network, never to the cache.
if (url.pathname === "/balance" || url.pathname.startsWith("/history")) return;
```

Y la lista que sí cachea ahora solo guarda respuestas `res.ok` — así un 401 sin
sesión tampoco se queda guardado como si fuera válido.

## 4. `security.txt` real

`web/security.txt` nuevo, más un `location ^~ /.well-known/` en nginx que lo
sirve como `text/plain` y devuelve 404 si no existe, en vez de dejar que caiga al
catch-all de la SPA.

## Tests

```bash
python3 test_nonce_expiry.py   # 13/13
```

Cubre el nonce caducado, el fresco, el límite exacto de 10 minutos (válido a 9m59s,
inválido a 10m01s), la ausencia de `jwt_exp` (falla cerrado) y la ausencia de
nonce. `sw.js` valida con `node --check`.

Los seis suites del proyecto pasan.