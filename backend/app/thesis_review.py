"""Auditoría reproducible del expediente. Solo puntúa afirmaciones con observables."""
import math


def assess(dossier):
    protocol = dossier.get("protocol", {}).get("metrics", [])
    market = dossier.get("observed_market", {})
    security = dossier.get("security_at_read") or {}
    signals = dossier.get("market_signals", {})
    def number(value):
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def positive(field):
        return (number(market.get(field)) or 0) > 0

    evidence = {
        "uso_protocolario": any((number(x.get("value")) or 0) > 0 for x in protocol),
        "dos_comparables": any(len(x.get("peers_by_same_category_and_chain", [])) >= 2 for x in protocol),
        "capitalizacion_y_fdv": positive("marketCap") and positive("fdv"),
        "precio_y_liquidez": positive("price") and positive("liquidity"),
        "seguridad_consultada": bool(security),
    }
    vetoes = []
    if str(security.get("is_honeypot")) == "1":
        vetoes.append("honeypot detectado por GoPlus")
    for field in ("buy_tax", "sell_tax"):
        tax = number(security.get(field))
        if tax is not None and 0.1 < tax <= 1:
            vetoes.append(f"{field} superior a 10% según GoPlus")
    observations = []
    if signals.get("price_up_liquidity_down") is True:
        observations.append("El precio aumentó mientras cayó la liquidez entre las últimas fotos.")
    if evidence["uso_protocolario"]:
        observations.append("TVL atribuida a un protocolo asociado al contrato. La captura de valor del token requiere evidencia propia.")
    # Estas preguntas no son pronósticos. Se congelan para una reevaluación posterior.
    scenarios = [
        {"caso": "adopcion", "observar": "TVL del mismo producto frente a sus pares y mecanismo que beneficia al token", "dias": 90,
         "invalida": "El producto crece sin mecanismo verificable de captura de valor para el token."},
        {"caso": "continuidad", "observar": "Liquidez y desempeño relativo al universo elegible en la misma cadena", "dias": 30,
         "invalida": "La aparente ventaja desaparece después de costos o también aparece en los controles."},
        {"caso": "estres", "observar": "Caída de liquidez, pérdida de precio observable o veto de seguridad confirmado", "dias": 7,
         "invalida": "El activo pierde una vía observable de salida o aparece un veto verificable."},
    ]
    return {"estado": "veto_documentado" if vetoes else "tesis_pendiente",
            "evidencia_disponible": evidence, "vetos": vetoes, "observaciones": observations,
            "preguntas_de_escenario": scenarios,
            "pendientes": ["captura de valor", "detonador con fecha corroborado", "tesis e invalidación específicas", "acuerdo independiente de jueces"]}
