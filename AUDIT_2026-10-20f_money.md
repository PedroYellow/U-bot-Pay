# Audit — 20 Oct 2026, sexta ronda (dinero: control dual, carreras, push)

Ronda sobre las tres cosas que mueven o protectan dinero fuera del camino ya
auditado: el gate de doble firma, las condiciones de carrera en `/approve`, y las
suscripciones push.

## 🔴 CRÍTICO — El control dual podía auto-aprobarse con un comando

`/cosigner` (tg.py) aceptaba cualquier entero sin validar:

```python
core.set_policy(user["id"], approver_telegram_id=int(parts[1]))
await update.message.reply_text(f"🔐 Co-signer set: {parts[1]}")
```

Y `/approve` sólo comparaba que el `approver_key` coincidiera con ese campo:

```python
if str(user.get("approver_telegram_id")) != str(a.approver_key):
    raise HTTPException(403, "not the approver")
```

**No comprobaba que fueran personas distintas.** Secuencia:

1. `/cosigner <mi propio telegram_id>`
2. Pido un pago de 25 USDC (encima del `dual_threshold`)
3. `/approve pay_abc` desde el mismo chat → `approver_key == approver_telegram_id` → **firma y envía**

El producto se vende por "nada se mueve sin un segundo sí". Un comando de
autonominación lo anula.

**Mitigación accidental**: el `daily_limit` de demo es 40 USDC, así que el techo
del abuso ahí es 40/día. Pero el `daily_limit` por defecto del esquema es 500
(core.py:14) — para un usuario real, el auto-aprobado es un bypass completo.

**Arreglado en dos capas** (ver abajo).

## 🔴 CRÍTICO — Carrera de doble gasto en `/approve`

El orden de operaciones era: leer → comprobar `status == 'pending_approval'` →
`await send_usdc(...)` → `mark('settled')`.

El `await` en medio significa que **dos `/approve` concurrentes pasan el check
antes de que ninguna escriba**. Ambas llaman a `send_usdc`. Con más de un worker
uvicorn es automático; con uno, dos peticiones simultáneas bastan.

Gasto doble del mismo pago, con las dos transferencias firmadas por la misma
clave de tesorería.

**Arreglado** con un compare-and-set atómico antes de cualquier `await`.

## 🟠 MEDIO — `save_push_subscription` permite robar la suscripción de otro

`push_subscriptions` tiene `UNIQUE(endpoint)`, y el `UPDATE` emparejaba sólo por
endpoint:

```python
existing = c.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
if existing:
    c.execute("UPDATE ... SET user_id = ?, ... WHERE id = ?", (user_id, ..., existing["id"]))
```

Un usuario autenticado que mande el endpoint push de otra persona **reescribe su
`user_id`** y se queda con la suscripción. Cross-user write: la víctima deja de
recibir notificaciones. No mueve dinero, pero es una escritura sobre datos de
otro usuario y era trivial de explotar (los endpoints push son adivinables si
alguien tiene acceso al navegador de la víctima).

## 🟡 MEDIO — `spent_today` no contaba `settling`

Bug que **introduje al arreglar la carrera**: el estado nuevo `settling` no
estaba en `_RESERVED_STATUSES`, así que un pago en proceso de liquidación liberaba
su presupuesto durante toda la llamada de red. Lo cazó el test de la carrera.

## 🟡 MENOR — Cosas revisadas que están bien

- **VAPID**: solo se expone la clave pública, nunca el subject (audit H6 ya lo
  hizo, y el comentario lo documenta).
- **`/push/*` exige sesión** para suscribir y desuscribir.
- **Telegram bloqueado en grupos** (`_require_private`).
- **El nonce de Telegram se rota tras usarse** — sin replay.

---

# ✅ Fixed

## 1. El control dual ya no se puede auto-aprobar

`/cosigner` valida ahora: que sea numérico, que **no seas tú mismo**, que el
nombrado exista como usuario del bot, y que no sea la misma cuenta.

`/approve` lo comprueba **también en el momento de liquidar**, así que cualquier
fila configurada antes del arreglo queda bloqueada igual:

```python
if user.get("telegram_id") is not None and str(user["telegram_id"]) == str(a.approver_key):
    raise HTTPException(403, "a payment cannot be approved by the person making it")
```

## 2. `/approve` reclama el pago de forma atómica

Nuevo estado `settling` y un compare-and-set:

```python
def claim_payment_for_settlement(pid):
    cur = c.execute("UPDATE payments SET status='settling' WHERE id=? AND status='pending_approval'", (pid,))
    return cur.rowcount == 1
```

Un solo `UPDATE` atómico decide el ganador. El perdedor recibe **409**. Y si el
claim tiene éxito pero luego el flujo aborta antes del `mark()`, el `finally`
devuelve el claim para que el pago no quede varado en `settling`.

## 3. La suscripción push ya no se puede robar

El `UPDATE` empareja por `(endpoint, user_id)`, y si el endpoint ya pertenece a
otro se rechaza con **409** en vez de reescribir el dueño.

## 4. `settling` reserva presupuesto

Añadido a `_RESERVED_STATUSES`.

## Tests

```bash
python3 test_dual_control.py   # 17/17
python3 test_approve_race.py   # 15/15
python3 test_push_hijack.py    # 16/16
```

El de la carrera lanza **8 hilos con barrera** contra el mismo pago y comprueba
que gana exactamente uno — es el escenario real, no una simulación.

Las nueve suites del proyecto pasan.

## Nota de despliegue

El estado `settling` es nuevo. No requiere migración (es un valor de texto, no una
columna), pero los pagos que ya estuvieran en `pending_approval` siguen siendo
aprobables con normalidad.