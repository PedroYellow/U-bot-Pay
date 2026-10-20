# Audit — 20 Oct 2026, octava ronda (DataAgent / x402 receptor)

Ronda sobre el otro lado del x402: el servicio que **recibe** el pago. Es la
contraparte de la ronda 7, que endureció el cliente.

## 🔴 CRÍTICO — La protección anti-replay vivía en memoria y se perdía al reiniciar

```python
_seen_nonces: set[str] = set()        # dataagent.py:28
```

Dos consecuencias, ambas serias:

**1. Se borra en cada reinicio.** Una autorización EIP-3009 sigue siendo válida
hasta su `validBefore`. Cualquier autorización capturada dentro de esa ventana
podía reutilizarse después de un deploy o un crash. Con el techo de validez que
puse en la ronda 7 (`min(timeout+540, 1800)`), la ventana máxima de replay es de
30 minutos por reinicio. **Antes de ese arreglo era mucho peor**: un servicio
malicioso podía pedir `validBefore` muy lejano y la autorización era
reutilizable indefinidamente con solo reiniciar el proceso.

**2. El nonce se registraba DESPUÉS de liquidar.** Con dos peticiones
concurrentes llevando la misma autorización, ambas pasaban `verify()` —porque el
set todavía no la contenía— y ambas llamaban a `settle_authorization`. Doble
liquidación del mismo pago.

**Arreglado**: store en SQLite, reclamo atómico *antes* de tocar la cadena, y
rollback si el settlement falla para que el pagador pueda reintentar.

## 🟠 MEDIO — `/health` del DataAgent exponía el saldo del payee, sin auth

Verificado en producción antes de arreglarlo:

```
GET https://agent.ubotpay.com/health
{"ok":true,...,"payee":"0xebF8BA0ba9e9e06ee6451C924b917a57dacB330A","payee_usdc":0.5,...}
```

Es exactamente el mismo tipo de fuga que el treasury de `/dashboard` que arreglé
en la ronda 1 — y que aquí seguía sin arreglar, lo cual deja el trabajo
inconsistente. Ahora el saldo requiere `DATAAGENT_API_KEY`.

**Detalle que salió al verificar**: el `payTo` del DataAgent
(`0xebF8BA0b...`) es la **misma wallet** que la que aparece como `revenue_wallet`
en `/pricing`. O sea, el cobro del servicio de research y las comisiones de
transferencia van a la misma dirección. Puede ser intencionado (es una demo), y
merece una decisión consciente.

## 🟡 MENOR — `verify()` decía a qué dirección pertenecía la firma falsificada

```python
return False, f"bad signature (recovered {signer})"
```

Un atacante que falsificara una firma recibía gratis la dirección a la que
pertenecía realmente. Ahora el mensaje es genérico y el detalle va al log.

El `except` también devolvía `f"verify error: {e}"` al cliente. Ahora genérico.

Igual con el error de settlement, que devolvía `str(e)`.

## 🟡 MENOR — `/report` sin rate limit

Cada `GET` sin `X-PAYMENT` devuelve 402 (barato). Cada `GET` con pago válido cobra
0.5 USDC **y llama a la LLM** (`make_report` → Anthropic). No hay límite.

Con el nonce atómico ya no se puede liquidar dos veces la misma autorización, pero
sí se pueden agotar los tokens de la LLM con autorizaciones distintas. **No lo he
arreglado** — el impacto real depende de qué cuota de Anthropic tengas y prefiero
no inventar un límite que rompa la demo. Es una decisión tuya.

## 🟡 OBSERVACIÓN — El DataAgent usa la private key del U-bot

`settle_authorization` viene de `chain.py`, que usa `UBOT_PRIVATE_KEY`: la misma
tesorería que paga. El docstring lo llama "relayer (U-bot treasury pays gas)".

Es coherente con que sea una demo de hackathon — el "servicio de pago
independiente" y el pagador son la misma wallet. Pero significa que **no estamos
demostrando un pago entre partes reales**, solo el mecanismo x402. Vale la pena
decirlo así en el pitch en vez de sugerir que hay dos entidades.

## ✅ Revisado y OK

- **El nonce se registra tras el settlement en el diseño original**, para que un
  fallo de red no queme la autorización. Ese criterio era correcto; el problema
  era que la ventana entre `verify()` y el registro era explotable. Mi arreglo
  reclama antes pero **libera si falla**, que conserva la intención original.
- **El EIP-712 se construye con los tipos canónicos** y `Account.recover_message`
  verifica la firma.
- **`to`, `value` y `validBefore`** se validan contra lo esperado antes de firmar.
- **El `.db` del nonce está cubierto por `.gitignore`** (`*.db`).

## Nota de despliegue

`DATAAGENT_NONCE_DB` (opcional, por defecto junto al código). Y
`DATAAGENT_API_KEY` si quieres ver el saldo desde `/health`.

## Test

```bash
python3 test_dataagent_nonce.py   # 18/18
```

Incluye 6 hilos con barrera reclamando el mismo nonce → exactamente uno gana, un
"restart" simulado (caché en frío) que demuestra que el nonce persiste, y el
rollback tras un settlement fallido.

Las once suites del proyecto pasan.