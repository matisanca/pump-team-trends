#!/usr/bin/env python3
"""
pump-team-trends/agent.py

Agente autónomo de inteligencia de mercado para Pump Team.
Investiga tendencias semanales en farmacología deportiva y las reporta por WhatsApp.

ARQUITECTURA — Loop agentico real:
  El servidor de Anthropic corre su propio loop de búsquedas (hasta 10 por llamada).
  Cuando llega al límite interno devuelve stop_reason="pause_turn" en lugar de "end_turn".
  Este script detecta el pause_turn, preserva el contexto acumulado y hace una nueva
  llamada para que el servidor retome desde donde quedó.
  El proceso continúa hasta que el agente produzca el reporte final (end_turn)
  o se alcance el límite de MAX_SEARCHES búsquedas para controlar costos.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone

import anthropic
import requests


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Configuración ─────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"

# Límite de búsquedas para controlar costos (~$0.50-0.70 por run típico)
MAX_SEARCHES = 15

# Reintentos ante fallo de la API de Anthropic (con 30s de espera entre intentos)
MAX_API_RETRIES = 2
RETRY_WAIT_SECONDS = 30


# ── System prompt del agente ──────────────────────────────────────────────────
# No modificar este string — es el prompt exacto acordado con Mati.

SYSTEM_PROMPT = """Sos un agente de inteligencia de mercado especializado en farmacología deportiva,
optimización hormonal y culturismo. Tu trabajo es investigar qué temas están
explotando ESTA SEMANA en el mundo angloparlante.

REGLAS DE INVESTIGACIÓN:
- Buscá SIEMPRE en inglés
- Investigá en este orden:
  1. Qué publicaron esta semana: @toddleemd, @realnicktrigili, @coach.agz, @dynamite_d, @carsonlabroque
  2. Posts más upvoteados esta semana en: r/steroids, r/PEDs, r/Testosterone, r/moreplatesmoredates, r/trt
  3. Hilos virales en Twitter/X sobre: TRT, steroids, PCT, peptides, HPTA, fertility, libido on cycle
  4. Papers nuevos en PubMed/Google Scholar (últimos 30-60 días) sobre AAS, TRT, péptidos, HPTA
  5. Búsquedas de seguimiento sobre temas que se repitan en múltiples fuentes
- Solo incluí temas con evidencia REAL y CONCRETA (video viral, post 200+ upvotes, paper nuevo, hilo con replies masivos)
- 3 oportunidades sólidas > 5 inventadas

QUIÉN ES MATI:
Médico y personal trainer. ÚNICO médico hispanohablante que combina criterio clínico con
experiencia como preparador. Su solución siempre: ANALÍTICAS PRIMERO → causa exacta → solución específica.

EL AVATAR (Luciano):
Hombre 30-42 años, LATAM, 5-7 años en fitness, 2-6 en farmacología.
Ya fue defraudado por preparadores con ciclos genéricos sin análisis.
Miedos concretos: quedar en TRT de por vida, perder fertilidad, disfunción eréctil, ir a ciegas.

BUENA OPORTUNIDAD:
✅ Alta conversación en inglés + CERO contenido de calidad en español
✅ Activa el miedo concreto de Luciano
✅ Pregunta muy repetida sin respuesta médica clara
✅ Paper reciente que desafía algo que la gente hace intuitivamente

MALA OPORTUNIDAD:
❌ Tema amplio sin pregunta específica del avatar
❌ Tendencia de hace más de 2 semanas
❌ Ya hay contenido de calidad en español

FORMATO DE OUTPUT — seguí esto exactamente:

📡 RADAR — PUMP TEAM | Semana del [fecha]
Búsquedas: [N]

#1 — [NOMBRE DEL TEMA]
Urgencia: 🔴 Esta semana / 🟡 Próximas 2 semanas
Evidencia: [fuente exacta + fecha + dato concreto]
Por qué explota ahora: [qué pasó específicamente esta semana, 2 líneas máx]
Dolor de Luciano: [frase concreta en sus palabras, ej: "terminé el ciclo y no se me levanta"]
Gap en español: [por qué nadie en LATAM lo cubre con criterio médico]
Ángulo de Mati: [qué puede explicar él que ningún otro creador puede]

#2 — [...]
#3 — [...]
#4 — [...]"""


