from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from backend.extensions import cache
from backend.models import Appointment, Department, Doctor, DoctorAvailability, Patient, User, db
from backend.security import role_required


admin_bp = Blueprint("admin", __name__)

TIME_24H_RE = __import__("re").compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _minutes(value):
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _validate_slot_pair(start_value, end_value, label):
    if not start_value and not end_value:
        return None
    if not start_value or not end_value:
        return f"{label} start and end must both be set"
    if not TIME_24H_RE.match(start_value) or not TIME_24H_RE.match(end_value):
        return f"{label} time must be in HH:MM 24-hour format"
    if _minutes(start_value) >= _minutes(end_value):
        return f"{label} start time must be earlier than end time"
    return None


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
    cache.clear()

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


@admin_bp.get("/doctors/<int:doctor_id>/availability")
@role_required("admin")
def get_doctor_availability(doctor_id):
    Doctor.query.get_or_404(doctor_id)
    today = date.today()
    end = today + timedelta(days=6)
    records = (
        DoctorAvailability.query.filter(
            DoctorAvailability.doctor_id == doctor_id,
            DoctorAvailability.date >= today,
            DoctorAvailability.date <= end,
        )
        .order_by(DoctorAvailability.date.asc())
        .all()
    )
    return jsonify(
        [
            {
                "date": row.date.isoformat(),
                "morning_start": row.morning_start,
                "morning_end": row.morning_end,
                "evening_start": row.evening_start,
                "evening_end": row.evening_end,
            }
            for row in records
        ]
    )


@admin_bp.post("/doctors/<int:doctor_id>/availability")
@role_required("admin")
def set_doctor_availability(doctor_id):
    Doctor.query.get_or_404(doctor_id)
    data = request.get_json() or {}
    rows = data.get("availability") or []
    if not isinstance(rows, list) or not rows:
        return jsonify({"error": "availability list is required"}), 400

    today = date.today()
    last_day = today + timedelta(days=6)

    for row in rows:
        try:
            avail_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            return jsonify({"error": "Each availability entry needs a valid date in YYYY-MM-DD"}), 400

        if avail_date < today or avail_date > last_day:
            return jsonify({"error": "Availability can only be set for next 7 days"}), 400

        morning_start = row.get("morning_start")
        morning_end = row.get("morning_end")
        evening_start = row.get("evening_start")
        evening_end = row.get("evening_end")

        morning_error = _validate_slot_pair(morning_start, morning_end, "Morning")
        if morning_error:
            return jsonify({"error": morning_error, "date": avail_date.isoformat()}), 400
        evening_error = _validate_slot_pair(evening_start, evening_end, "Evening")
        if evening_error:
            return jsonify({"error": evening_error, "date": avail_date.isoformat()}), 400

        if not ((morning_start and morning_end) or (evening_start and evening_end)):
            return jsonify({"error": "At least one slot (morning/evening) is required", "date": avail_date.isoformat()}), 400

        availability = DoctorAvailability.query.filter_by(doctor_id=doctor_id, date=avail_date).first()
        if not availability:
            availability = DoctorAvailability(doctor_id=doctor_id, date=avail_date)
            db.session.add(availability)

        availability.morning_start = morning_start
        availability.morning_end = morning_end
        availability.evening_start = evening_start
        availability.evening_end = evening_end

    db.session.commit()
    cache.delete(f"doctor_availability_{doctor_id}")
    cache.delete("admin_dashboard")
    cache.clear()
    return jsonify({"message": "Availability updated"})


@admin_bp.delete("/doctors/<int:doctor_id>/delete")
@role_required("admin")
def delete_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)

    # Remove dependent records first so the doctor can be deleted safely.
    appointments = Appointment.query.filter_by(doctor_id=doctor.id).all()
    for appt in appointments:
        if appt.treatment:
            db.session.delete(appt.treatment)
        db.session.delete(appt)

    from backend.models import DoctorAvailability

    for availability in DoctorAvailability.query.filter_by(doctor_id=doctor.id).all():
        db.session.delete(availability)

    # Remove the doctor profile and associated login record in one transaction.
    db.session.delete(doctor)
    db.session.delete(doctor.user)
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Doctor deleted"})


