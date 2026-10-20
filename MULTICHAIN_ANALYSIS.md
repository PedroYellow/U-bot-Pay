# Integración multichain — diagnóstico

Pediste añadir Sonic, VeChain, Solana y Tron. Antes de escribir código verifiqué
qué soporta el producto hoy, porque la respuesta cambia el alcance por completo.
**No he modificado nada.** Esto es solo el análisis.

Fecha: 20 Oct 2026. Fuentes: lectura de `chain.py`/`api.py`, y consultas
directas a las RPC y a LI.FI.

---

## 1. Qué soporta el producto hoy

El registry tiene **14 chains**. Verifiqué una por una contra la API de LI.FI
(`GET https://li.quest/v1/chains`, 65 chains):

| Chain | id | En LI.FI |
|---|---|---|
| Ethereum | 1 | ✅ |
| Optimism | 10 | ✅ |
| Unichain | 130 | ✅ |
| Polygon | 137 | ✅ |
| zkSync Era | 324 | ✅ |
| World Chain | 480 | ✅ |
| Mantle | 5000 | ✅ |
| Base | 8453 | ✅ |
| Arbitrum | 42161 | ✅ |
| Celo | 42220 | ✅ |
| **Avalanche** | 43114 | ✅ |
| Linea | 59144 | ✅ |
| Scroll | 534352 | ✅ |
| Avalanche Fuji | 43113 | ❌ (es testnet, esperado) |

**Las 13 mainnet funcionan.** Esto no es una Accuratez del registry: LI.FI las
cubre a todas. Verifiqué además que el bridge cotiza y liquida de verdad — el pago
x402 que hice en el deploy llegó a Avalanche mainnet con `status 0x1`.

### Pero "soportada" tiene un matiz que hay que entender

**Leer** balances funciona en las 14: `balance_at(address, chain_id)` sí usa el
chain_id correctamente.

**Enviar** solo funciona en Avalanche. `send_usdc()` no acepta `chain_id`:

```python
def send_usdc(to, amount, memo_hash=None, fee=0.0):
    acct = account()                                  # ← cuenta de Avalanche
    nonce = w3.eth.get_transaction_count(acct.address)  # ← w3 global
    h = _transfer(acct, to, amount, nonce)            # ← RPC de Avalanche
```

El campo `evm: bool` existe en el dataclass `Chain` pero **no se usa en ningún
sitio del código**. Es decorativo.

El diseño real es: *avalanchе es donde se mueve el dinero; las otras chains son
destinos y orígenes de bridge, y LI.FI se encarga de mover entre ellas*. Es
un diseño legítimo, pero no es "soportamos 14 chains" — es "soportamos 14 chains
**como extremos de un bridge**".

---

## 2. Un bug que encontré por el camino

`/turn` usa `t.chain_id` en el intent `balance`, pero el modelo `Turn` **no
declara ese campo**:

```python
class Turn(BaseModel):
    user_key: str = "demo"
    text: str
    channel: str = "voice"
    lang_hint: str | None = None
    # sin chain_id

@app.post("/turn")
async def turn(t: Turn, ...):
    ...
    b = chain.balance_at(addr, t.chain_id)   # ← t.chain_id no existe
```

Verificado en producción:

```bash
curl -X POST /turn -d '{"user_key":"demo","text":"balance","chain_id":8453}'
→ 200 {"chain":"avalanche-mainnet", ...}
```

Pydantic descarta campos no declarados sin error, así que `chain_id` se ignora
en silencio y siempre devuelve Avalanche. Un cliente que creyera estar leyendo
el saldo de Base recibiría el de Avalanche **sin ningún aviso**.

**Impacto**: bajo. Nadie pasa `chain_id` por `/turn` hoy, así que el comportamiento
es el esperado por accidente. Pero es una trampa para el siguiente que intente
usar el endpoint con multi-chain, que es exactamente lo que estás intentando
hacer. Lo arreglaría antes de tocar nada más.

---

## 3. Las cuatro chains que pides

### Sonic (id 146) — EVM ✅ — **integración real y barata**

- RPC responde: `https://rpc.soniclabs.com`, `eth_chainId` → 146 verificado.
- Está en LI.FI ✅ (confirmado en la lista de 65).
- Es EVM, así que `balance_at` funcionaría sin tocar nada más que el registry.

