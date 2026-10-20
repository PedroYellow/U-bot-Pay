# Audit — 20 Oct 2026, décima ronda (documentación y arranque)

Ronda sobre lo que alguien que clona el repo se encuentra. Diez rondas de
cambios después, la documentación había quedado atrás y describía un producto que
ya no es el que está en el código.

## 🔴 ALTO — El README configuraba mal el webhook de voz

La sección "ElevenLabs Agent setup" decía:

> **URL:** `https://ubotpay.com/turn` · header `x-api-key`

Dos cosas mal:

1. **La URL** es `ubotpay.com`, no `api.ubotpay.com`. El vhost de `ubotpay.com`
   sirve ficheros estáticos; el API está en el subdominio.
2. **El header.** En la ronda 2 separé el secreto de voz del API key
   (`UBOT_VOICE_SECRET` / `x-ubot-channel`) precisamente para que la voz se
   pudiera revocar sin tocar Telegram. El README seguía con `x-api-key`, que
   ahora **da 403**: una sesión no puede actuar como otro usuario.

Quien siguiera el README configuraría el widget de voz y obtendría 401 en cada
turno, sin ninguna pista de por qué.

Corregido, con una advertencia explícita de qué **no** usar y qué significa cada
modo de autenticación (incluido que el webhook de voz queda pineado al usuario
demo y en read-only).

## 🟠 MEDIO — El README describía endpoints que ya no son así

| Afirmación | Realidad |
|---|---|
| `GET /health` → "Chain + treasury status" | Devuelve solo `{ok, ts}`. Chain y status vienen en las cabeceras `X-Chain`/`X-Status`. El balance va en `/health/full`, con key. |
| `/balance/{user}` · `/history/{user}` · `/invoices/{user}` | No existen. Son `/balance`, `/history`, `/invoices` y exigen key. |
| `POST /agent/query` → "U-bot hires DataAgent and pays it via x402" | **Devuelve 503.** Deshabilitado en el audit del 15 Oct (finding #2) y el README nunca lo menciona. |
| "`Ubot` parses…" | El host de Ubot no resuelve. Ahora hay parser de tres niveles. |
| Ejemplo de respuesta de `/turn` con `zero fees` | El texto real dice `no fee`. |

Reescrita la tabla de endpoints con la columna de autenticación real
(`—` / key / sesión / voz), verificada endpoint por endpoint contra el código.

## 🟠 MEDIO — `UBOT_JWT_SECRET` vacío degradaba en silencio

`.env.example` documentaba la variable y la dejaba vacía. El efecto de que esté
vacía es que **cada arranque genera un secreto nuevo**, invalidando todas las
sesiones y todos los JWT emitidos. El servicio arrancaba igualmente, con un solo
`log.warning` que nadie ve en un `systemctl restart` que "funciona".

Ahora hay checks de arranque: `UBOT_API_KEY` es fatal (ya lo era), y los secretos
opcionales avisan con la consecuencia **y el comando para arreglarlo**:

```
UBOT_JWT_SECRET is not set — generates a random secret PER BOOT — every restart
invalidates all sessions and JWTs. Generate with: openssl rand -hex 32
```

Probado en los tres escenarios: vacío → fatal, solo API key → arranca con
avisos, completo → limpio.

## 🟡 MENOR — La sección de features prometía cosas que el código no hacía

- "Nothing moves without a spoken *yes*" → ahora además el co-signer **nunca**
  puede ser el pagador (ronda 6). La promesa original era más débil de lo que
  creíamos.
- "Avalanche C-Chain (Fuji)" comochainsaugmentation → es mainnet, Fuji es para
  tests.

## ✅ Revisado y OK

- **Las referencias restantes a Fuji son correctas**: una dice explícitamente
  "mainnet; Fuji for tests" y la otra es el enlace a la faucet de testnet.
- **`run_tests.sh` sigue en verde** después de tocar `api.py`.
- El ejemplo de `curl` de `/demo/seed` tenía una comilla sin cerrar en el README;
  corregido al reescribir el bloque.

## Lo que este tipo de bug cuesta

No es un agujero de seguridad — es que **la documentación miente sobre el estado
del producto**. En un hackathon, la primera persona que lea el README y monte el
widget de voz se va a encontrar un 401 sin explicación, y va a perder veinte
minutos depurando algo que ya está resuelto en el código.

## Test

Las once suites siguen pasando. No hay test nuevo en esta ronda: los cambios son
de documentación y de un check de arranque, que verifiqué ejecutando la lógica en
las tres configuraciones.