# Crypto Intelligence System (CIS)

Implementación del [Master Spec](../Crypto_Intelligence_System_Master_Specification.md): sistema de
**investigación** (no trading automático) que detecta oportunidades tempranas en **Binance Alpha**
usando un pipeline multi-agente (Gemini) con debate Bull/Bear/Mediador/Juez, 5 scores independientes
y backtesting continuo con auto-corrección.

## Qué SÍ hace
- Discovery: descarga el universo completo de tokens de Binance Alpha (API pública oficial, sin key).
- Hard filters configurables (liquidez, volumen, market cap, holders, antigüedad de listado).
- 7 agentes investigadores + Bull + Bear + Mediador + Juez (Gemini, tier gratuito).
- Seguridad de contrato vía GoPlus (gratis, sin key) como evidencia Tier 1 para el Security Analyst.
- 5 scores independientes + veredicto (incluyendo "Insufficient Evidence").
- **Un solo botón de actualización** que en cada corrida: busca nuevos candidatos, investiga a fondo
  los mejores, Y ADEMÁS vuelve a revisar el precio real de todo lo que ya venció su horizonte de 7
  días — tanto lo que sí fue analizado (seleccionado) como lo que fue descartado por los hard
  filters. Esto es lo que alimenta la auto-corrección: si un % alto de descartados también hubiera
  llegado a +20%, es señal de que un filtro está demasiado estricto.
- Auto-corrección continua: cada vez que hay evaluaciones nuevas, el sistema diagnostica patrones de
  error (sobre lo analizado y sobre lo descartado) y propone ajustes concretos de umbrales, que puedes
  aplicar con un clic.
- Dashboard con varias vistas (Resumen, Oportunidades, Descartados, Historial, Auto-corrección,
  Configuración), sin build step, estilo editorial minimalista en blanco y negro.
- Historial de resultados: precisión general, precisión por tipo de veredicto, tiempo promedio a
  máximo, mejor/peor llamada, y una simulación de dinero puramente educativa (NO es una promesa de
  rendimiento). Con aviso automático de "muestra pequeña" mientras hay pocas evaluaciones.
- Acceso privado con usuario/contraseña, con bloqueo tras intentos fallidos — nadie sin la clave
  puede ver nada del sistema, y no se puede fuerza-bruta la contraseña.
- **Actualización automática**: corre sola cada N horas (configurable), sin depender de que alguien
  se acuerde de darle clic al botón.
- **Alertas por Telegram**: aviso automático cuando aparece un veredicto "Strong Opportunity" (o los
  que tú configures).
- **Historial propio como señal on-chain**: cada actualización guarda una foto de holders/liquidez/
  volumen de cada token. El On-chain Analyst compara esas fotos entre ciclos para ver tendencia real
  (¿holders creciendo o cayendo?), sin depender de una API externa de pago.

## Qué NO hace (por diseño, o por decisiones tuyas)
- No ejecuta operaciones, no conecta wallets, no maneja llaves privadas.
- No usa Google Search grounding por defecto: **desde enero de 2026 requiere facturación habilitada
  en el proyecto de Google Cloud, incluso el primer uso** (lo confirmé en pruebas: falla con 429
  apenas se llama, con una key puramente gratuita). Los agentes que antes iban a usar búsqueda web
  ahora razonan explícitamente solo sobre los datos reales que sí tenemos (Binance Alpha + GoPlus) y
  marcan como Tier 4/incertidumbre cualquier cosa que no puedan verificar. Ver "Recomendación:
  activar grounding" más abajo.
- No integra Etherscan/Solscan (decisión deliberada, ver sección de recomendaciones: no son buena
  relación costo/beneficio para este proyecto).
- No usa fuentes de pago (Nansen, Dune, CMC Pro) — elegiste solo APIs gratuitas.

## Sobre la API key de Gemini
Una sola `GEMINI_API_KEY` sirve para **todo el proyecto** (los 7 analistas + bull/bear/mediador/juez).
El rate limit del tier gratuito es por key, no por "rol", así que usar varias no ayuda salvo que
quieras crear cuentas de Google separadas para más throughput (no vale la pena a este volumen). El
sistema serializa las llamadas (`gemini_client.py`) para no pasarse del RPM/RPD gratuito, con
reintentos y backoff.

