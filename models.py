"""
Modelos de datos de FinanTech.

Representan la capa de Datos de la arquitectura propuesta: un único
repositorio de Cliente (MDM) del que se alimentan los módulos de
Créditos, Evaluación y Contabilidad.
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

ROL_CLIENTE = "cliente"
ROL_ANALISTA = "analista"

ESTADOS_CREDITO = [
    "Solicitado",
    "En evaluación",
    "Aprobado",
    "Rechazado",
    "Desembolsado",
]


class Usuario(UserMixin, db.Model):
    """Credencial de acceso. Un usuario es un cliente o personal de FinanTech (analista).

    Si es cliente, queda enlazado a su registro único en `Cliente` (MDM):
    la identidad que inicia sesión es la misma que ven Créditos y Contabilidad.
    """
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default=ROL_CLIENTE)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), unique=True)

    cliente = db.relationship("Cliente", backref=db.backref("usuario", uselist=False))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def es_analista(self):
        return self.rol == ROL_ANALISTA

    @property
    def es_cliente(self):
        return self.rol == ROL_CLIENTE


class Cliente(db.Model):
    """Dato maestro único de cliente (MDM) — capa de Datos."""
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    identificacion = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(20))
    ingresos_mensuales = db.Column(db.Float, default=0)
    score = db.Column(db.Integer, default=650)  # score crediticio simulado
    fecha_registro = db.Column(db.DateTime, default=datetime.utcnow)

    creditos = db.relationship("Credito", backref="cliente", lazy=True,
                                cascade="all, delete-orphan")

    @property
    def creditos_activos(self):
        return [c for c in self.creditos if c.estado in ("Aprobado", "Desembolsado")]

    @property
    def deuda_total(self):
        return sum(c.monto for c in self.creditos if c.estado == "Desembolsado")


class Credito(db.Model):
    """Solicitud de crédito — capa de Negocio (proceso de originación)."""
    __tablename__ = "creditos"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    plazo_meses = db.Column(db.Integer, nullable=False)
    proposito = db.Column(db.String(200))
    estado = db.Column(db.String(30), default="Solicitado")
    score_evaluacion = db.Column(db.Integer)
    tasa_interes = db.Column(db.Float)
    fecha_solicitud = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_decision = db.Column(db.DateTime)
    fecha_desembolso = db.Column(db.DateTime)

    movimientos = db.relationship("Movimiento", backref="credito", lazy=True,
                                   cascade="all, delete-orphan",
                                   order_by="Movimiento.fecha")

    @property
    def cuota_mensual(self):
        if not self.tasa_interes or not self.plazo_meses:
            return self.monto / max(self.plazo_meses, 1)
        i = self.tasa_interes / 12 / 100
        n = self.plazo_meses
        if i == 0:
            return self.monto / n
        return self.monto * (i * (1 + i) ** n) / ((1 + i) ** n - 1)

    PASOS = ["Solicitado", "En evaluación", "Aprobado", "Desembolsado"]

    @property
    def paso_actual(self):
        if self.estado == "Rechazado":
            return 2  # se detiene después de evaluación
        try:
            return self.PASOS.index(self.estado)
        except ValueError:
            return 0


class Movimiento(db.Model):
    """Bitácora de auditoría del proceso — trazabilidad de la capa de Datos."""
    __tablename__ = "movimientos"

    id = db.Column(db.Integer, primary_key=True)
    credito_id = db.Column(db.Integer, db.ForeignKey("creditos.id"), nullable=False)
    descripcion = db.Column(db.String(300), nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    # Quién puede ver este movimiento: "todos", "analista" (detalle interno de
    # riesgo/contabilidad) o "cliente" (mensaje en lenguaje simple para el cliente).
    audiencia = db.Column(db.String(10), nullable=False, default="todos")
