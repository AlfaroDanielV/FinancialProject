# Apple Pay → captura sin contacto (configuración)

Registrá tus compras **sin contacto con Apple Pay** en Ledger CR sin tocar nada:
cada vez que pagás acercando el iPhone/Apple Watch a un datáfono, la compra
(monto + comercio) entra sola a tu ledger y se descuenta de tu **Disponible** al
instante.

> **Importante.** El disparador sólo se activa con **toques físicos NFC** (pagar
> acercando el teléfono). NO cubre Apple Pay en webs/apps ni los pagos con chip o
> banda. Para todo lo demás sigue mandando el **correo del banco** (Gmail), que es
> la fuente de verdad. Cuando el correo del banco llega después, se junta solo con
> la compra que ya registró Apple Pay — **nunca se duplica**.

## Requisitos

1. **Build de EAS / TestFlight** de Ledger CR (no funciona en Expo Go — necesita
   código nativo).
2. Estar **logueado** en la app (si no, corré `/login` en Telegram y pegá el
   código).
3. **Datos móviles de Wallet activados**: Ajustes → Cartera y Apple Pay → activá
   los datos móviles, para que la automatización pueda mandar la compra aunque no
   estés en Wi-Fi. Si estás sin señal, se reintenta solo apenas haya internet.

## Crear la automatización (iOS 26)

1. Abrí la app **Atajos** (Shortcuts).
2. Andá a la pestaña **Automatización** → tocá **+** → **Crear automatización
   personal** (o "Nueva automatización").
3. Buscá y elegí el disparador **Cartera** (en inglés "Wallet"; en iOS más viejo
   aparece como "Transacción").
4. Dejá que se active con **cualquier tarjeta** (o elegí una específica si querés
   registrar sólo esa) y tocá **Siguiente**.
5. Tocá **Agregar acción** y buscá **"Registrar compra de Apple Pay"** (la acción
   de Ledger CR).
6. Conectá los campos:
   - **Monto** → el **Monto** de la transacción (Shortcut Input / "Amount").
   - **Comercio** → el **Comercio** de la transacción ("Merchant").
   - **Tarjeta** → opcional (dejalo vacío o conectá el nombre de la tarjeta).
7. Tocá **Siguiente** y dejá:
   - **Ejecutar de inmediato**: **SÍ**.
   - **Notificar al ejecutar**: **NO** (así es 100% sin tacto, sin avisos).
8. Tocá **Listo**.

## Cómo verificar que quedó bien

- Hacé una compra chica acercando el teléfono al datáfono.
- En segundos debería bajar tu **Disponible** en la pantalla de Inicio y aparecer
  el movimiento en **Movimientos** (origen "apple_pay").
- Cuando llegue el correo del banco de esa misma compra, el movimiento se queda
  **uno solo** (no se duplica): la app lo reconcilia automáticamente.

## Si dejan de registrarse las compras

- **"Tu sesión venció"**: el token de la app dura ~30 días. Abrí Ledger CR y
  reingresá con `/login`; la automatización vuelve a funcionar sola.
- **Sin internet al momento del pago**: la compra se guarda y se reintenta sola
  en el siguiente pago o cuando vuelva la señal — no se pierde ni se duplica.
- **No aparece la acción "Registrar compra de Apple Pay" en Atajos**: asegurate de
  estar en el build de EAS/TestFlight (no Expo Go) y de haber abierto la app al
  menos una vez después de instalarla.

## Notas técnicas (para el operador)

- La acción es un **App Intent** nativo (`ApplePayCaptureIntent.swift`) inyectado
  por el config plugin `plugins/withApplePayIntent.js` — se compila dentro de la
  app principal y corre en segundo plano.
- Lee el token de sesión del **Keychain** (servicio `ledgercr.appintent`, escrito
  por `src/lib/appIntentToken.ts` al iniciar sesión) y hace
  `POST /api/v1/transactions/apple-pay` con `Authorization: Bearer`.
- Cada toque genera un `client_event_id` (UUID) → el backend lo guarda en
  `source_ref` con índice único, así un reintento es **idempotente**.
- El monto se interpreta como **gasto** (negativo); la moneda se detecta del
  símbolo (₡ → CRC, $ → USD) y se guarda en su moneda nativa (sin conversión).
- **Despliegue Apple**: NO requiere capacidades nuevas (ni Keychain Sharing, ni
  App Groups, ni Apple Pay/PassKit). El App Intent va dentro de la app principal
  y lee su propio keychain. Seguí el **mismo** flujo de EAS Build → TestFlight.
  Como es **código nativo**, necesita una **build nueva** (no sirve un update
  OTA `eas update`) y subir el `buildNumber`. No hay CI nativo: la verificación
  es en el dispositivo.
