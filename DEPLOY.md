# Despliegue sin servidor propio: GitHub Actions + Turso + Cloudflare Worker

Esta es la arquitectura que reemplaza "tener la PC prendida" o pagar un VPS:

```
GitHub Actions (cron cada 12h)  --escribe-->  Turso (base de datos remota)  <--lee--  Cloudflare Worker (dashboard 24/7)
        |                                                                                      |
   corre pipeline.py                                                                    dispara el workflow
   (Gemini + Binance +                                                                  cuando le das clic a
    GoPlus + Telegram)                                                                  "Actualizar sistema"
```

Nada de esto necesita que tu PC esté prendida. Cada pieza es gratis en el volumen de este proyecto.

Los pasos marcados **(TÚ)** solo los puedes hacer tú — necesitan tu cuenta/tarjeta/sesión de navegador.
Los marcados **(YO)** te los hago yo en cuanto confirmes que hiciste el paso anterior.

---

## 1. Turso — base de datos (TÚ)

1. Ve a https://turso.tech y crea una cuenta (gratis, con GitHub o email).
2. Instala el CLI de Turso o usa su dashboard web para crear una base:
   ```bash
   turso db create cis
   turso db show cis --url
   turso db tokens create cis
   ```
3. Guarda dos valores: la **URL** (empieza con `libsql://...`) y el **token**.

Dámelos (o ponlos tú directamente en `backend/.env` como `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN`) y yo:
- **(YO)** pruebo la conexión real desde Python.
- **(YO)** corro `init_db()` contra la base remota para crear las tablas.

---

## 2. GitHub — repositorio + secretos (TÚ arranca, YO empujo el código)

1. **(TÚ)** Crea un repo **privado** en https://github.com/new (nombre sugerido: `crypto-intelligence-system`). No lo inicialices con README (ya tenemos uno).
2. **(TÚ)** Dame la URL del repo (o autentica git en esta máquina) y yo hago `git push`.
3. **(TÚ)** En el repo → Settings → Secrets and variables → Actions → New repository secret, agrega:
   - `GEMINI_API_KEY` — key de tu primer proyecto de Google Cloud (ej. `cis-primary`).
   - `GEMINI_SMART_API_KEYS` — keys de 2 proyectos de Google Cloud **adicionales y distintos**
     (ej. `cis-smart-2`, `cis-smart-3`), separadas por coma sin espacios. La cuota gratuita
     diaria del modelo smart (Bull/Bear/Mediador/Juez) es de solo 20 llamadas/día **por
     proyecto** — con 3 proyectos en total cubres los 15 candidatos que analiza cada
     actualización. `gemini_client.py` rota automáticamente entre ellas.
   - `TURSO_DATABASE_URL`
   - `TURSO_AUTH_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GOPLUS_APP_KEY` / `GOPLUS_APP_SECRET` (opcionales, déjalos vacíos si no los usas)
4. **(TÚ)** Crea un **Personal Access Token** (classic, con scopes `repo` **y `workflow`**) en
   https://github.com/settings/tokens — lo necesita el Worker para poder disparar el workflow
   desde el botón "Actualizar sistema" (y a mí me hace falta `workflow` para poder subir
   cambios al archivo `.github/workflows/update-cycle.yml`). Guárdalo, lo usamos en el paso 3.

Con esto, el workflow en [`.github/workflows/update-cycle.yml`](.github/workflows/update-cycle.yml)
ya queda corriendo solo cada 12h en cuanto el código esté en el repo.

---

## 3. Cloudflare Worker — dashboard 24/7 (TÚ inicias sesión, YO despliego)

1. **(TÚ)** Corro `wrangler login` — te abre el navegador para autorizar con tu cuenta de
   Cloudflare (la misma que ya tienes).
2. **(YO)** Creo el namespace de KV para sesiones:
   ```bash
   wrangler kv namespace create SESSIONS
   ```
   y actualizo `worker/wrangler.toml` con el ID real que devuelva.
3. **(TÚ o YO, tú das los valores)** Configuro los secretos del Worker:
   ```bash
   wrangler secret put AUTH_USERNAME        # PAI
   wrangler secret put AUTH_PASSWORD        # TUPU777
   wrangler secret put TURSO_DATABASE_URL
   wrangler secret put TURSO_AUTH_TOKEN
   wrangler secret put GITHUB_PAT           # el token del paso 2.4
   wrangler secret put GITHUB_REPO          # ej. "tu-usuario/crypto-intelligence-system"
   ```
4. **(YO)** Despliego: `wrangler deploy` — te da una URL tipo `cis-dashboard.tu-cuenta.workers.dev`.
5. **(Opcional, TÚ)** Si quieres tu propio dominio en vez de `*.workers.dev`, lo conectamos
   después con una Route de Cloudflare — no requiere el Tunnel que discutimos antes, es más simple.

---

## 4. Verificación final (YO)

- Confirmo que el login funciona en la URL real.
- Disparo una actualización manual desde el botón y confirmo que aparece en GitHub Actions.
- Confirmo que los datos escritos por GitHub Actions aparecen en el dashboard.

---

## Notas

- El backend local (`backend/app/main.py`, SQLite) sigue funcionando para desarrollo — no se
  tocó, solo se le agregó la capacidad de usar Turso si `TURSO_DATABASE_URL` está configurado.
- Si algún día quieres volver a correrlo todo local (sin GitHub Actions ni Worker), basta con
  no poner `TURSO_DATABASE_URL` y usar `uvicorn` como antes.
- El costo real esperado: **$0/mes** en el volumen de este proyecto (GitHub Actions, Turso y
  Cloudflare Workers/KV se quedan muy por debajo de sus límites gratuitos).
