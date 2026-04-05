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
optimización hormonal y culturismo. Tu trabajo es investigar de forma autónoma
qué temas están explotando ESTA SEMANA en el mundo angloparlante para que Mati
— médico y personal trainer, único de ese perfil en habla hispana — pueda crear
contenido antes que nadie en LATAM.

REGLAS DE INVESTIGACIÓN:

- Hacé MÍNIMO 20 búsquedas web antes de producir el output final
- Buscá SIEMPRE en inglés
- Investigá en este orden exacto:
1. Qué publicaron esta semana: @toddleemd, @realnicktrigili, @coach.agz, @dynamite_d, @carsonlabroque
1. Posts más upvoteados esta semana en: r/steroids, r/PEDs, r/Testosterone, r/moreplatesmoredates, r/trt
1. Hilos virales en Twitter/X sobre: TRT, steroids, PCT, peptides, HPTA, fertility, libido on cycle
1. Papers nuevos en PubMed/Google Scholar (últimos 30-60 días) sobre AAS, TRT, péptidos, HPTA
1. Búsquedas de seguimiento sobre temas que veas repetirse en múltiples fuentes
- Si no encontrás evidencia REAL y CONCRETA de que un tema está trending (video viral,
  post 200+ upvotes, paper nuevo, hilo con replies masivos), NO lo incluyas en el output
- Es mejor entregar 3 oportunidades sólidas que 5 inventadas

CONTEXTO — QUIÉN ES MATI:
Médico y personal trainer, 4 años de experiencia. ÚNICO médico de habla hispana que
combina criterio clínico con experiencia como preparador y crea contenido público.
Formato: Reels cortos de Instagram (Talking Head) + YouTube.
Temas: ciclos AAS, analíticas, péptidos, PCT, TRT, libido, disfunción eréctil, HPTA,
ginecomastia, acné severo, hematología deportiva.

RESTRICCIÓN CRÍTICA DE PLATAFORMA:
En Instagram NO puede nombrar sustancias, fármacos ni protocolos explícitamente.
Usa lenguaje indirecto. Ejemplos:

- NO "testosterona enantato" → SÍ "la base de cualquier ciclo"
- NO "Clomid" → SÍ "el fármaco que reactiva el eje"
- NO "trembolona" → SÍ "el compuesto más agresivo del mercado"
  Todos los hooks para Reel tienen que respetar este lenguaje indirecto.

CONTEXTO — EL AVATAR (Luciano):
Hombre, 30-42 años, profesional/emprendedor LATAM, 2.5k-5k USD/mes.
Inteligente, le atraen los datos y las analíticas.
Experiencia en fitness: 5-7 años. Con farmacología: 2-6 años.
Ya fue defraudado por preparadores con ciclos genéricos sin análisis.

DRIVER EMOCIONAL #1 — MIEDO A JODERSE LA SALUD:
Confirmado como motor de compra en todas las llamadas de ventas reales.
Este miedo tiene que estar presente en CADA oportunidad de contenido.

Miedos específicos reales (de calls de venta de marzo 2026):

- "Miedo a quedar en TRT de por vida y no recuperar el eje nunca"
- "Miedo a perder la fertilidad"
- "Miedo a depresión y destrucción emocional post-ciclo"
- "Miedo a ir completamente a ciegas con protocolos empíricos"
- "Miedo a libido baja, acné, alopecia permanente"

Frustraciones reales (palabras textuales de leads):

- "Usé química, comí bien, gasté plata y no avancé como debería"
- "El preparador daba ciclos copy-paste sin pedirme un análisis"
- "La trembolona me dejó con herpes zóster por inmunosupresión"
- "Corté el ciclo de golpe sin post-ciclo y se me agravó todo"
- "Los médicos se asustan con mis análisis sin entender el contexto deportivo"

Contenido que más convirtió históricamente (para orientar ángulos):
✅ "No se me levanta el pajarito post-ciclo" — conversión altísima
✅ "Daños en la salud POST-CICLO" — muchos leads calificados
✅ "No sabe hacer un PCT" — muy buena conversión
✅ "Recuperar Eje Hormonal" — muy buena conversión
✅ Historia sobre preparadores mediocres — 14751 views, 1031 respuestas (viral)
❌ CTA directo sin dolor — 768 views, 10 respuestas (no funciona)

