# Pump Team — Agente de Tendencias

Agente autónomo que investiga tendencias semanales en farmacología deportiva,
optimización hormonal y culturismo. Corre cada domingo a las 18hs Argentina y
manda un reporte estructurado por WhatsApp.

## Setup en 5 pasos

### 1. Forkeá o cloná este repo en tu cuenta de GitHub

```bash
git clone https://github.com/TU_USUARIO/pump-team-trends.git
cd pump-team-trends
```

### 2. Configurá los GitHub Secrets

En tu repo → **Settings → Secrets and variables → Actions → New repository secret**

El agente usa el mismo provider que tu bot `agentkit-coach`. Los valores están en el `.env` de ese proyecto.

**Siempre requeridos (2):**

| Secret | Dónde encontrarlo |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `WHATSAPP_NUMBER` | Tu número con código de país, ej: `5491154822840` |

**Si usás Whapi.cloud** (provider por defecto — `WHATSAPP_PROVIDER=whapi`):

| Secret | Valor en tu `.env` de agentkit-coach |
|---|---|
| `WHAPI_API_URL` | `WHAPI_API_URL` |
| `WHAPI_API_TOKEN` | `WHAPI_API_TOKEN` |

**Si usás Meta Cloud API** (`WHATSAPP_PROVIDER=meta`):

| Secret | Valor en tu `.env` de agentkit-coach |
|---|---|
| `META_ACCESS_TOKEN` | `META_ACCESS_TOKEN` |
| `META_PHONE_NUMBER_ID` | `META_PHONE_NUMBER_ID` |

> El Secret `WHATSAPP_PROVIDER` es opcional — si no lo ponés usa `whapi` por defecto.

### 3. Habilitá GitHub Actions

Si es un fork, andá a la pestaña **Actions** del repo y aceptá el prompt para habilitar workflows.

### 4. Probá manualmente

En la pestaña **Actions** → **Trend Scout — Pump Team** → **Run workflow**.
Revisá los logs para ver cada búsqueda en tiempo real.

### 5. Listo

El agente corre automáticamente todos los domingos a las 18hs (Argentina, UTC-3).

---

## Prueba local (con Whapi)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export WHATSAPP_NUMBER="5491154822840"
export WHAPI_API_URL="https://gate.whapi.cloud"
export WHAPI_API_TOKEN="tu-token"

pip install anthropic requests
python agent.py
```

Si querés probar sin mandar el WhatsApp, simplemente no seteés `WHAPI_API_URL` / `WHAPI_API_TOKEN` — el script va a fallar el envío graciosamente e imprimir el reporte completo en stdout de todas formas.

---

## Costo estimado por ejecución

| Componente | Estimación |
|---|---|
| Tokens de entrada (system prompt + resultados de búsqueda) | ~40-60K tokens |
| Tokens de thinking (adaptive, varía por complejidad) | ~10-30K tokens |
| Tokens de salida (reporte final) | ~2-3K tokens |
| **Total estimado** | **~$0.50 – $1.50 USD por run** |

Precio base: Claude Opus 4.6 — $5.00/1M input, $25.00/1M output.
Con 52 runs al año el costo anual es aprox. **$26 – $78 USD**.

---

## Personalización

**Cambiar el límite de búsquedas** (variable `MAX_SEARCHES` en `agent.py`):
- Más búsquedas → reporte más completo, mayor costo
- Menos búsquedas → más rápido y barato, pero puede no alcanzar el mínimo de 20

**Cambiar el horario** (archivo `.github/workflows/trend_scout.yml`):
La línea `cron: '0 21 * * 0'` corresponde a domingos 21:00 UTC = 18:00 Argentina.
Usá [crontab.guru](https://crontab.guru) para ajustar el horario.
