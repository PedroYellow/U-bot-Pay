# VeChain — por qué no se ha integrado

Pediste añadir Sonic, VeChain, Solana y Tron. **Sonic está integrada y verificada**
(`336e24e`). VeChain no, y quiero explicarte exactamente por qué en lugar de
escribir código que fallaría en silencio.

Fecha: 20 Oct 2026. Todo verificado contra las APIs y RPCs reales, no contra
documentación.

---

## Tres bloqueantes, independientes

### 1. No hay ningún RPC público que responda

Los cuatro RPC públicos documentados por VeChain fallan, y no es un problema de
mi IP ni de User-Agent — lo probé desde dos entornos distintos:

```
rpc.vechain.org          → DNS no resuelve
sync-mainnet.vechain.org → 307 → /doc/stoplight-ui/  (solo sirve la documentación)
mainnet.vechain.org      → 307 → /doc/stoplight-ui/
```

Y con `POST /rpc` explícito, los que resuelven devuelven **403 Forbidden**:
solo están sirviendo las páginas de docs.

Busqué alternativas:

| Proveedor | Resultado |
|---|---|
| `vechain-mainnet.drpc.org` | "Unknown network" |
| `rpc.ankr.com/vechain` | 403 — requiere API key |
| `vechain.api.onfinality.io/public` | no resuelve |
| `vechain-rpc.publicnode.com` | 404 |
| `1rpc.io/vechain` | "Unknown network" |
| `vechain-mainnet.gateway.tatum.io` | 404 |

**Sin RPC no hay integración**: `chain.balance_at()` necesita un endpoint, y sin
él la chain aparece en la UI y no funciona. Peor que no aparecer.

### 2. Ningún motor de bridge cubre VeChain

Lo comprobé en los tres proveedores que usa el producto:

- **LI.FI** — rechaza los tres chain ids posibles con *"toChain must be equal to
  one of the allowed values"*. No está en su lista de 65 chains.
- **Stargate** — no menciona VeChain en su configuración.
- **Across** — no menciona VeChain.

Añadir VeChain al registry haría que el cotizador lanzara `"unsupported target
chain"`, que es exactamente el fallo que me encontré al añadir Sonic.

### 3. Discrepancia de chain ID

Esto lo encontré de paso y habría causado un bug silencioso:

- **1048585** (`0x100009`) — el id nativo de VeChainThor. Es lo que responde la
  cadena real.
- **818** — el id "EVM-compatible" que usan MetaMask y los bridges.

Si se registra con 818 y se manda a firmar, `web3.py` rechaza la transacción
porque `chainId` no coincide con la red. Y si se registra con 1048585, LI.FI no
la reconoce. Hay que decidir cuál usar *y* traducir en el sitio correcto.

### 4. No hay USDC nativo

VeChain tiene VeUSD (estable propia) y USDC bridged. El producto asume USDC con 6
decimales en todas las chains. Añadir USDC bridged significa que el usuario recibe
un token que puede despegarse — decisión de producto, no técnica.

---

## Qué haría falta para sí integrarla

1. **Un RPC.** El oficial requiere API key (gratis, pero hay que registrarse).
   Con eso, el resto del trabajo de lectura de balances es idéntico al de Sonic:
   una entrada en el registry más la dirección del token, verificada on-chain.
2. **Un motor de bridge.** Ninguno de los tres que usa el producto cubre VeChain.
   Habría que integrar StargateV2 o Symbiosis desde cero, o editar el producto
   para que un chain sin bridge solo sirva lectura de saldo.
3. **Resolver el chain ID** y el USDC bridged.

El punto 2 es el caro: no es añadir una chain, es añadir un proveedor de
integridad nuevo y mantenerlo.

---

## Recomendación

**No la integro ahora.** Añadirla sin RPC y sin bridge sería exactamente el tipo
error que el resto de este trabajo ha intentado evitar: una chain que aparece
en el dropdown y falla al usarla.

Cuando quieras retomarla, el orden sería:

1. Consigue un RPC con API key → con eso, la lectura de saldos son 30 minutos.
2. Decide USDC bridged vs solo VeUSD → es una decisión de producto tuya.
3. Evalúa StargateV2 o Symbiosis como proveedor → esto es lo que decide si vale
   la pena.

Si en algún momento quieres **solo lectura de saldos** en VeChain (ver cuánto
tienes en esa chain, sin poder enviar ni cotizar bridges), eso sí es barato: con
un RPC válido, es una entrada en el registry y ya. Dímelo y lo hago.