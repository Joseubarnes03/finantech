"""Carga datos de ejemplo para que la demo no arranque vacía."""
from datetime import datetime, timedelta

from models import db, Cliente, Credito, Movimiento, Usuario, ROL_ANALISTA, ROL_CLIENTE

CLIENTES = [
    dict(nombre="Ana María Torres", identificacion="1023456789", email="ana.torres@mail.com",
         telefono="3001234567", ingresos_mensuales=3_500_000, score=720),
    dict(nombre="Carlos Pérez Gómez", identificacion="1098765432", email="carlos.perez@mail.com",
         telefono="3017654321", ingresos_mensuales=2_200_000, score=640),
    dict(nombre="Luisa Fernanda Rojas", identificacion="1045678912", email="luisa.rojas@mail.com",
         telefono="3109988776", ingresos_mensuales=5_000_000, score=790),
    dict(nombre="Jorge Andrés Martínez", identificacion="1067891234", email="jorge.martinez@mail.com",
         telefono="3123344556", ingresos_mensuales=1_800_000, score=590),
]

def cargar_datos(reiniciar=False):
    """Carga los datos de ejemplo. Requiere app context."""
    if reiniciar:
        db.drop_all()
    db.create_all()

    clientes = []
    for data in CLIENTES:
        c = Cliente(**data)
        db.session.add(c)
        clientes.append(c)
    db.session.commit()

    ana, carlos, luisa, jorge = clientes

    # Crédito ya desembolsado (historial positivo para Ana)
    cr1 = Credito(cliente_id=ana.id, monto=4_000_000, plazo_meses=18,
                  proposito="Remodelación de vivienda", estado="Desembolsado",
                  score_evaluacion=745, tasa_interes=14.0,
                  fecha_solicitud=datetime.utcnow() - timedelta(days=40),
                  fecha_decision=datetime.utcnow() - timedelta(days=39),
                  fecha_desembolso=datetime.utcnow() - timedelta(days=38))
    db.session.add(cr1)
    db.session.flush()
    db.session.add_all([
        Movimiento(credito_id=cr1.id, descripcion="Solicitud creada por el cliente vía canal digital (autenticado).",
                   fecha=cr1.fecha_solicitud),
        Movimiento(credito_id=cr1.id, descripcion="Tu solicitud fue aprobada.", fecha=cr1.fecha_decision, audiencia="cliente"),
        Movimiento(credito_id=cr1.id, descripcion="Tu crédito fue desembolsado por $4,000,000.", fecha=cr1.fecha_desembolso, audiencia="cliente"),
        Movimiento(credito_id=cr1.id, descripcion="Evaluación de riesgo: APROBADO (score 745).", audiencia="analista",
                   fecha=cr1.fecha_decision),
        Movimiento(credito_id=cr1.id, descripcion="Desembolso ejecutado por Tesorería. Registro contable generado.", audiencia="analista",
                   fecha=cr1.fecha_desembolso),
    ])

    # Crédito pendiente de evaluación (Carlos)
    cr2 = Credito(cliente_id=carlos.id, monto=2_500_000, plazo_meses=12,
                  proposito="Capital de trabajo", estado="Solicitado",
                  fecha_solicitud=datetime.utcnow() - timedelta(hours=5))
    db.session.add(cr2)
    db.session.flush()
    db.session.add(Movimiento(credito_id=cr2.id,
                               descripcion="Solicitud creada por el cliente vía canal digital (autenticado).",
                               fecha=cr2.fecha_solicitud))

    # Crédito aprobado, pendiente de desembolso (Luisa)
    cr3 = Credito(cliente_id=luisa.id, monto=8_000_000, plazo_meses=24,
                  proposito="Libre inversión", estado="Aprobado",
                  score_evaluacion=800, tasa_interes=12.0,
                  fecha_solicitud=datetime.utcnow() - timedelta(days=2),
                  fecha_decision=datetime.utcnow() - timedelta(days=1))
    db.session.add(cr3)
    db.session.flush()
    db.session.add_all([
        Movimiento(credito_id=cr3.id, descripcion="Solicitud creada por el cliente vía canal digital (autenticado).",
                   fecha=cr3.fecha_solicitud),
        Movimiento(credito_id=cr3.id, descripcion="Tu solicitud fue aprobada. Pronto se realizará el desembolso.", fecha=cr3.fecha_decision, audiencia="cliente"),
        Movimiento(credito_id=cr3.id, descripcion="Evaluación de riesgo: APROBADO (score 800).", audiencia="analista",
                   fecha=cr3.fecha_decision),
    ])

    # Crédito rechazado (Jorge)
    cr4 = Credito(cliente_id=jorge.id, monto=6_000_000, plazo_meses=6,
                  proposito="Compra de vehículo", estado="Rechazado",
                  score_evaluacion=540,
                  fecha_solicitud=datetime.utcnow() - timedelta(days=7),
                  fecha_decision=datetime.utcnow() - timedelta(days=6))
    db.session.add(cr4)
    db.session.flush()
    db.session.add_all([
        Movimiento(credito_id=cr4.id, descripcion="Solicitud creada por el cliente vía canal digital (autenticado).",
                   fecha=cr4.fecha_solicitud),
        Movimiento(credito_id=cr4.id, descripcion="Tu solicitud no fue aprobada en esta ocasión. Contacta a un asesor para conocer más opciones.", fecha=cr4.fecha_decision, audiencia="cliente"),
        Movimiento(credito_id=cr4.id,
                   descripcion="Evaluación de riesgo: RECHAZADO (score 540). La cuota estimada supera el 40% de los ingresos.", audiencia="analista",
                   fecha=cr4.fecha_decision),
    ])

    # --- Usuarios (inicio de sesión) ---
    analista = Usuario(nombre="Laura Gómez (Analista de crédito)", email="analista@finantech.com", rol=ROL_ANALISTA)
    analista.set_password("Analista123")
    db.session.add(analista)
    for cli in (ana, carlos, luisa, jorge):
        u = Usuario(nombre=cli.nombre, email=cli.email, rol=ROL_CLIENTE, cliente_id=cli.id)
        u.set_password("Cliente123")
        db.session.add(u)

    db.session.commit()
    print("Datos de ejemplo cargados correctamente.")


if __name__ == "__main__":
    from app import create_app
    with create_app().app_context():
        cargar_datos(reiniciar=True)
