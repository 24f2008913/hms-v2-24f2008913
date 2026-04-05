from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from backend.app import cache, role_required
from backend.models import Appointment, Department, Doctor, Patient, User, db


admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/dashboard")
@role_required("admin")
@cache.cached(timeout=60, key_prefix="admin_dashboard")
def dashboard():
    return jsonify(
        {
            "total_doctors": Doctor.query.count(),
            "total_patients": Patient.query.count(),
            "total_appointments": Appointment.query.count(),
        }
    )


@admin_bp.post("/doctors")
@role_required("admin")
def add_doctor():
    data = request.get_json() or {}
    required = ["name", "email", "temp_password", "specialization", "department_id", "experience_years"]
    missing = [field for field in required if data.get(field) in [None, ""]]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    email = data["email"].strip().lower()
    username = (data.get("username") or email.split("@")[0]).strip()
    if User.query.filter((User.email == email) | (User.username == username)).first():
        return jsonify({"error": "Doctor email/username already exists"}), 409

    department = Department.query.get(data["department_id"])
    if not department:
        return jsonify({"error": "Department not found"}), 404

    user = User(username=username, email=email, role="doctor", is_active=True)
    user.set_password(data["temp_password"])

    doctor = Doctor(
        user=user,
        name=data["name"].strip(),
        specialization=data["specialization"].strip(),
        department_id=department.id,
        experience_years=int(data["experience_years"]),
        bio=(data.get("bio") or "").strip() or None,
    )

    db.session.add_all([user, doctor])
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.delete_many("patient_doctors_all", "patient_doctors_q_none")

    return jsonify({"message": "Doctor created", "doctor_id": doctor.id}), 201


@admin_bp.put("/doctors/<int:doctor_id>")
@role_required("admin")
def update_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    data = request.get_json() or {}

    if data.get("name") is not None:
        doctor.name = data["name"].strip()
    if data.get("specialization") is not None:
        doctor.specialization = data["specialization"].strip()
    if data.get("experience_years") is not None:
        doctor.experience_years = int(data["experience_years"])
    if data.get("bio") is not None:
        doctor.bio = data["bio"].strip() or None
    if data.get("department_id") is not None:
        department = Department.query.get(data["department_id"])
        if not department:
            return jsonify({"error": "Department not found"}), 404
        doctor.department_id = department.id

    db.session.commit()
    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Doctor updated"})


@admin_bp.delete("/doctors/<int:doctor_id>")
@role_required("admin")
def blacklist_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    doctor.user.is_active = False
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Doctor blacklisted"})


@admin_bp.delete("/patients/<int:patient_id>")
@role_required("admin")
def blacklist_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    patient.user.is_active = False
    db.session.commit()

    cache.delete("admin_dashboard")

    return jsonify({"message": "Patient blacklisted"})


@admin_bp.get("/appointments")
@role_required("admin")
def all_appointments():
    appointments = Appointment.query.order_by(Appointment.date.desc()).all()
    payload = []
    for appt in appointments:
        payload.append(
            {
                "id": appt.id,
                "patient_name": appt.patient.name,
                "doctor_name": appt.doctor.name,
                "department": appt.department.name,
                "date": appt.date.isoformat(),
                "time_slot": appt.time_slot,
                "status": appt.status,
                "created_at": appt.created_at.isoformat(),
            }
        )
    return jsonify(payload)


@admin_bp.get("/search")
@role_required("admin")
def search_entities():
    entity_type = (request.args.get("type") or "").strip().lower()
    q = (request.args.get("q") or "").strip()

    if not q:
        return jsonify([])

    results = []

    if entity_type in ["doctor", "both", ""]:
        doctors = Doctor.query.filter(
            (Doctor.name.ilike(f"%{q}%")) | (Doctor.specialization.ilike(f"%{q}%"))
        ).all()
        for d in doctors:
            results.append(
                {
                    "type": "doctor",
                    "id": d.id,
                    "name": d.name,
                    "specialization": d.specialization,
                    "department": d.department.name,
                    "is_active": d.user.is_active,
                }
            )

    if entity_type in ["patient", "both", ""]:
        patients = Patient.query.filter(
            (Patient.name.ilike(f"%{q}%"))
            | (Patient.contact.ilike(f"%{q}%"))
            | (Patient.id == q if q.isdigit() else False)
        ).all()
        for p in patients:
            results.append(
                {
                    "type": "patient",
                    "id": p.id,
                    "name": p.name,
                    "contact": p.contact,
                    "is_active": p.user.is_active,
                }
            )

    return jsonify(results)


@admin_bp.get("/doctors")
@role_required("admin")
def list_doctors():
    doctors = Doctor.query.order_by(Doctor.id.desc()).all()
    return jsonify(
        [
            {
                "id": d.id,
                "name": d.name,
                "email": d.user.email,
                "specialization": d.specialization,
                "department": d.department.name,
                "department_id": d.department_id,
                "experience_years": d.experience_years,
                "bio": d.bio,
                "is_active": d.user.is_active,
            }
            for d in doctors
        ]
    )


@admin_bp.get("/patients")
@role_required("admin")
def list_patients():
    patients = Patient.query.order_by(Patient.id.desc()).all()
    return jsonify(
        [
            {
                "id": p.id,
                "name": p.name,
                "email": p.user.email,
                "contact": p.contact,
                "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
                "address": p.address,
                "is_active": p.user.is_active,
            }
            for p in patients
        ]
    )


@admin_bp.get("/departments")
@role_required("admin")
def list_departments():
    return jsonify(
        [
            {"id": d.id, "name": d.name, "description": d.description}
            for d in Department.query.order_by(Department.name.asc()).all()
        ]
    )
