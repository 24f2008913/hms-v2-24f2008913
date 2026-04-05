from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, send_from_directory
from flask_caching import Cache
from flask_jwt_extended import JWTManager, get_jwt, verify_jwt_in_request
from flask_mail import Mail
from flask_migrate import Migrate
from flask_cors import CORS

from backend.config import Config
from backend.models import db


jwt = JWTManager()
cache = Cache()
mail = Mail()
migrate = Migrate()


def role_required(*allowed_roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            role = claims.get("role")
            is_active = claims.get("is_active", False)
            if not is_active:
                return jsonify({"error": "Your account is inactive"}), 403
            if role not in allowed_roles:
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def create_app():
    app = Flask(__name__, template_folder="../frontend")
    app.config.from_object(Config)

    db.init_app(app)
    jwt.init_app(app)
    cache.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)
    CORS(app)

    from backend.routes.auth import auth_bp
    from backend.routes.admin import admin_bp
    from backend.routes.doctor import doctor_bp
    from backend.routes.patient import patient_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(doctor_bp, url_prefix="/api/doctor")
    app.register_blueprint(patient_bp, url_prefix="/api/patient")

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/exports/<path:filename>")
    def exported_file(filename):
        export_dir = Path(app.root_path).parent / "exports"
        return send_from_directory(export_dir, filename, as_attachment=True)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