@admin_bp.post("/patients")
@role_required("admin")
def add_patient():
    data = request.get_json() or {}
    required = ["name", "email", "temp_password", "contact"]
    missing = [field for field in required if data.get(field) in [None, ""]]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    email = data["email"].strip().lower()
    username = (data.get("username") or email.split("@")[0]).strip()
    if User.query.filter((User.email == email) | (User.username == username)).first():
        return jsonify({"error": "Patient email/username already exists"}), 409

    dob = None
    if data.get("date_of_birth"):
        try:
            dob = datetime.strptime(data["date_of_birth"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "date_of_birth must be YYYY-MM-DD"}), 400

    user = User(username=username, email=email, role="patient", is_active=True)
    user.set_password(data["temp_password"])

    patient = Patient(
        user=user,
        name=data["name"].strip(),
        contact=data["contact"].strip(),
        date_of_birth=dob,
        address=(data.get("address") or "").strip() or None,
    )

    db.session.add_all([user, patient])
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Patient created", "patient_id": patient.id}), 201


@admin_bp.put("/patients/<int:patient_id>")
@role_required("admin")
def update_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    data = request.get_json() or {}

    if data.get("name") is not None:
        patient.name = data["name"].strip()
    if data.get("contact") is not None:
        patient.contact = data["contact"].strip()
    if data.get("address") is not None:
        patient.address = data["address"].strip() or None
    if data.get("date_of_birth") is not None:
        if data["date_of_birth"] == "":
            patient.date_of_birth = None
        else:
            try:
                patient.date_of_birth = datetime.strptime(data["date_of_birth"], "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"error": "date_of_birth must be YYYY-MM-DD"}), 400

    db.session.commit()
    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Patient updated"})


@admin_bp.delete("/doctors/<int:doctor_id>")
@role_required("admin")
def blacklist_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    doctor.user.is_active = False
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Doctor blacklisted"})


@admin_bp.put("/doctors/<int:doctor_id>/unblacklist")
@role_required("admin")
def unblacklist_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    doctor.user.is_active = True
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Doctor unblacklisted"})


@admin_bp.delete("/patients/<int:patient_id>")
@role_required("admin")
def blacklist_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    patient.user.is_active = False
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Patient blacklisted"})


@admin_bp.put("/patients/<int:patient_id>/unblacklist")
@role_required("admin")
def unblacklist_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    patient.user.is_active = True
    db.session.commit()

    cache.delete("admin_dashboard")
    cache.clear()

    return jsonify({"message": "Patient unblacklisted"})


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


@admin_bp.get("/appointments/<int:appointment_id>")
@role_required("admin")
def appointment_detail(appointment_id):
    appt = Appointment.query.get_or_404(appointment_id)
    treatment = appt.treatment

    return jsonify(
        {
            "id": appt.id,
            "patient_name": appt.patient.name,
            "doctor_name": appt.doctor.name,
            "department": appt.department.name,
            "date": appt.date.isoformat(),
            "time_slot": appt.time_slot,
            "status": appt.status,
            "created_at": appt.created_at.isoformat(),
            "treatment": (
                {
                    "diagnosis": treatment.diagnosis,
                    "prescription": treatment.prescription,
                    "notes": treatment.notes,
                    "next_visit_date": treatment.next_visit_date.isoformat() if treatment.next_visit_date else None,
                }
                if treatment
                else None
            ),
        }
    )


@admin_bp.put("/appointments/<int:appointment_id>/status")
@role_required("admin")
def update_appointment_status(appointment_id):
    appt = Appointment.query.get_or_404(appointment_id)
    data = request.get_json() or {}
    new_status = (data.get("status") or "").strip().title()
    allowed = {"Booked", "Completed", "Cancelled"}
    if new_status not in allowed:
        return jsonify({"error": "status must be one of Booked, Completed, Cancelled"}), 400

    appt.status = new_status
    db.session.commit()
    cache.delete("admin_dashboard")
    cache.clear()
    return jsonify({"message": "Appointment status updated", "status": appt.status})


@admin_bp.get("/patients/<int:patient_id>/treatment-history")
@role_required("admin")
def patient_treatment_history(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    appointments = (
        Appointment.query.filter(Appointment.patient_id == patient.id)
        .order_by(Appointment.date.desc(), Appointment.created_at.desc())
        .all()
    )

    return jsonify(
        {
            "patient": {
                "id": patient.id,
                "name": patient.name,
                "email": patient.user.email,
                "contact": patient.contact,
            },
            "records": [
                {
                    "appointment_id": a.id,
                    "doctor_name": a.doctor.name,
                    "department": a.department.name,
                    "date": a.date.isoformat(),
                    "time_slot": a.time_slot,
                    "status": a.status,
                    "diagnosis": a.treatment.diagnosis if a.treatment else None,
                    "prescription": a.treatment.prescription if a.treatment else None,
                    "notes": a.treatment.notes if a.treatment else None,
                    "next_visit_date": a.treatment.next_visit_date.isoformat() if a.treatment and a.treatment.next_visit_date else None,
                }
                for a in appointments
            ],
        }
    )


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
