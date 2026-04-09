from pathlib import Path

from flask import Flask, jsonify, render_template, send_from_directory
from flask_cors import CORS

from backend.config import Config
from backend.extensions import cache, jwt, mail, migrate
from backend.models import db


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