Probado en vivo: el pipeline completo (11 llamadas reales por token) funciona y produce análisis
coherente — por ejemplo detectó automáticamente una función de minteo activa y 82.78% de
concentración de supply en un contrato real, y bajó el veredicto a "High Risk / Speculative" pese al
buen momentum de precio.

## Recomendaciones de impacto real (implementadas)

Después del primer despliegue hice una revisión honesta de qué le faltaba al sistema para tener
impacto real (no solo funcionar). Esto es lo que se implementó y por qué:

### 1. Activar Google Search grounding (requiere una acción tuya: facturación)
Es la mejora de mayor impacto: hoy el Narrative Analyst y el Social Intelligence Analyst razonan
sin datos en vivo (2 de 7 analistas adivinando). Investigué el precio real: el grounding da
**5,000 usos gratis al mes** una vez que activas facturación en tu proyecto de Google Cloud, y
después cuesta **$14 por 1,000 búsquedas**. Con hasta 4 analistas usando grounding × ~15
candidatos/actualización × 2 actualizaciones/día (con el auto-update en 12h), estarías muy por
debajo del umbral gratuito en uso personal. **Yo no puedo activar facturación por ti** (requiere
meter una tarjeta en la consola de Google Cloud, eso te toca a ti). Pasos:
1. Ve a https://aistudio.google.com/apikey, entra a tu proyecto, activa facturación (Google suele
   dar además crédito gratis de bienvenida).
2. En `backend/.env` pon `GEMINI_ENABLE_SEARCH_GROUNDING=true`.
3. Listo — no hay que tocar código, el flag ya está implementado en `gemini_client.py`.

### 2. Actualización automática (implementado)
`app/scheduler.py` corre `run_update_cycle()` solo, cada `AUTO_UPDATE_INTERVAL_HOURS` (12h por
defecto), en un hilo de fondo separado del servidor web. El primer ciclo arranca ~60s después de
levantar el servidor. Se salta el ciclo si ya hay una actualización en curso (manual o automática),
así nunca se solapan. Se puede apagar con `AUTO_UPDATE_ENABLED=false`.

### 3. Alertas por Telegram (implementado, falta que crees el bot)
`app/notifications.py` manda un mensaje cuando aparece un veredicto en `TELEGRAM_NOTIFY_VERDICTS`
(por defecto solo "Strong Opportunity"). Yo no puedo crear tu bot de Telegram (requiere tu cuenta):
1. En Telegram, busca **@BotFather** y mándale `/newbot`. Te da un `TELEGRAM_BOT_TOKEN`.
2. Mándale cualquier mensaje a tu bot recién creado (para que Telegram registre el chat).
3. Abre `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` en el navegador y busca `"chat":{"id": ...}`
   — ese número es tu `TELEGRAM_CHAT_ID`.
4. Pon ambos valores en `backend/.env`. Mientras estén vacíos, el sistema simplemente no manda nada
   (no rompe el pipeline).