# ── Prompt del agente guionista ───────────────────────────────────────────────

SCRIPT_WRITER_PROMPT = """Sos un guionista experto que escribe guiones para Instagram Reels de Mati,
médico y personal trainer argentino. Tu trabajo es tomar un brief de investigación
y escribir 3 guiones listos para grabar, uno por tema.

CONTEXTO DE MATI:
Único médico hispanohablante con criterio clínico + experiencia como preparador.
Su solución diferencial: analíticas primero → causa exacta → solución personalizada.
Nunca protocolos genéricos.

EL AVATAR (Luciano):
Hombre 30-42 años, LATAM, ya fue defraudado por preparadores. Le atraen los datos.
Miedos concretos: disfunción eréctil, quedar en TRT de por vida, perder fertilidad,
perder todo lo ganado en el ciclo.

RESTRICCIÓN INSTAGRAM — LENGUAJE INDIRECTO OBLIGATORIO:
NO nombrar sustancias explícitamente. Ejemplos:
- NO "testosterona" → SÍ "la base del ciclo" o "el compuesto base"
- NO "Clomid/enclomifeno" → SÍ "el fármaco que reactiva el eje"
- NO "HCG" → SÍ "el compuesto que despierta los testículos"
- NO "trenbolona" → SÍ "el compuesto más agresivo"
- NO "anastrozol/AI" → SÍ "el bloqueador que te recetan para los estrógenos"

REGLAS ESTRICTAS — NO NEGOCIABLES:
1. UN solo problema + UNA sola solución por guión. Si el tema es ginecomastia, NO toques libido.
2. Máximo 200 palabras por guión (≈ 1 minuto 20 segundos).
3. Dolor TANGIBLE y CONCRETO: "no se te va a levantar", "vas a perder todo lo que ganaste",
   "te quedás en TRT de por vida". NUNCA abstracto ("impacto sistémico", "pozo hormonal").
4. NO repetir el mismo mensaje más de una vez en el guión.
5. CTA apunta al DESEO: "para que sepas cómo recuperarlo" no "para evitar daños".
6. Dar valor real en el video — una solución concreta y accionable.
7. Frases cortas, una idea por línea.
8. Español rioplatense informal: "tenés", "hacés", "estás", "vos".

ESTRUCTURA OBLIGATORIA:
HOOK (2-3 líneas): Gancho concreto. Dolor específico O promesa de solución. Directo.
CONTEXTO (3-5 líneas): Qué hace la mayoría mal y por qué está equivocado.
VALOR (6-8 líneas): La solución real paso a paso. Usar micro-hooks cortos para mantener atención.
  Ejemplos de micro-hooks: "Y ese es el error.", "Pero no.", "Acá está la clave.", "Y eso no se adivina."
CTA (2-3 líneas): Palabra clave para comentar. Apunta al beneficio/deseo.

EJEMPLO DE GUIÓN (calibrá estilo y extensión con esto):

HOOK
Si terminaste el ciclo y no se te levanta...
no es normal, pero tampoco es permanente.
Tiene solución.

CONTEXTO
Lo primero que hace la gente: ver testosterona baja y entrar en pánico.
Pero ese no es el problema real.
El problema es que tu eje quedó inhibido.
El cerebro dejó de mandar señales. Los testículos dejaron de responder.
Y si no lo reactivás bien, la libido no vuelve.

VALOR
La recuperación tiene fases, y saltarse una rompe todo.
Primero: reactivar los testículos.
Sin esto, nada de lo que hagas después sirve.
Segundo: esperar la vida media de lo que usaste.
Si todavía hay esteroides en sangre, el eje no arranca.
Tercero: reactivar el eje con el fármaco correcto.
Y al final: analíticas completas.
No cuando te sentís bien. Cuando los números lo confirman.

CTA
Hice un video completo explicando esto fase por fase.
Comentá "RECUPERAR" y te lo mando.

---

OUTPUT ESPERADO:
Escribí exactamente 3 guiones, uno por cada tema del brief.
Separalos con esta línea exacta entre cada uno:
===GUIÓN #1===
[guión]
===GUIÓN #2===
[guión]
===GUIÓN #3===
[guión]"""


