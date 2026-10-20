# Audit — 20 Oct 2026, novena ronda (despliegue, dependencias, tests)

Ronda sobre las cosas que hacen que el código correcto llegue a producción.

## 🔴 ALTO — El script de deploy no desplegaba el sitio

`deploy_web.sh` copiaba cuatro ficheros: `index.html`, `agent.html`, `ubot.css`,
`i18n.js`. Todo lo demás que sirve el navegador — `sw.js`, `manifest.json`,
`analytics.html`, los cuatro iconos de la PWA y `security.txt` — **no estaba en
el script**.

Esos ficheros están vivos en producción, pero solo porque alguien los subió a mano
alguna vez. El camino de deploy no podía reproducir el sitio: un `git clone`
limpio + `bash deploy_web.sh` dejaba la PWA rota.

Prueba concreta, medida antes de arreglarlo:

```
GET https://ubotpay.com/security.txt   ->  200, 93469 bytes
GET https://ubotpay.com/index.html     ->  200, 93469 bytes
```

El `security.txt` que escribí en la ronda 5 **no está en el servidor**. Lo que
devuelve es el `index.html` del fallback SPA, con un 200 que dice "tenemos
security.txt" a quien lo compruebe.

**Arreglado**: manifiesto explícito de los 12 assets, verificación posterior de que
cada uno está en destino, y el destino parametrizable para poder probarlo.

## 🟠 MEDIO — El check de secretos del deploy era un falso positivo

Al reescribir el script incluí `x-api-key` en el patrón de detección. El primer
run real **abortó sobre un deploy correcto**: `sw.js:68` contiene `x-api-key` en
una comprobación defensiva del propio service worker (no cachear peticiones que
traigan esa cabecera). Es el *nombre* de una cabecera, no un secreto.

Un check que falla siempre es un check que la gente acaba desactivando. Ahora
detecta un **valor** asignado (`"x-api-key": "..."`), no la mención.

Probado en tres escenarios: deploy limpio → OK; falta un asset opcional → avisa
y sigue; secreto real inyectado → aborta con la línea exacta.

## 🟡 MEDIO — `requirements.txt` sin versiones fijadas

```
web3>=7     fastapi     uvicorn     httpx     anthropic
apscheduler     pyyaml     python-multipart
python-telegram-bot==21.*     ← el único acotado
```

Un redeploy es `git pull && pip install -r requirements.txt && systemctl
restart`. Una major nueva de cualquiera de esas librerías entra en producción sin
que nadie la haya elegido, y el servicio deja de importar.

Acotado `web3>=7,<8` (la librería con más superficie de breaking change: `Web3()`
sin provider, los typed dicts de `encode_typed_data`). **El resto no lo he
fijado** — hacerlo exige leer las versiones reales del venv del VPS, que no puedo
acceder. El fichero lleva el comando exacto para hacerlo y explica qué hacer
mientras tanto.

## 🟡 MENOR — Los tests eran once scripts sin runner

Las once suites que escribí son ficheros sueltos. "¿Pasan los tests?" significaba
recordar once nombres de fichero. Añadido `run_tests.sh`.

Ha encontrado un problema al usarlo: `test_brain.py` (el original del 15 Oct)
**lee `/opt/ubotpay/.env` en tiempo de import**, así que revienta fuera del VPS.
Está superseded por `test_brain_fallback.py`; el runner lo excluye por nombre y
lo explica.

## ✅ Revisado y OK

- **README documenta la instalación** correctamente (venv + `pip install -r`).
- **`ubotpay.service`** usa `EnvironmentFile=/opt/ubotpay/.env` y `Restart=always`
  con `RestartSec=5`.
- **`set -euo pipefail`** estaba ya en el script original — por eso falló limpio
  cuando le pedí `/var/www` sin permisos, en vez de seguir y fingir éxito.
- **`.gitignore`** cubre `*.db`, así que las bases de datos de los tests y el
  nonce store del DataAgent no se commitean.

## Nota

No hay CI (`.github/workflows/` no existe). Con once suites que corren sin red,
sin cadena y sin credenciales, un workflow de cinco líneas las ejecutaría en cada
push. No lo he añadido porque decidir la política de cuándo falla CI es decisión
tuyo, no mía — pero la infraestructura está ahí.

## Test

```bash
./run_tests.sh        # 11 suites passed
./run_tests.sh policy # solo las que coincidan
```