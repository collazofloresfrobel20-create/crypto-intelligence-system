"""
Alertas por Telegram cuando aparece un veredicto relevante (por defecto: Strong Opportunity).
Completamente opcional: si no se configuran TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, las llamadas
simplemente no hacen nada (no rompen el pipeline).

Cómo conseguir los valores (el usuario debe hacerlo, no se puede automatizar desde aquí):
  1. En Telegram, busca @BotFather y mándale /newbot. Sigue las instrucciones -> te da un
     TELEGRAM_BOT_TOKEN (algo como "123456:ABC-DEF...").
  2. Manda cualquier mensaje a tu bot recién creado (para que Telegram registre el chat).
  3. Abre https://api.telegram.org/bot<TU_TOKEN>/getUpdates en el navegador y busca
     "chat":{"id": ...} en la respuesta -> ese número es tu TELEGRAM_CHAT_ID.
  4. Pon ambos valores en backend/.env.
"""
import requests

from .config import settings

_session = requests.Session()


def notify_verdict(prediction: dict):
    """Envía una alerta si el veredicto de `prediction` está en TELEGRAM_NOTIFY_VERDICTS."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return
    if prediction.get("verdict") not in settings.TELEGRAM_NOTIFY_VERDICTS:
        return

    text = (
        f"{prediction.get('verdict')}: {prediction.get('symbol')} ({prediction.get('name') or ''})\n"
        f"Opportunity {prediction.get('opportunity_score')} · Risk {prediction.get('risk_score')} · "
        f"Confidence {prediction.get('confidence_score')} · Earliness {prediction.get('earliness_score')}\n"
        f"Evidencia: {prediction.get('evidence_tier')}\n"
        f"{prediction.get('system_note') or ''}"
    )
    send_message(text)


def send_message(text: str):
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        _session.post(url, json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass  # una alerta fallida no debe tumbar el pipeline
