"""
Motor de evaluación de riesgo — capa de Aplicaciones (microservicio "Evaluación").

Reglas simples a propósito: lo importante para el caso de estudio es
mostrar que la decisión se toma con el dato único del cliente (MDM),
no con información repetida o inconsistente entre sistemas.
"""


def evaluar_credito(cliente, credito):
    """Devuelve (aprueba: bool, score: int, tasa: float, motivo: str)."""
    score = cliente.score

    # Penaliza si la cuota superaría el 40% de los ingresos declarados
    ingresos = cliente.ingresos_mensuales or 1
    tasa_base = 18.0

    cuota_estimada = credito.monto / credito.plazo_meses
    relacion_cuota_ingreso = cuota_estimada / ingresos

    if relacion_cuota_ingreso > 0.4:
        score -= 80
    elif relacion_cuota_ingreso > 0.25:
        score -= 30

    # Penaliza montos altos a plazos muy cortos (presión de liquidez)
    if credito.plazo_meses < 6 and credito.monto > 5_000_000:
        score -= 40

    # Bonifica clientes con historial de crédito ya desembolsado y sin mora
    creditos_previos = [c for c in cliente.creditos if c.id != credito.id]
    if any(c.estado == "Desembolsado" for c in creditos_previos):
        score += 20

    aprueba = score >= 620

    if aprueba:
        if score >= 750:
            tasa = tasa_base - 4
        elif score >= 680:
            tasa = tasa_base - 2
        else:
            tasa = tasa_base
        motivo = "Score suficiente y relación cuota/ingreso dentro del límite permitido."
    else:
        tasa = None
        if relacion_cuota_ingreso > 0.4:
            motivo = "La cuota estimada supera el 40% de los ingresos declarados del cliente."
        else:
            motivo = "El score resultante quedó por debajo del mínimo requerido (620)."

    return aprueba, max(score, 300), tasa, motivo
