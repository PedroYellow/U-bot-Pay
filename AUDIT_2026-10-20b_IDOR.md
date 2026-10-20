# Audit — 20 Oct 2026, segunda ronda (superficie de identidad)

Continuación del audit anterior. Esta ronda se centró en la **superficie de
identidad**: quién puede operar qué wallet, y qué hace un endpoint público con
una clave que el llamante elige.

## 🔴 CRÍTICO — IDOR en `/turn`: `user_key` elegido por el llamante, sin auth

`POST /turn` es **público, sin autenticación** (api.py:361, comentario explícito:
*"Public — no auth required"*). El `user_key` lo elige el llamante:

```python
# api.py:355
@app.post("/turn")
async def turn(t: Turn):
    user = resolve_user(t.user_key)     # ← caller-controlled, sin auth
    uid = user["id"]
```

Y `resolve_user` (api.py:274) acepta **cualquiera** de estas formas:

| `user_key` | Qué hace |
|---|---|
| `"demo"`, `""`, `null` | devuelve el usuario demo (telegram_id=0) |
| un entero | `core.get_user(telegram_id=int(...))` → **ese usuario** |
| `0x…` (42 chars) | busca por wallet y **si no existe lo crea** |

### Consecuencia 1 — operar la wallet de otra persona

Basta con conocer el `telegram_id` de un usuario (son públicos para quien haya
hablado con el bot, y secuenciales para los primeros) o su dirección de wallet:

```bash
curl -X POST https://api.ubotpay.com/turn \
  -H 'Content-Type: application/json' \
  -d '{"user_key":"<su_telegram_id_o_wallet>","text":"how much do I have?"}'
```

Devuelve **su saldo**, **sus facturas**, **su historial**, **sus contactos**.
No hace falta ser esa persona.

### Consecuencia 2 — pagos desde la wallet de otro, y gasto de la tesorería

Los intents de pago se ejecutan contra la wallet resuelta. El caso más grave es
`agent_query` (api.py:436-460):

```python
r = await x402_client.pay_and_fetch(
    url, os.environ["UBOT_PRIVATE_KEY"],   # ← la PRIVATE KEY de la tesorería
    max_usdc=float(user.get("daily_limit") or 5))
```

Se paga a un tercero (DataAgent) usando la **clave privada del operador**, con el
`user_key` que haya pedido el atacante. El límite (`daily_limit`) sale del usuario
resuelto, y un usuario recién auto-registrado nace con el default.

### Consecuencia 3 — auto-registro unlimited

`upsert_user_by_wallet` crea un usuario para **cualquier** dirección `0x…` válida
que se le pase, sin prueba de。porque `/turn` es público, cualquiera puede crear
usuarios y, con ello, fabricar contexto para los prompts.

**Estado**: no pude completar un exploit end-to-end contra producción porque
`/turn` devuelve **500 en todas las peticiones** (el bug del LLM, ya arreglado en
`6af9800` pero **sin deployear**). Lo verifiqué por lectura de código, y confirmé
en producción que `user_key="1"` (inexistente) → 404 y `user_key="demo"` → 500.
El vector está confirmado por el código; falta ejecutarlo con los fixes puestos.

**Mitigación recomendada** (no aplicada — cambia el contrato del endpoint):
1. `/turn` no debe aceptar un `user_key` libre. El canal Telegram y el widget deben
   identificarse (el bot ya manda `x-api-key`; el widget debe usar sesión/JWT).
2. `agent_query` nunca debe usar `UBOT_PRIVATE_KEY` en un endpoint público: moverlo
   a un worker autenticado, o limitarlo por allowlist.
3. `daily_limit` debe tener un techo global, no solo el valor del usuario.

**Interim**: antes del deploy, poner `/turn` detrás de `x-api-key` mientras se
diseña la auth por sesión. Rompe el widget web público, pero es preferible a
dejarlo abierto.

---

## 🟠 ALTO — El widget web ya no lleva API key, pero el endpoint sigue abierto

`web/index.html:170` dice *"API key removed from browser for security"*, y
`window.__ubotpayAuth` es opcional. Eso significa que el front ya no se autentica,
pero `/turn` no exige nada: **el control de acceso no se movió, se eliminó**.

Antes (audit 15 Oct) el blast radius estaba acotado por la key pública + rate limit.
Ahora es un endpoint abierto que devuelve datos de cualquier `user_key` válido.

---

## 🟡 MEDIO — Cosas revisadas que están bien

Vale la pena dejarlo por escrito para no re-auditarlas:

- **XSS en el front**: los datos de terceros (LI.FI/Across, nombres de partner)
  pasan por `esc()`; audit C1 ya lo había hecho. 15 usos de `innerHTML`, todos con
  strings estáticos o escapados. Las URLs de explorador vienen del servidor.
- **Telegram en grupos**: bloqueado (`_require_private`, tg.py:76-84, audit H1).
- **Secretos en git**: solo `.env.example` trackeado; `.env`, `*.db`, `wallet.env`
  en `.gitignore`. Ninguna clave real en el repo.
- **Link Telegram por OTP**: código de 6 dígitos con expiración de 10 min
  (tg.py:143-165), nunca JWT en el chat (audit C3).
- **`/auth/wallet/nonce`**: mensaje firmado, expira en 600s.

---

## Orden de arreglo (esta ronda)

1. **IDOR en `/turn`** — auth por sesión, `user_key` no libre. Es lo importante.
2. **`agent_query` con `UBOT_PRIVATE_KEY` en endpoint público** — mover a worker.
3. Techo global en `daily_limit`.
4. Desplegar `6af9800` (arregla el 500) —requisito previo de todo lo anterior, porque con el
   500 nadie puede explotar nada, pero tampoco funciona el producto.