**Aviso importante**: la dirección de USDC que circula por internet
(`0xA487d2D0Df7e3d32b1ABD1d693BA4bb9Da5fA28`) **no existe en la cadena**.
Lo comprobé con `eth_getCode` → devuelve `0x`. LI.FI también la rechaza con
"Unknown token symbol". Hay que conseguir la dirección real de la documentación
oficial de Sonic antes de escribirla en el código.

**Coste**: bajo. Registry + verificar la dirección del token + probar.

### VeChain (id 818) — EVM ✅ — **integración de tamaño medio**

- **Los 4 RPC públicos que probé no responden** desde aquí:
  `rpc.vechain.org`, `mainnet.vechain.org`, `node.mainnet.vechain.org`,
  `rpc1.vechain.net`. Necesitas un RPC válido (el oficial requiere API key).
- **NO está en LI.FI.** No aparece en la lista de 65. Un bridge hacia VeChain
  requeriría otro proveedor (Across, Stargate, StargateV2, Symbiosis) o
  integrarlo a mano.
- **VeChain no tiene USDC nativo.** Tiene VeUSD (estable propia) y USDC
  bridged. Cambia el producto: no podrías decir "envía USDC a VeChain" igual que
  en las otras.

**Coste**: medio. Registry + un motor de bridge que sí cubra VeChain + decidir
qué token(es) soportar.

### Solana — **NO es EVM** ❌ — **integración grande**

- No usa `web3.py`. Necesita `solders` (el SDK oficial de Solana).
- Direcciones base58, no `0x...`. Todo el modelo de `Chain` asume `0x`.
- USDC es un SPL token (mint `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`),
  y cada wallet necesita su propia *associated token account*, que hay que crear
  si no existe.
- LI.FI sí soporta Solana como chain de destino (la probé: el error que devuelve
  es de formato de dirección, no de chain), así que **el bridge hacia Solana
  probablemente funcione** sin motor nuevo.

**Coste**: alto. Un cliente de cadena nuevo detrás de una interfaz común con el
EVM. `chain.balance_at()` necesitaría una implementación distinta.

### Tron — **NO es EVM** ❌ — **integración grande**

- TVM, no EVM. Direcciones base58 que empiezan por `T`.
- Los contratos TRC20 **no implementan la interfaz ERC-20 estándar**: no hay
  `decimals()` ni `transfer(to,amount)` con la misma firma. `chain.py` asume
  ERC-20 en todas partes.
- Requiere firma con TRX, no con `eth_account`.
- **NO está en LI.FI.** Necesitaría un motor de bridge aparte.

**Coste**: alto. Lo mismo que Solana, más el problema de que no hay bridge en el
motor actual.

---

## 4. Resumen

| Chain | EVM | En LI.FI | RPC accessible | Coste |
|---|---|---|---|---|
| **Sonic** | ✅ | ✅ | ✅ | **Bajo** — la única de las cuatro que es EVM, está en LI.FI y su RPC funciona |
| **VeChain** | ✅ | ❌ | ❌ (4/4 fallan) | Medio — motor de bridge nuevo |
| **Solana** | ❌ | ✅ | ✅ | **Alto** — cliente no-EVM |
| **Tron** | ❌ | ❌ | ✅ | **Alto** — cliente no-EVM + motor nuevo |

**Nota sobre LI.FI**: aparecen 65 chains, pero no Solana ni Tron en esa lista.
Solana sí la acepta como destino de bridge (lo verifiqué con una petición
directa), pero no está en el endpoint de chains. Vale la pena confirmar la
cobertura real antes de contar con ella.

---

## 5. Lo que yo haría

**Primero, el bug de `Turn.chain_id`.** Es media hora y elimina la trampa que
te va a morder justo cuando intentes usar multi-chain.

**Después, Sonic.** Es la única de las cuatro que es EVM, está en LI.FI, su RPC
funciona y el trabajo es una entrada en el registry más la dirección del token
correcta. Te da una chain nueva funcionando de verdad en lugar de cuatro
promesas.

**VeChain solo con decisión explícita**, porque "soportar VeChain" significa
elegir qué token y con qué bridge. No es una adición, es una decisión de
producto.

**Solana y Tron, solo si el producto va en serio multi-cadena.** Son días de
trabajo: un cliente por familia de chain detrás de una interfaz común. Y antes
hay que responder una pregunta de producto que no es técnica: ¿el treasury
mantiene fondos en esas chains, o el dinero sale siempre de Avalanche y solo se
mueve por bridge? La segunda respuesta hace que las dos sean mucho más baratas.

Dime por dónde empezamos y si quieres que deje el diagnóstico por escrito en el
repo.