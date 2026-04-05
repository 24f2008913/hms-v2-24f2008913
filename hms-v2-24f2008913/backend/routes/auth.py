from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token

from backend.models import Patient, User, db


auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/register")
def register_patient():
    data = request.get_json() or {}
    required = ["username", "email", "password", "name", "contact"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    username = data["username"].strip()
    email = data["email"].strip().lower()

    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({"error": "Username or email already exists"}), 409

    user = User(username=username, email=email, role="patient", is_active=True)
    user.set_password(data["password"])

    dob = None
    if data.get("date_of_birth"):
        try:
            dob = datetime.strptime(data["date_of_birth"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "date_of_birth must be YYYY-MM-DD"}), 400

    patient = Patient(
        user=user,
        name=data["name"].strip(),
        contact=data["contact"].strip(),
        date_of_birth=dob,
        address=(data.get("address") or "").strip() or None,
    )

    db.session.add(user)
    db.session.add(patient)
    db.session.commit()

    return jsonify({"message": "Patient registered successfully"}), 201


@auth_bp.post("/login")
def login():
    data = request.get_json() or {}
    username_or_email = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username_or_email or not password:
        return jsonify({"error": "Username/email and password are required"}), 400

    user = User.query.filter(
        (User.username == username_or_email) | (User.email == username_or_email.lower())
    ).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid credentials"}), 401

    claims = {
        "role": user.role,
        "is_active": user.is_active,
    }
    token = create_access_token(identity=str(user.id), additional_claims=claims)

    return jsonify(
        {
            "access_token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
            },
        }
    )
