# Integración TronRental — diseño

Fecha: 21 Oct 2026. Precios y endpoints verificados contra la API en vivo, no
contra documentación.

## Qué es

**tronrental.com** — stand en TOKEN2049 Singapore (oct 2025), sede Dubai. Alquila
energía TRON para que las transferencias USDT-TRC20 cuesten ~80% menos en gas.
Infraestructura y API propias:

- API REST: `https://api.tronrental.com/v1`
- Docs: `https://docs.tronrental.com` (con traducción al español)
- MCP oficial: `@tronrental-com/mcp-server` (claude mcp add tronrental)
- Cuenta verificada en Crunchbase, perfil en Binance Square y LinkedIn

## Precios reales (consultados sin API key, 21 Oct 2026)

| Concepto | TRX | USD |
|---|---|---|
| 65.000 energía, 1h (destino **con** USDT) | 1.499 | $0.51 |
| 131.000 energía, 1h (destino **sin** USDT) | 2.819 | $0.95 |
| Sin energía (quemar TRX) | 6.5 | $2.20 |
| Coste de activación de dirección inactiva | 1.2 | $0.41 |
| Fee fijo por compra | 0.2 | $0.07 |
| TRX/USD | — | 0.338028 |

Ahorro declarado: 80%.

## Endpoints que nos interesan

Públicos, sin API key:

- `GET /v1/prices` — precio de mercado de 65.000 unidades y bandwidth
- `GET /v1/energy/quote?volume=&duration=` — **precio exacto para un volumen**, con
  `rental_trx`, `fixed_fee_trx`, `total_trx`, `total_usd`. Sin key devuelve precio
  de mercado; con key devuelve tu tarifa negociada.

Con API key (`X-API-Key`):

- `POST /v1/energy/buy` — compra energía (`target_address`, `volume`, `duration`)
- `POST /v1/invoices` — **factura de pago sin necesidad de cuenta para el usuario**
- `GET /v1/orders/{id}` — estado de la orden
- `GET /v1/deposit/address`, `GET /v1/balance` — fondeo

## Tres cosas que cambian el diseño

### 1. TronRental cobra en TRX, no en TRC-20

La hipótesis inicial era "acepta pagos TRC-20". No es así: la respuesta es
`price_trx` / `rental_trx`, y el flujo de fondeo es `get_deposit_address` → se
envía TRX. **Esto es buena noticia para el requisito de "sin bridge"**: no hace
falta mover nada entre Avalanche y Tron. Pero significa que el usuario necesita
TRX además de USDT, y que el TRX de Avalanche no sirve — Tron es cadena propia.

### 2. El endpoint de Invoices no tiene campo de comisión

El body documentado es solo `{target_address, energy_amount, transfer_count,
bandwidth_amount, duration}`. No hay `price`, ni `commission`, ni `platform_fee`.
El usuario paga **directo a tronrental.com** con lo que tenga.

Consecuencia: en el flujo de invoice, U-bot no está en medio del cobro y no puede
retener un 1%.

### 3. La energía va a una dirección TRON del usuario

`target_address` es una dirección TRON (base58, prefijo `T`, 34 caracteres,
checksum base58check). U-bot debe pedirla y validarla — nunca aceptar una `0x…`,
que es una dirección EVM y no es lo mismo.

## Arquitectura elegida

Facturación separada: el usuario paga a TronRental por su cuenta, U-bot le cobra
el 1% aparte contra su saldo U-bot.