# ── WhatsApp ──────────────────────────────────────────────────────────────────
# Soporta dos providers del mismo bot (agentkit-coach):
#   - "whapi"  → Whapi.cloud (default)     vars: WHAPI_API_URL, WHAPI_API_TOKEN
#   - "meta"   → Meta Cloud API            vars: META_ACCESS_TOKEN, META_PHONE_NUMBER_ID
# Selección: WHATSAPP_PROVIDER="whapi" | "meta"  (default: whapi)
#
# WhatsApp tiene límite práctico de ~4096 chars por burbuja.
# El reporte puede superar eso, así que se divide automáticamente.

_WA_MAX_LEN = 4000


def _split_message(text: str) -> list[str]:
    """Divide un texto largo en partes de máximo _WA_MAX_LEN caracteres."""
    if len(text) <= _WA_MAX_LEN:
        return [text]
    return [text[i : i + _WA_MAX_LEN] for i in range(0, len(text), _WA_MAX_LEN)]


def _send_via_whapi(message: str, number: str) -> None:
    """
    Envía un mensaje via Whapi.cloud.
    Mismo formato que usa MetaProvider en agentkit-coach/agent/providers/whapi.py.
    Requiere: WHAPI_API_URL, WHAPI_API_TOKEN
    """
    api_url   = os.environ["WHAPI_API_URL"].rstrip("/")
    api_token = os.environ["WHAPI_API_TOKEN"]

    # Whapi usa chatId con formato "numero@s.whatsapp.net"
    resp = requests.post(
        f"{api_url}/messages/text",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        json={"to": f"{number}@s.whatsapp.net", "body": message},
        timeout=30,
    )
    resp.raise_for_status()


def _send_via_meta(message: str, number: str) -> None:
    """
    Envía un mensaje via Meta Cloud API (graph.facebook.com v21.0).
    Mismo formato que usa MetaProvider en agentkit-coach/agent/providers/meta.py.
    Requiere: META_ACCESS_TOKEN, META_PHONE_NUMBER_ID
    """
    access_token    = os.environ["META_ACCESS_TOKEN"]
    phone_number_id = os.environ["META_PHONE_NUMBER_ID"]

    resp = requests.post(
        f"https://graph.facebook.com/v21.0/{phone_number_id}/messages",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": number,
            "type": "text",
            "text": {"body": message},
        },
        timeout=30,
    )
    resp.raise_for_status()


def send_whatsapp(message: str, number: str) -> bool:
    """
    Envía el reporte por WhatsApp usando el mismo provider que agentkit-coach.
    Divide automáticamente mensajes largos en partes de 4000 chars.

    Provider activo según WHATSAPP_PROVIDER:
        "whapi" (default) → Whapi.cloud
        "meta"            → Meta Cloud API
    """
    provider = os.environ.get("WHATSAPP_PROVIDER", "whapi").lower()
    parts    = _split_message(message)

    try:
        for i, part in enumerate(parts, 1):
            if len(parts) > 1:
                logger.info(f"WhatsApp → parte {i}/{len(parts)}")
            if provider == "meta":
                _send_via_meta(part, number)
            else:
                _send_via_whapi(part, number)

        logger.info(f"WhatsApp → OK via {provider} ({len(parts)} mensaje(s) a {number})")
        return True
    except requests.RequestException as e:
        logger.error(f"WhatsApp → ERROR via {provider}: {e}")
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_report(content: list) -> str:
    """
    Extrae únicamente los bloques de texto del response del agente.
    Ignora thinking blocks, server_tool_use blocks y web_search_tool_result blocks.
    """
    texts = [
        block.text
        for block in content
        if getattr(block, "type", None) == "text" and hasattr(block, "text")
    ]
    if not texts:
        raise ValueError(
            "El agente no produjo ningún bloque de texto. "
            "Revisá los logs para ver qué devolvió la API."
        )
    return "\n".join(texts)


def count_and_log_searches(content: list, running_total: int) -> int:
    """
    Cuenta los bloques server_tool_use (búsquedas web) en un response,
    los loguea con timestamp, y retorna el nuevo total acumulado.
    """
    for block in content:
        if getattr(block, "type", None) == "server_tool_use":
            running_total += 1
            # El input del server_tool_use contiene la query de búsqueda
            input_data = getattr(block, "input", {}) or {}
            query = (
                input_data.get("query", "—")
                if isinstance(input_data, dict)
                else "—"
            )
            logger.info(f"  🔍 Búsqueda #{running_total}: {query}")
    return running_total


