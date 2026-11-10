"""
FinanTech — prototipo funcional de la arquitectura empresarial propuesta.

Encarna en código las capas de Negocio, Datos y Aplicaciones del
trabajo de Arquitectura Empresarial:
  - Datos:        un único repositorio de Cliente (MDM) — models.Cliente
  - Negocio:      el proceso de originación de crédito como flujo único
  - Aplicaciones: rutas /api/* que simulan microservicios detrás de
                  un mismo "gateway" (esta misma app Flask)
  - Seguridad:    identidad centralizada con dos roles (cliente / analista)
"""
import os
import secrets
from datetime import datetime
from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, session, abort)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)

from models import (db, Cliente, Credito, Movimiento, Usuario, ESTADOS_CREDITO,
                    ROL_CLIENTE, ROL_ANALISTA)
from motor_riesgo import evaluar_credito

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def cargar_usuario(user_id):
    return db.session.get(Usuario, int(user_id))


@login_manager.unauthorized_handler
def no_autenticado():
    # Los "microservicios" responden JSON, no redirigen a una página HTML
    if request.path.startswith("/api/"):
        return jsonify(error="Autenticación requerida"), 401
    flash(login_manager.login_message, login_manager.login_message_category)
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


def rol_requerido(rol):
    """Restringe una ruta a un rol. Sin sesión -> login; rol incorrecto -> 403."""
    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(*args, **kwargs):
            if current_user.rol != rol:
                abort(403)
            return vista(*args, **kwargs)
        return envoltura
    return decorador


analista_requerido = rol_requerido(ROL_ANALISTA)


def es_url_segura(destino):
    """Evita open-redirect: solo se acepta redirigir a esta misma aplicación."""
    if not destino:
        return False
    base = urlparse(request.host_url)
    objetivo = urlparse(urljoin(request.host_url, destino))
    return objetivo.scheme in ("http", "https") and base.netloc == objetivo.netloc


def inicio_segun_rol():
    if current_user.es_analista:
        return url_for("dashboard")
    return url_for("mi_panel")


def create_app():
    app = Flask(__name__)
    # En producción define SECRET_KEY como variable de entorno.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "finantech-dev-secret-cambiar")
    # En Render puedes definir DATABASE_URL (p. ej. PostgreSQL); si no, usa SQLite local.
    uri = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'finantech.db')}")
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        # Primer arranque (p. ej. en Render con BD vacía): cargar datos de demo.
        if os.environ.get("CARGAR_DEMO", "1") == "1" and db.session.query(Usuario).count() == 0:
            from seed import cargar_datos
            cargar_datos()

    # ---------------- CSRF sencillo para todos los formularios POST ----------------
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def proteger_csrf():
        if request.method == "POST":
            esperado = session.get("_csrf", "")
            recibido = request.form.get("_csrf", "")
            if not esperado or not secrets.compare_digest(esperado, recibido):
                abort(400)

    @app.errorhandler(400)
    def error_400(e):
        return render_template("error.html", codigo=400, titulo="Solicitud no válida",
                               mensaje="La sesión del formulario expiró. Vuelve atrás y reintenta."), 400

    @app.errorhandler(403)
    def error_403(e):
        if request.path.startswith("/api/"):
            return jsonify(error="No tienes permiso para este recurso"), 403
        return render_template("error.html", codigo=403, titulo="Acceso restringido",
                               mensaje="Tu perfil no tiene permiso para ver esta sección."), 403

    @app.errorhandler(404)
    def error_404(e):
        return render_template("error.html", codigo=404, titulo="No encontrado",
                               mensaje="Lo que buscas no existe o no está disponible para tu cuenta."), 404

    register_routes(app)
    return app