### 4. Tratar el historial con cautela hasta tener muestra suficiente (implementado)
La pestaña **Historial** ahora muestra un aviso explícito ("muestra pequeña, trata estas cifras con
cautela") mientras haya menos de 20 predicciones evaluadas. Con la actualización automática ya
activada, esto se resuelve solo con el tiempo — no hace falta que hagas nada, solo no confíes en el
% de acierto hasta que ese aviso desaparezca.

### 5. Presupuesto de Gemini: rotación de API keys gratuitas (implementado) o facturación
Confirmado en producción: la cuota gratuita diaria del modelo smart (Bull/Bear/Mediador/Juez) es
de **20 llamadas/día por proyecto de Google Cloud** (no por key — varias keys del mismo proyecto
comparten la cuota). Cada token analizado gasta 4 de esas 20, así que 1 proyecto = ~5 tokens/día.

En vez de pagar de entrada, implementé rotación automática de keys: `GEMINI_SMART_API_KEYS`
acepta keys de proyectos de Google Cloud **distintos** (cada uno gratis), separadas por coma.
`gemini_client.py` pasa a la siguiente automáticamente cuando la actual se agota por hoy. Con 3
proyectos en total (el de `GEMINI_API_KEY` + 2 en `GEMINI_SMART_API_KEYS`) cubres los 15
candidatos de `MAX_CANDIDATES_PER_RUN` sin gastar nada — verificado con una corrida real.

Si en el futuro subes `MAX_CANDIDATES_PER_RUN` por encima de 15, necesitas más proyectos (uno
extra por cada 5 candidatos adicionales), o activar facturación para dejar de administrar keys:
precios reales investigados: `gemini-3.5-flash-lite` (analistas) = **$0.30 / $2.50** por millón de
tokens input/output; `gemini-3.5-flash` (debate/juez) = **$1.50 / $9.00** por millón — la
combinación que ya usa el sistema es la más barata posible sin sacrificar razonamiento. Con
facturación, analizar muchos más candidatos por ciclo cuesta centavos de dólar al día.

### 6. Rate-limit de login (implementado)
Antes de exponerlo a internet, un endpoint de login público sin límite de intentos es un riesgo real
de fuerza bruta. Ahora `app/auth.py` bloquea por `LOGIN_MAX_ATTEMPTS` intentos fallidos (5 por
defecto) durante `LOGIN_LOCKOUT_MINUTES` (15 por defecto), por IP — y si el sistema queda detrás de
Cloudflare Tunnel, usa el header `CF-Connecting-IP` para identificar la IP real del visitante en vez
de la IP interna del túnel.

### 7. On-chain: decisión distinta a la pedida originalmente (Etherscan/Solscan)
Investigué ambas antes de integrarlas y **no valen la pena para este proyecto**:
- **Etherscan API V2** da una sola key multichain, pero **su tier gratuito excluye BSC** — y
  comprobé con datos reales de Binance Alpha que **el 74% de los tokens (490 de 665) están en BSC**.
  Integrarla solo cubriría un cuarto del universo.
- **Solscan** no tiene un tier gratuito confiable para producción; su plan de pago arranca en
  ~$199/mes — desproporcionado para este proyecto.

En vez de eso, implementé algo mejor y gratis: cada actualización ya guarda una foto de
holders/liquidez/volumen por token (`predictions` en SQLite). El On-chain Analyst ahora recibe el
historial propio de ciclos anteriores del mismo símbolo y calcula tendencia real (¿holders creciendo
o cayendo?) en vez de depender de una foto fija o de una API externa de pago. Esto mejora solo con
el tiempo, automáticamente, ahora que la actualización corre sola (punto 2).

### 8. Segunda opinión del Juez vía Groq (Fase 2, 2026-09-24, modo shadow por defecto)
Cada veredicto de Gemini se contrasta ahora con una segunda opinión independiente de un modelo
distinto: Groq (Llama), que tiene free tier permanente sin facturación. `app/groq_client.py`
implementa el mismo contrato que `gemini_client.py` pero adaptado a la API de Groq (modo JSON
genérico en vez del `response_schema` nativo de Gemini, con reintento si el JSON no trae las
claves esperadas).

**Comportamiento actual (`ENSEMBLE_JUDGE_MODE=shadow`, el que arranca activo):** se llama a Groq,
se guardan su veredicto y sus 4 scores (`secondary_judge_*`), y si coincide o no con Gemini
(`judge_agreement`) — pero el veredicto final que se muestra en el dashboard y el que dispara
Telegram **sigue siendo exactamente el de Gemini, sin tocar**. La razón: un desacuerdo entre dos
modelos no prueba por sí solo que la evidencia sea insuficiente (podría ser que Groq tuvo menos
contexto, o que el caso es genuinamente ambiguo) — antes de actuar sobre eso hace falta acumular
historial y cruzarlo contra el resultado real (`max_return_pct`) para ver si el desacuerdo de
verdad predice peor precisión.

**Modo `active`** (activable desde Configuración, decisión humana, nunca automática): si Gemini
dice "Strong Opportunity" y Groq no coincide, el veredicto final que se persiste baja a
"Insufficient Evidence" — los 4 scores numéricos de Gemini no se tocan, solo el veredicto.
Cualquier otro veredicto de Gemini no se ve afectado por el desacuerdo.

**Si Groq no está disponible** (sin `GROQ_API_KEY`, caído, rate limit, JSON inválido tras
reintentos): el ciclo sigue normal con solo el veredicto de Gemini, `secondary_judge_verdict` y
`judge_agreement` quedan en `NULL` para esa predicción — nunca se bloquea el research por la
caída de un tercero. Necesitas crear el secret `GROQ_API_KEY` en GitHub Actions manualmente
(gratis en [console.groq.com](https://console.groq.com)) para que esto funcione en producción.

## Acceso privado
El dashboard entero (frontend + API) queda detrás de un login simple de usuario/contraseña — sin
sesión válida, cualquier ruta redirige a `/login` (o devuelve 401 en API). Es un solo usuario fijo,
no un sistema de cuentas: pensado para "solo yo y gente relacionada", no para escalar a multiusuario.

Usuario y contraseña se configuran en `backend/.env` (`AUTH_USERNAME`, `AUTH_PASSWORD`) — cámbialos
ahí si quieres otros distintos a los que me diste. La sesión dura 30 días (cookie httponly) y se
guarda en la misma base SQLite.

## Despliegue sin servidor propio: GitHub Actions + Turso + Cloudflare Worker
**Decisión de arquitectura (reemplaza el enfoque anterior de Cloudflare Tunnel):** para no
depender de tu PC prendida ni de un VPS pagado, el sistema se separó en tres piezas, todas
gratis en este volumen:

- **GitHub Actions** (cron cada 12h) corre el pipeline pesado (Gemini, Binance, GoPlus,
  Telegram) — [`.github/workflows/update-cycle.yml`](.github/workflows/update-cycle.yml).
- **Turso** (SQLite remoto vía HTTP) guarda los datos — accesible tanto desde Python (GitHub
  Actions) como desde JavaScript (el Worker), sin que ninguno de los dos necesite un disco
  persistente propio.
- **Cloudflare Worker** ([`worker/`](worker/)) sirve el dashboard 24/7 con cero servidor: lee
  Turso directamente y, cuando le das clic a "Actualizar sistema", dispara el workflow de
  GitHub Actions vía su API en vez de correr el pipeline él mismo (un Worker no puede: el
  pipeline tarda 10-60 min por el rate limit gratuito de Gemini, muy por encima del límite de
  ejecución de un Worker).

El backend Python local (`uvicorn`) sigue funcionando igual que siempre para desarrollo — usa
SQLite local automáticamente si no defines `TURSO_DATABASE_URL`.

**Guía de despliegue paso a paso, con lo que te toca a ti y lo que hago yo:** [`DEPLOY.md`](DEPLOY.md).

## Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate   # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edita `backend/.env` y pon tu `GEMINI_API_KEY` (gratis en https://aistudio.google.com/apikey).

## Ejecutar

```bash
cd backend
venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

Abre http://localhost:8000 — ahí está el dashboard. También hay una configuración de preview
(`crypto-intelligence-system`, puerto 8010) para lanzarlo desde el entorno de desarrollo.

## Flujo de uso
El sistema ya corre solo cada `AUTO_UPDATE_INTERVAL_HOURS` (12h por defecto) — no necesitas darle
clic al botón para que funcione día a día, es para forzar un ciclo extra cuando quieras.

1. Pestaña **Resumen**: botón **"Actualizar sistema"**. Cada clic corre el ciclo completo (descubre,
   filtra, investiga a fondo los mejores candidatos nuevos, revisa el resultado real de todo lo
   anterior que ya venció, y genera diagnóstico si hay suficiente evidencia nueva). Tarda varios
   minutos por el rate limit del tier gratuito de Gemini — el log en vivo muestra el progreso.
2. Pestaña **Oportunidades**: tarjetas con scores, bull/bear case, riesgos, evidencia y veredicto de
   cada candidato analizado.
3. Pestaña **Descartados**: tabla de tokens rechazados por los hard filters, con la razón exacta y
   (una vez evaluados) si en realidad sí hubieran sido una buena oportunidad.
4. Pestaña **Historial**: qué tan bien le ha ido al sistema en total — tasa de acierto general,
   precisión por tipo de veredicto, tiempo promedio a máximo, mejor/peor llamada, y una simulación
   de dinero puramente educativa.
5. Pestaña **Auto-corrección**: precisión histórica por rango de confidence, eficacia de los filtros
   (analizado vs. descartado), y el diagnóstico de auto-corrección más reciente con botón para
   aplicar los ajustes propuestos.
6. Pestaña **Configuración**: edita los umbrales de los hard filters directamente.

No hace falta un "modo piloto" separado: cada actualización ya se auto-valida contra sus propias
predicciones anteriores, seleccionadas y descartadas por igual.

## Estructura
```
backend/app/
  binance_alpha.py     Cliente API pública Binance Alpha (token list, klines)
  goplus.py             Cliente GoPlus Security (contrato/seguridad)
  gemini_client.py       Wrapper Gemini con throttling, reintentos, JSON schema, grounding opcional
  filters.py             Hard filters + ranking pre-Earliness
  market_stats.py         Estadísticas derivadas de klines (volatilidad, cambio 7d)
  auth.py                 Login de usuario único + sesiones + rate-limit de intentos fallidos
  notifications.py         Alertas por Telegram (opcional, no-op si no está configurado)
  scheduler.py              Actualización automática LOCAL (dev); en producción, el cron es GitHub Actions
  db.py                      SQLite local o Turso remoto, detectado automáticamente
  dynamic_config.py          Hard filters compartidos entre Worker/GitHub Actions/local (tabla system_config)
  agents/research.py       Los 7 analistas investigadores
  agents/debate.py         Bull, Bear, Mediador, Juez, diagnóstico de auto-corrección
  pipeline.py              Orquesta discovery -> filtros -> research -> tracking de descartados
  backtesting.py           Evaluación post-horizonte + stats históricas + eficacia de filtros + historial
  diagnosis.py             Auto-corrección continua (reemplaza el antiguo "piloto de 7 días")
  main.py                   FastAPI + rutas + middleware de autenticación (uso local)
backend/scripts/run_once.py  Punto de entrada para GitHub Actions: un ciclo y termina
frontend/
  index.html              Dashboard (usado por el servidor local FastAPI)
  login.html               Pantalla de acceso (uso local)
worker/                    Cloudflare Worker: dashboard 24/7 sin servidor propio
  src/index.js              Router + auth gate + disparo de GitHub Actions
  src/auth.js                Login/sesiones/rate-limit (Workers KV)
  src/db.js                   Cliente Turso para el Worker (@libsql/client/web)
  src/backtesting.js          Puerto JS de las agregaciones de backend/app/backtesting.py
  src/github.js                Dispara y consulta el workflow de GitHub Actions
  public/                     Copia del frontend (mismo HTML/JS, mismas rutas /api/*)
.github/workflows/update-cycle.yml   Cron de GitHub Actions (cada 12h) + workflow_dispatch manual
DEPLOY.md                  Guía paso a paso para desplegar esta arquitectura
cloudflare/config.yml      Plantilla de Cloudflare Tunnel (enfoque anterior, ya no es el principal)
```

## Límites conocidos / próximos pasos naturales
- El tier gratuito de Gemini limita cuántos tokens puedes investigar por actualización (~1400
  llamadas/día, ~11 llamadas por token analizado → baja `MAX_CANDIDATES_PER_RUN` si te quedas sin
  cuota). Los descartados no consumen cuota de Gemini (no pasan por LLM).
- Los ajustes que propone la auto-corrección, y los que edites a mano en Configuración, se aplican en
  memoria del proceso corriendo (no sobreviven un reinicio); para hacerlos permanentes, cópialos a
  `backend/.env`.
- El scheduler automático vive en el mismo proceso del servidor: si reinicias el servidor, el
  contador de `AUTO_UPDATE_INTERVAL_HOURS` vuelve a empezar desde cero (arranca ~60s después).