# ── Agentic loop ──────────────────────────────────────────────────────────────

def run_agent(client: anthropic.Anthropic) -> str:
    """
    Ejecuta el loop agentico del agente de tendencias.

    Cómo funciona el loop con server-side tools:
    ─────────────────────────────────────────────
    La herramienta web_search corre enteramente en los servidores de Anthropic.
    El servidor ejecuta hasta 10 búsquedas por llamada a la API. Cuando llega
    a ese límite interno devuelve stop_reason="pause_turn" con todo el contexto
    acumulado (queries + resultados) en response.content.

    Para continuar, simplemente re-enviamos:
        messages = [user_original, assistant_response_con_tool_results]

    El servidor detecta el bloque server_tool_use al final del contexto y
    retoma automáticamente. No hace falta agregar un mensaje "Continuá".

    El loop termina cuando:
      a) El agente produce el reporte final → stop_reason="end_turn"
      b) Alcanzamos MAX_SEARCHES → pedimos el reporte con lo que hay
    """
    current_date = datetime.now().strftime("%d/%m/%Y")

    # Mensaje inicial: disparar la investigación
    user_input = (
        f"Hoy es {current_date}. "
        "Seguí el protocolo de investigación del system prompt al pie de la letra: "
        "mínimo 20 búsquedas web en el orden especificado. "
        "Al terminar, producí el reporte completo en el formato exacto indicado."
    )

    messages = [{"role": "user", "content": user_input}]
    total_searches = 0
    api_call_count = 0
    awaiting_final_report = False  # True cuando ya pedimos el reporte final

    while True:
        api_call_count += 1
        logger.info(
            f"── API call #{api_call_count} "
            f"(búsquedas acumuladas: {total_searches}) ──"
        )

        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            # Adaptive thinking: Claude decide cuánto razonar según la complejidad.
            # No usar budget_tokens (deprecado en Opus 4.6).
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        # Contamos y logueamos cada búsqueda de esta respuesta
        total_searches = count_and_log_searches(response.content, total_searches)

        logger.info(
            f"stop_reason={response.stop_reason!r} | "
            f"búsquedas en este call: {total_searches} total"
        )

        # ── CASO 1: El agente terminó normalmente ────────────────────────────
        if response.stop_reason == "end_turn":
            logger.info(f"Agente terminó. Total de búsquedas: {total_searches}")
            return extract_report(response.content)

        # ── CASO 2: El servidor pausó (límite de 10 iteraciones internas) ────
        if response.stop_reason == "pause_turn":

            # Si ya habíamos pedido el reporte final y aún devuelve pause_turn,
            # forzamos la extracción con lo que hay (evita loop infinito).
            if awaiting_final_report:
                logger.warning(
                    "pause_turn recibido después de solicitar reporte final. "
                    "Extrayendo texto disponible."
                )
                return extract_report(response.content)

            # Construimos el contexto para la próxima llamada.
            # El response.content incluye los resultados de las búsquedas,
            # por lo que el servidor puede continuar sin perder información.
            messages = [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response.content},
            ]

            # Si alcanzamos el límite de búsquedas, pedimos el reporte final
            if total_searches >= MAX_SEARCHES:
                logger.info(
                    f"Límite de {MAX_SEARCHES} búsquedas alcanzado. "
                    "Solicitando reporte final con la información recopilada."
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"Excelente. Has realizado {total_searches} búsquedas web. "
                        "Con toda la información recopilada hasta ahora, "
                        "producí el reporte final completo en el formato exacto "
                        "especificado en el system prompt. "
                        "No realices más búsquedas — solo el reporte."
                    ),
                })
                awaiting_final_report = True

            continue  # siguiente iteración del while

        # ── CASO 3: stop_reason inesperado ───────────────────────────────────
        logger.warning(
            f"stop_reason inesperado: {response.stop_reason!r}. "
            "Intentando extraer texto de todas formas."
        )
        return extract_report(response.content)


# ── Retry wrapper ─────────────────────────────────────────────────────────────