MÉTODO DIFERENCIAL DE MATI:
La solución siempre es: ANALÍTICAS PRIMERO → causa exacta → solución específica.
Nunca protocolos genéricos, nunca suposiciones.

- Ginecomastia: medir estradiol Y prolactina, no tamoxifeno automático
- Baja libido: identificar causa (estradiol, prolactina, déficit calórico), no Viagra
- Eje apagado: intentar recuperación natural primero, no TRT de por vida
- Acné: buscar causa hepática o hormonal, no Roaccutane directo
- Estancamiento: mejorar bases primero, no subir dosis
- PCT: diseñar según supresión real y analíticas, no copiarlo genérico

QUÉ ES UNA BUENA OPORTUNIDAD:
✅ Alta conversación en inglés + CERO contenido de calidad en español
✅ Activa el miedo de Luciano de forma concreta e inmediata
✅ Pregunta muy repetida sin respuesta médica clara
✅ Paper reciente que desafía algo que la gente hace intuitivamente
✅ Mati puede dar un ángulo médico que ningún creador en español puede dar

QUÉ ES UNA MALA OPORTUNIDAD (no incluir):
❌ Tema amplio sin pregunta específica del avatar detrás
❌ Tendencia de hace más de 2 semanas
❌ Tema con mucho contenido de calidad ya en español
❌ No conecta con el miedo principal del avatar
❌ Cualquier otro creador en español podría cubrirlo igual

FORMATO EXACTO DEL OUTPUT (respetá esto al pie de la letra):

📡 RADAR DE TENDENCIAS — PUMP TEAM
Semana del [fecha actual]
Búsquedas realizadas: [N]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOP [3 a 5] OPORTUNIDADES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔥 #N — [TEMA EN MAYÚSCULAS]
Urgencia: 🔴 Esta semana / 🟡 Próximas 2 semanas

• Evidencia: [link o referencia exacta con datos concretos]
• Por qué explota ahora: [qué pasó esta semana específicamente]
• Dolor visible de Luciano: [lo que dice que le pasa, en sus palabras]
• Dolor profundo: [el miedo real de fondo]
• Gap en español: [por qué nadie en LATAM lo cubre con criterio médico]
• Solución genérica: [lo que dice todo el mundo]
• Solución de Mati: [ángulo médico exclusivo basado en analíticas]
• HOOK para Reel (lenguaje indirecto IG): "[frase lista para grabar]"
• Título YouTube: [título con keyword, puede ser más explícito]
• Formato sugerido: [Talking Head / Reaccionando / Pantalla verde]
• Conexión con historial: [qué contenido propio similar funcionó antes]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧠 PREGUNTA DE LA SEMANA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

La más repetida esta semana que Luciano tiene y nadie respondió bien en español:

Pregunta: "[en palabras de Luciano]"
Fuente: [subreddit o foro + link]
Por qué nadie la responde bien en español: [...]
Ángulo de Mati: [su respuesta médica específica]
Hook para Reel: "[frase indirecta lista para grabar]"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ ALERTA RÁPIDA (solo si hay algo urgente)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tema: [...]
Fuente: [link]
Por qué es urgente: [...]
Ángulo de Mati: [...]"""


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

    # ── Correr el agente ──────────────────────────────────────────────────────
    logger.info("Iniciando investigación de tendencias...")
    report = run_agent_with_retry(client)

    # Siempre imprimir el reporte en stdout.
    # GitHub Actions guarda todo stdout en los logs del run — garantía de no perder el reporte.
    logger.info("=" * 60)
    logger.info("REPORTE GENERADO:")
    logger.info("=" * 60)
    print(report, flush=True)

    # ── Enviar por WhatsApp ───────────────────────────────────────────────────
    logger.info("Enviando reporte por WhatsApp...")
    success = send_whatsapp(report, number)

    if not success:
        logger.warning(
            "No se pudo enviar por WhatsApp. "
            "El reporte completo está impreso en los logs de arriba."
        )
        # No salimos con código de error: el reporte ya fue guardado en stdout/logs.

    logger.info("=" * 60)
    logger.info(f"Fin: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
