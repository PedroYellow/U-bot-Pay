# Audit — 20 Oct 2026, tercera ronda (controles de riesgo)

Esta ronda audita los **controles de dinero**: límite diario, dual-control, y si
de verdad impiden gastar más de lo debido. Es la parte que más caro sale si falla.

## 🔴 ALTO — `daily_limit = 0` desactiva el límite en lugar de bloquear

`core.py:292`:

```python
def check_policy(user, amount_usdc):
    if spent_today(user["id"]) + amount_usdc > (user.get("daily_limit") or 1e12):
```

`0 or 1e12` → **billón**. Un usuario con `daily_limit = 0` (es decir, "no quiero
gastar nada") obtiene en realidad un límite de 1.000.000.000.000 USDC.

Verificado:

```python
>>> check_policy({"id":1,"daily_limit":0,"dual_threshold":1000}, 500, lambda _: 0)
{"allowed": True, "reason": ""}          # ← permitido
```

El patrón `x or <techo enorme>` se usa para decir "sin límite", pero convierte
un `0` explícito — la configuración más restrictiva posible — en la más
permisiva. `None` (usuario sin policy) cae en el mismo agujero.

**Fix**: distinguir "sin política" de "límite cero".

```python
lim = user.get("daily_limit")
if lim is None:            # sin policy → usa el default del operador
    lim = float(os.environ.get("UBOT_DEFAULT_DAILY_LIMIT", "40"))
if spent_today(user["id"]) + amount_usdc > lim:
```

## 🔴 ALTO — El límite se comprueba al cotizar, nunca al liquidar

`check_policy` se llama **una sola vez** en todo el codebase: `_stage_payment`
(api.py:551), que es el paso de *cotizar*. Después el pago se guarda en
`_pending` y puede quedar **pendiente de aprobación horas o días**.

`/approve` (api.py:584) liquida sin volver a mirar la política:

```python
@app.post("/approve")
async def approve(a: Approve, x_api_key=...):
    auth(x_api_key)
    pay = core.get_payment(a.payment_id)
    if not pay or pay["status"] != "pending_approval":
        raise HTTPException(404, ...)
    user = core.get_user(user_id=pay["user_id"])
    if str(user.get("approver_telegram_id")) != str(a.approver_key):
        raise HTTPException(403, "not the approver")
    r = await asyncio.to_thread(chain.send_usdc, ...)   # ← sin check_policy
```

Escenario: el usuario pide 3 pagos de 12 USDC (dual-control, cada uno se cotiza
con `spent_today` ≈ 0 porque ninguno está `settled` todavía), los tres quedan
pendientes, y al aprobarlos todos se gastan 36 USDC contra un `daily_limit` de 40
— o cualquier cifra, si el cotizador se repite.

El problema de raíz es que **`spent_today` solo cuenta `status='settled'`**
(core.py:288). Un pago `pending` o `pending_approval` no reserva importe.

**Fix**: (a) contar también los pendientes, y (b) re-evaluar `check_policy` en
`/approve` antes de firmar.

## 🟠 MEDIO — `spent_today` usa UTC midnight, no el día local

```python
day0 = int(time.time()) - (int(time.time()) % 86400)
```

El día empieza a medianoche UTC (07:00 en Bangkok). Para un producto tailandés,
un límite "40 USDC hoy" se reinicia a las 7 de la mañana hora local, no a medianoche.
No es una vulnerabilidad, pero el límite diario no significa lo que el usuario
cree. Además el cálculo llama a `time.time()` dos veces, que es feo pero inocuo.

**Fix**: `datetime.now(timezone.utc).replace(hour=0,...)` o mejor, zona del
usuario, y cachear `now` en una variable.

## 🟡 MEDIO — `dual_threshold = 0` desactivaría el control dual

Mismo patrón que el primero: `user.get("dual_threshold") or 1e12`. Con
`dual_threshold = 0`, *ningún* pago pediría segunda aprobación. Para un producto
que se vende por "nada se mueve sin un sí", es el fallo más serio de los dos.

## ✅ Revisado y OK

- **No hay endpoint público para crear contactos.** `add_contact` solo se llama
  desde `_seed_for` (api.py:712-716) y todos apuntan a `DEMO_RECIPIENT`. Un
  atacante no puede inyectar una wallet arbitraria como destino. `send_usdc` no
  valida el checksum, pero tampoco hay por dónde meter una dirección.
- **`/approve` sí verifica que quien aprueba es el co-signer configurado**
  (api.py:591) — `403` si no.
- **Fee del receptor**: `send_usdc` manda `amount` completo al receptor y el fee
  va en una segunda transferencia aparte, así que el destinatario no paga la
  comisión. Correcto.
- **Límite de agent_query** ya arreglado en el commit del IDOR
  (`UBOT_AGENT_MAX_USDC`).

## Orden de arreglo

1. `daily_limit = 0` debe bloquear, no permitir. Una línea, riesgo alto.
2. Re-evaluar `check_policy` en `/approve` + contar pendientes en `spent_today`.
3. `dual_threshold = 0` debe exigir co-signer, no omitirlo.
4. Día local en vez de UTC midnight (cosmético pero confuso).