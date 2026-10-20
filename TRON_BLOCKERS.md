# Tron — por qué no se ha integrado

Pediste añadir Sonic, VeChain, Solana y Tron. **Sonic y Solana están integradas y
verificadas** (`336e24e`, `6c3c030`). Tron no, y el motivo no es técnico: es que en
Tron no hay USDC.

Fecha: 20 Oct 2026. Todo verificado contra LI.FI, no contra documentación.

---

## Lo que sí funciona

LI.FI reconoce Tron con el chain id **`728126428`** (no aparece en su lista de 65
chains, pero las cotizaciones funcionan). La transacción que devuelve es **EVM
normal** — `chainId 43114`, se firma con MetaMask — igual que con Solana. El mismo
razonamiento de "solo destino" aplica.

Así que el bloqueo técnico de Tron no existe. El problema es otro.

---

## El bloqueo: no hay USDC en Tron

El producto entero asume USDC:

- `chain.USDC_BY_CHAIN` y `bridge.USDC_BY_CHAIN` — siempre USDC
- `/bridge/quote` — `amount_usdc`
- `/pricing`, `/dashboard` — tesorería en USDC
- x402 — pagos en USDC

En Tron, el token líquido es **USDT**. Consulté los 1209 tokens que LI.FI conoce
en Tron. Los que tienen symbol USDC son tres, todos wrappers de bridges:

```
TR8uUYpffcPib4NioKAs81LZxUF98CgHYs   18 decimales
TQkCviLATL42z5ZS4FBiueMz4zXrUeYJih    6 decimales
TYRBPWof3rGijKgZQRyJqyh6Lz4XMU8Zew   18 decimales
```

**Ninguno de los tres devuelve cotización.** Probé los tres contra LI.FI y la
respuesta es siempre *"No available quotes for the requested transfer"*. Son tokens
sin liquidez o rutas de bridge.

Los 1206 restantes son USDT, casi todos wrappers de bridges distintos — el mismo
problema que con los USDC.

---

## Y aunque funcionara, el número es malo

La única ruta que cotiza a Tron es **USDC → USDT vía Layerswap**:

```
10 USDC (Avalanche) → 7.681241 USDT (Tron), 77 s, Layerswap
```

El usuario **pierde un 23%** y encima recibe **otro token** del que pidió. No es un
bridge: es una conversión con una pérdida grande y un cambio de divisa que nadie le
ha pedido.

Comparado con las otras chains integradas:

| Destino | Recibe | Pérdida |
|---|---|---|
| Base | 9.9948 USDC | 0.05% |
| Sonic | 9.975 USDC | 0.25% |
| Solana | 9.975 USDC | 0.25% |
| **Tron** | **7.68 USDT** | **23%** |

Ese 23% es la diferencia entre "bridge" y "trampa".

---

## Por qué no lo implementé «con USDT»

Habría sido fácil añadirlo: una entrada más en los dos mapas y el mismo guard de
solo-destino. Funcionaría, y por eso es peligroso.

El usuario pediría "envía 10 USDC a mi dirección de Tron" y recibiría 7.68 USDT sin
haberlo aceptado. El endpoint no distingue; el `amount_usdc` de la API promete USDC.
Sería la clase de decisión que un banco no toma sin avisar.

### Detalle de validación de direcciones

La dirección de destino de Tron es base58 con prefijo `T` y checksum base58check.
La de Solana es una clave de 32 bytes. El regex actual de api.py
(`^[1-9A-HJ-NP-Za-km-z]{32,44}$`) comprueba longitud y alfabeto, no bytes decodificados
ni checksum — así que **una dirección de Tron pasada a un destino Solana pasa la
validación** y solo falla después, en LI.FI, como un 502 genérico en vez de un 400
claro.

No hay pérdida de fondos en ese camino (LI.FI rechaza antes de mover nada), pero es
un error poco claro. Un validador de checksum base58 de verdad resolvería los dos
ecosistemas de una vez; no lo he añadido porque `base58` no está instalado y no
quiero meter una dependencia nueva por una validación de entrada. Queda como
limitación conocida.

Al añadir Tron de verdad habría que distinguir TRC-20 de Solana explícitamente:
prefijo `T` + 21 bytes para Tron, 32 bytes para Solana.

---

## Qué haría falta para integrarlo

1. **Decisión de producto:** ¿el producto puede mover USDT, o solo USDC? Si solo
   USDC, Tron queda fuera por definición y este documento es el cierre.
2. **Si acepta USDT:** una ruta USDC→USDC en Tron cuando exista (CircleNative no
   ha issuing en Tron; bridged USDC no tiene liquidez). Hoy no la hay.
3. Validación de direcciones TRC-20 con checksum base58check y prefijo `T`, que es
   un esquema distinto del que ya hay para Solana.

Mi recomendación es **no integrar Tron** salvo que el producto pase a ser
multi-estable. Cuando lo haga, la estructura ya está lista: mismo patrón que
Sonic y Solana, más la validación de direcciones TRC-20.