def run_agent_with_retry(client: anthropic.Anthropic) -> str:
    """
    Corre el agente con reintentos ante fallas transitorias de la API.
    Reintentos: MAX_API_RETRIES veces con RETRY_WAIT_SECONDS de espera.
    Solo reintenta ante anthropic.APIError (5xx, rate limit, etc.).
    """
    last_error: Exception | None = None

    for attempt in range(MAX_API_RETRIES + 1):
        try:
            return run_agent(client)
        except anthropic.APIError as e:
            last_error = e
            if attempt < MAX_API_RETRIES:
                logger.error(
                    f"API error (intento {attempt + 1}/{MAX_API_RETRIES + 1}): {e}"
                )
                logger.info(f"Reintentando en {RETRY_WAIT_SECONDS}s...")
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                logger.error(f"Agotados los {MAX_API_RETRIES} reintentos.")

    raise last_error  # type: ignore[misc]


# ── Agente guionista ──────────────────────────────────────────────────────────

def run_script_agent(client: anthropic.Anthropic, research_brief: str) -> list[str]:
    """
    Toma el brief de investigación y genera 3 guiones de Reel.
    Retorna una lista con los 3 guiones como strings separados.
    No usa web search — solo procesa el brief del investigador.
    """
    logger.info("── Agente guionista: generando 3 guiones ──")

    response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=SCRIPT_WRITER_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Este es el brief de investigación de esta semana:\n\n{research_brief}\n\n"
                "Escribí los 3 guiones para los temas #1, #2 y #3. "
                "Seguí exactamente la estructura y el formato indicados."
            ),
        }],
    )

    raw = response.content[0].text
    print(raw, flush=True)

    # Separar los 3 guiones por el delimitador ===GUIÓN #N===
    scripts = []
    for i in range(1, 4):
        marker_start = f"===GUIÓN #{i}==="
        marker_end   = f"===GUIÓN #{i + 1}===" if i < 3 else None
        idx_start = raw.find(marker_start)
        if idx_start == -1:
            logger.warning(f"No se encontró delimitador para guión #{i}")
            continue
        idx_start += len(marker_start)
        idx_end = raw.find(marker_end) if marker_end else len(raw)
        script = raw[idx_start:idx_end].strip()
        if script:
            scripts.append(script)
            logger.info(f"Guión #{i} extraído ({len(script)} chars)")

    if not scripts:
        logger.warning("No se pudieron extraer guiones con el delimitador. Enviando texto completo.")
        scripts = [raw]

    return scripts


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("PUMP TEAM — AGENTE DE TENDENCIAS")
    logger.info(f"Inicio: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    # Validar variables de entorno requeridas según el provider activo
    provider = os.environ.get("WHATSAPP_PROVIDER", "whapi").lower()
    base_vars = ["ANTHROPIC_API_KEY", "WHATSAPP_NUMBER"]
    provider_vars = (
        ["META_ACCESS_TOKEN", "META_PHONE_NUMBER_ID"] if provider == "meta"
        else ["WHAPI_API_URL", "WHAPI_API_TOKEN"]
    )
    missing = [v for v in base_vars + provider_vars if not os.environ.get(v)]
    if missing:
        logger.error(f"Variables de entorno faltantes: {', '.join(missing)}")
        sys.exit(1)

    api_key = os.environ["ANTHROPIC_API_KEY"]
    number  = os.environ["WHATSAPP_NUMBER"]

    client = anthropic.Anthropic(api_key=api_key)

    # ── Agente 1: Investigador ────────────────────────────────────────────────
    logger.info("Iniciando investigación de tendencias...")
    research_brief = run_agent_with_retry(client)

    logger.info("=" * 60)
    logger.info("BRIEF DE INVESTIGACIÓN:")
    logger.info("=" * 60)
    print(research_brief, flush=True)

    logger.info("Enviando brief por WhatsApp...")
    send_whatsapp(research_brief, number)

    # ── Agente 2: Guionista ───────────────────────────────────────────────────
    logger.info("Generando guiones de Reel...")
    scripts = run_script_agent(client, research_brief)

    logger.info("=" * 60)
    logger.info(f"GUIONES GENERADOS: {len(scripts)}")
    logger.info("=" * 60)

    for i, script in enumerate(scripts, 1):
        logger.info(f"Enviando guión #{i} por WhatsApp...")
        send_whatsapp(f"🎬 GUIÓN #{i}\n\n{script}", number)

    logger.info("=" * 60)
    logger.info(f"Fin: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