def register_routes(app):

    def _registrar(credito, descripcion, audiencia="todos"):
        db.session.add(Movimiento(credito_id=credito.id, descripcion=descripcion,
                                  audiencia=audiencia))

    def _credito_visible_o_404(credito_id):
        """Un cliente solo puede ver SUS créditos; el analista ve todos."""
        credito = db.session.get(Credito, credito_id)
        if credito is None:
            abort(404)
        if current_user.es_cliente and credito.cliente_id != current_user.cliente_id:
            abort(404)  # 404 y no 403: no revelamos que ese crédito existe
        return credito

    # ===============================================================
    # Autenticación
    # ===============================================================
    @app.route("/")
    def inicio():
        if current_user.is_authenticated:
            return redirect(inicio_segun_rol())
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(inicio_segun_rol())

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            usuario = Usuario.query.filter_by(email=email).first()

            if usuario and usuario.check_password(password):
                login_user(usuario)
                destino = request.args.get("next")
                if es_url_segura(destino):
                    return redirect(destino)
                return redirect(inicio_segun_rol())

            # Mensaje genérico: no revela si el correo existe
            flash("Correo o contraseña incorrectos.", "danger")

        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        session.pop("_csrf", None)
        flash("Cerraste sesión correctamente.", "success")
        return redirect(url_for("login"))

    @app.route("/registro", methods=["GET", "POST"])
    def registro():
        """Auto-registro SOLO para clientes. Las cuentas de analista las crea FinanTech."""
        if current_user.is_authenticated:
            return redirect(inicio_segun_rol())

        if request.method == "POST":
            nombre = request.form.get("nombre", "").strip()
            identificacion = request.form.get("identificacion", "").strip()
            email = request.form.get("email", "").strip().lower()
            telefono = request.form.get("telefono", "").strip()
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            try:
                ingresos = float(request.form.get("ingresos_mensuales") or 0)
            except ValueError:
                ingresos = -1

            error = None
            if not (nombre and identificacion and email):
                error = "Nombre, identificación y correo son obligatorios."
            elif ingresos < 0:
                error = "Los ingresos mensuales no son válidos."
            elif len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres."
            elif password != password2:
                error = "Las contraseñas no coinciden."
            elif Usuario.query.filter_by(email=email).first():
                error = "Ya existe una cuenta con ese correo."

            cliente = None
            if not error:
                cliente = Cliente.query.filter_by(identificacion=identificacion).first()
                if cliente:
                    # Ya existe en el MDM (lo registró un analista): se enlaza la
                    # cuenta a ese mismo registro, pero solo si el correo coincide,
                    # para que nadie reclame la identidad de otra persona.
                    if cliente.usuario is not None:
                        error = "Esa identificación ya tiene una cuenta. Inicia sesión."
                    elif cliente.email.strip().lower() != email:
                        error = ("Esa identificación ya está registrada con otro correo. "
                                 "Contacta a FinanTech para actualizar tus datos.")

            if error:
                flash(error, "danger")
                return render_template("registro.html", form=request.form)

            if cliente is None:
                cliente = Cliente(nombre=nombre, identificacion=identificacion,
                                  email=email, telefono=telefono,
                                  ingresos_mensuales=ingresos, score=650)
                db.session.add(cliente)
                db.session.flush()

            usuario = Usuario(nombre=cliente.nombre, email=email, rol=ROL_CLIENTE,
                              cliente_id=cliente.id)
            usuario.set_password(password)
            db.session.add(usuario)
            db.session.commit()

            login_user(usuario)
            flash("¡Cuenta creada! Ya puedes solicitar tu primer crédito.", "success")
            return redirect(url_for("mi_panel"))

        return render_template("registro.html", form={})

    # ===============================================================
    # Vista del CLIENTE
    # ===============================================================
    @app.route("/mi-panel")
    @rol_requerido(ROL_CLIENTE)
    def mi_panel():
        cliente = current_user.cliente
        creditos = sorted(cliente.creditos, key=lambda c: c.fecha_solicitud, reverse=True)
        return render_template("mi_panel.html", cliente=cliente, creditos=creditos)

    # ===============================================================
    # Vista del ANALISTA — dashboard y clientes (MDM)
    # ===============================================================
    @app.route("/panel")
    @analista_requerido
    def dashboard():
        total_clientes = Cliente.query.count()
        creditos = Credito.query.all()
        total_desembolsado = sum(c.monto for c in creditos if c.estado == "Desembolsado")
        pendientes = [c for c in creditos if c.estado in ("Solicitado", "En evaluación")]
        aprobados = len([c for c in creditos if c.estado in ("Aprobado", "Desembolsado")])
        rechazados = len([c for c in creditos if c.estado == "Rechazado"])

        ultimos_movimientos = (
            Movimiento.query.filter(Movimiento.audiencia.in_(["todos", "analista"]))
            .order_by(Movimiento.fecha.desc()).limit(8).all()
        )

        return render_template(
            "dashboard.html",
            total_clientes=total_clientes,
            total_creditos=len(creditos),
            total_desembolsado=total_desembolsado,
            pendientes=pendientes,
            aprobados=aprobados,
            rechazados=rechazados,
            ultimos_movimientos=ultimos_movimientos,
        )

    @app.route("/clientes")
    @analista_requerido
    def lista_clientes():
        q = request.args.get("q", "").strip()
        query = Cliente.query
        if q:
            query = query.filter(
                db.or_(
                    Cliente.nombre.ilike(f"%{q}%"),
                    Cliente.identificacion.ilike(f"%{q}%"),
                )
            )
        clientes = query.order_by(Cliente.nombre).all()
        return render_template("clientes.html", clientes=clientes, q=q)

    @app.route("/clientes/nuevo", methods=["GET", "POST"])
    @analista_requerido
    def nuevo_cliente():
        if request.method == "POST":
            identificacion = request.form["identificacion"].strip()
            if Cliente.query.filter_by(identificacion=identificacion).first():
                flash("Ya existe un cliente con esa identificación en el repositorio.", "danger")
                return render_template("form_cliente.html")
            try:
                ingresos = float(request.form.get("ingresos_mensuales") or 0)
                score = int(request.form.get("score") or 650)
            except ValueError:
                flash("Ingresos y score deben ser numéricos.", "danger")
                return render_template("form_cliente.html")

            cliente = Cliente(
                nombre=request.form["nombre"].strip(),
                identificacion=identificacion,
                email=request.form["email"].strip().lower(),
                telefono=request.form.get("telefono", "").strip(),
                ingresos_mensuales=ingresos,
                score=score,
            )
            db.session.add(cliente)
            db.session.commit()
            flash(f"Cliente {cliente.nombre} creado en el repositorio único (MDM).", "success")
            return redirect(url_for("detalle_cliente", cliente_id=cliente.id))
        return render_template("form_cliente.html")

    @app.route("/clientes/<int:cliente_id>")
    @analista_requerido
    def detalle_cliente(cliente_id):
        cliente = db.get_or_404(Cliente, cliente_id)
        return render_template("cliente_detalle.html", cliente=cliente)

    # ===============================================================
    # Créditos — proceso integrado (ambos roles, con permisos distintos)
    # ===============================================================
    @app.route("/creditos")
    @analista_requerido
    def lista_creditos():
        estado = request.args.get("estado", "")
        query = Credito.query
        if estado in ESTADOS_CREDITO:
            query = query.filter_by(estado=estado)
        creditos = query.order_by(Credito.fecha_solicitud.desc()).all()
        return render_template(
            "creditos.html", creditos=creditos, estados=ESTADOS_CREDITO, estado_actual=estado
        )

    @app.route("/creditos/nuevo", methods=["GET", "POST"])
    @login_required
    def nuevo_credito():
        # El cliente solo puede solicitar a su propio nombre; el analista puede
        # registrar una solicitud en nombre de cualquier cliente (atención presencial).
        if current_user.es_cliente:
            clientes = [current_user.cliente]
            cliente_preseleccionado = current_user.cliente_id
        else:
            clientes = Cliente.query.order_by(Cliente.nombre).all()
            cliente_preseleccionado = request.args.get("cliente_id", type=int)

        if request.method == "POST":
            if current_user.es_cliente:
                cliente_id = current_user.cliente_id
            else:
                cliente_id = request.form.get("cliente_id", type=int)
                if not db.session.get(Cliente, cliente_id or 0):
                    flash("Selecciona un cliente válido.", "danger")
                    return redirect(url_for("nuevo_credito"))
            try:
                monto = float(request.form["monto"])
                plazo = int(request.form["plazo_meses"])
            except (ValueError, KeyError):
                flash("Monto y plazo deben ser numéricos.", "danger")
                return redirect(url_for("nuevo_credito"))
            if monto < 100_000 or not (1 <= plazo <= 60):
                flash("El monto mínimo es $100.000 y el plazo debe estar entre 1 y 60 meses.", "danger")
                return redirect(url_for("nuevo_credito"))

            credito = Credito(
                cliente_id=cliente_id,
                monto=monto,
                plazo_meses=plazo,
                proposito=request.form.get("proposito", "").strip()[:200],
                estado="Solicitado",
            )
            db.session.add(credito)
            db.session.flush()
            origen = ("el cliente vía canal digital (autenticado)" if current_user.es_cliente
                      else f"el analista {current_user.nombre} en nombre del cliente")
            _registrar(credito, f"Solicitud creada por {origen}.")
            db.session.commit()
            flash("Solicitud registrada. El siguiente paso es la evaluación de riesgo.", "success")
            return redirect(url_for("detalle_credito", credito_id=credito.id))

        return render_template(
            "form_credito.html", clientes=clientes, cliente_preseleccionado=cliente_preseleccionado
        )

    @app.route("/creditos/<int:credito_id>")
    @login_required
    def detalle_credito(credito_id):
        credito = _credito_visible_o_404(credito_id)
        audiencias = ("todos", "analista") if current_user.es_analista else ("todos", "cliente")
        movimientos = [m for m in credito.movimientos if m.audiencia in audiencias]
        return render_template("credito_detalle.html", credito=credito, pasos=Credito.PASOS,
                               movimientos=movimientos)

    @app.route("/creditos/<int:credito_id>/evaluar", methods=["POST"])
    @analista_requerido
    def evaluar(credito_id):
        credito = db.get_or_404(Credito, credito_id)
        if credito.estado != "Solicitado":
            flash("Solo se puede evaluar una solicitud en estado «Solicitado».", "danger")
            return redirect(url_for("detalle_credito", credito_id=credito.id))

        aprueba, score, tasa, motivo = evaluar_credito(credito.cliente, credito)
        credito.score_evaluacion = score
        credito.fecha_decision = datetime.utcnow()
        quien = current_user.nombre

        if aprueba:
            credito.estado = "Aprobado"
            credito.tasa_interes = tasa
            _registrar(credito, f"Evaluación de riesgo ({quien}): APROBADO (score {score}). {motivo}", "analista")
            _registrar(credito, "Tu solicitud fue aprobada. Pronto se realizará el desembolso.", "cliente")
            flash("Crédito aprobado por el motor de evaluación de riesgo.", "success")
        else:
            credito.estado = "Rechazado"
            _registrar(credito, f"Evaluación de riesgo ({quien}): RECHAZADO (score {score}). {motivo}", "analista")
            _registrar(credito, "Tu solicitud no fue aprobada en esta ocasión. "
                                "Contacta a un asesor para conocer más opciones.", "cliente")
            flash("Crédito rechazado por el motor de evaluación de riesgo.", "warning")

        db.session.commit()
        return redirect(url_for("detalle_credito", credito_id=credito.id))

    @app.route("/creditos/<int:credito_id>/desembolsar", methods=["POST"])
    @analista_requerido
    def desembolsar(credito_id):
        credito = db.get_or_404(Credito, credito_id)
        if credito.estado != "Aprobado":
            flash("Solo se puede desembolsar un crédito previamente aprobado.", "danger")
            return redirect(url_for("detalle_credito", credito_id=credito.id))

        credito.estado = "Desembolsado"
        credito.fecha_desembolso = datetime.utcnow()
        _registrar(
            credito,
            f"Desembolso ejecutado por Tesorería ({current_user.nombre}). "
            f"Registro contable generado por ${credito.monto:,.0f}.",
            "analista",
        )
        _registrar(credito, f"Tu crédito fue desembolsado por ${credito.monto:,.0f}.", "cliente")
        db.session.commit()
        flash("Desembolso realizado y registrado en Contabilidad.", "success")
        return redirect(url_for("detalle_credito", credito_id=credito.id))

    # ===============================================================
    # "API Gateway" — microservicios; solo personal de FinanTech
    # ===============================================================
    @app.route("/api/clientes")
    @analista_requerido
    def api_clientes():
        return jsonify([
            {"id": c.id, "nombre": c.nombre, "identificacion": c.identificacion,
             "score": c.score, "deuda_total": c.deuda_total}
            for c in Cliente.query.all()
        ])

    @app.route("/api/creditos")
    @analista_requerido
    def api_creditos():
        return jsonify([
            {"id": cr.id, "cliente": cr.cliente.nombre, "monto": cr.monto,
             "estado": cr.estado, "tasa_interes": cr.tasa_interes}
            for cr in Credito.query.all()
        ])

    @app.route("/api/dashboard")
    @analista_requerido
    def api_dashboard():
        creditos = Credito.query.all()
        return jsonify({
            "total_clientes": Cliente.query.count(),
            "total_creditos": len(creditos),
            "total_desembolsado": sum(c.monto for c in creditos if c.estado == "Desembolsado"),
            "por_estado": {e: len([c for c in creditos if c.estado == e]) for e in ESTADOS_CREDITO},
        })


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1" or not os.environ.get("RENDER"),
            host="127.0.0.1", port=5000)