```
Usuario                  U-bot                      TronRental
   │                       │                            │
   │ "necesito energía     │                            │
   │  para mi TRON addr"    │                            │
   ├──────────────────────>│                            │
   │                       │ GET /v1/energy/quote        │  (público)
   │                       ├───────────────────────────>│
   │                       │  total_trx, savings        │
   │                       │<───────────────────────────┤
   │                       │                            │
   │                       │ total × 1.01 = fee          │
   │  cotización           │                            │
   │<──────────────────────┤                            │
   │                       │                            │
   │  confirma             │                            │
   ├──────────────────────>│                            │
   │                       │ reserve fee (check_policy) │
   │                       │ create_payment(kind=energy_fee)
   │                       │                            │
   │  paga el fee en USDC  │                            │
   │  a U-bot              │                            │
   ├──────────────────────>│                            │
   │                       │ POST /v1/invoices ─────────>│
   │                       │<──── invoice + payment url ─┤
   │                       │                            │
   │  paga a TronRental ─────────────────────────────────>│
   │                       │                            │
   │                       │ GET /v1/orders/{id}        │  (polling)
   │  energía activada     │<──── status=completed ──────┤
   │<──────────────────────┤                            │
```

U-bot cobra el fee **antes** de crear el invoice. Si el usuario nunca paga a
TronRental, U-bot se queda el 1% por presentar y validar el precio — es el
modelo de un comparator/afiliado, y hay que decirlo en los términos.

## Piezas a construir

### 1. `energy.py` — cliente TronRental

- `quote(volume)` → precio real, con caché corta (30 s; los precios cambian)
- `create_invoice(target_address, volume)` → invoice
- `get_order(order_id)` → estado
- Validación de `target_address`: base58, prefijo `T`, 34 chars, checksum
  base58check válido. **Rechazar 0x… explícitamente.**
- Fail-closed: si la API de TronRental no responde, no se cobra fee ni se promete
  precio. Se dice "no pude cotizar".

### 2. Tablas nuevas

```sql
CREATE TABLE energy_orders (
  id TEXT PRIMARY KEY,
  user_id INTEGER,
  target_address TEXT,        -- TRON address del usuario
  volume INTEGER,             -- energía comprada
  duration TEXT DEFAULT '1h',
  tronrental_invoice_id TEXT,
  tronrental_order_id TEXT,
  quote_trx REAL,             -- precio real de TronRental
  fee_usdc REAL,              -- 1% convertido
  status TEXT,                -- quoted|awaiting_fee|invoice_created|activated|failed|expired
  tx TEXT,                    -- el pago del fee a U-bot
  created_at INTEGER, settled_at INTEGER
);
```

Reutiliza `payments` para el cobro del fee: así hereda `claim_payment_for_settlement`,
`check_policy` (límite diario) y el patrón de estados ya auditado. **No** escribir un
segundo sistema de pagos.

### 3. Endpoints

- `GET /energy/quote?target_address=&volume=` — precio real + fee + savings
- `POST /energy/order` — cobra el fee y crea el invoice
- `GET /energy/order/{id}` — estado
- `POST /energy/order/{id}/check` — consulta a TronRental si se activó

### 4. Riesgo de confianza

El fee se cobra **antes** de que exista confirmación de que TronRental activó la
energía. Mitigaciones:

- El fee es pequeño (1% de $0.51 ≈ $0.005) y el producto es explícito
- Se registra `tronrental_order_id` para poder reconciliar
- Si el invoice expira o falla, `/energy/order/{id}/check` lo refleja; política de
  reembolso a decidir con datos reales de uso

## Lo que NO hace esta integración

- **No toca TRX ni USDT de Tron.** U-bot no mueve fondos en Tron; solo cotiza y
  factura el fee en USDC sobre Avalanche, que es lo que ya sabe hacer.
- **No hace bridge.** Explícitamente fuera de alcance.
- **No verifica pagos en TRC-20.** No hay RPC de Tron fiable (verificado: los
  públicos no responden), y no se necesita para el flujo de invoice.

## Decisión pendiente

1% de $0.51 son **$0.005** — medio céntimo. El fee fijo de TronRental son $0.07.
El 1% no cubre ni un sello de postage. Recomiendo subir el fee al 10% o añadir un
mínimo por operación, pero eso es decisión de negocio, no técnica.