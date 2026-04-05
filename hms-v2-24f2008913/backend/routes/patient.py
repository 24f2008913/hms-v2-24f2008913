from datetime import date, datetime, timedelta

from celery.result import AsyncResult
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from backend.app import cache, role_required
from backend.celery_worker import celery_app
from backend.jobs.csv_export import generate_patient_csv_export
from backend.models import (
    Appointment,
    Department,
    Doctor,
    DoctorAvailability,
    Patient,
    Treatment,
    User,
    db,
)


patient_bp = Blueprint("patient", __name__)


def _current_patient():
    user_id = int(get_jwt_identity())
    return Patient.query.filter_by(user_id=user_id).first()


@patient_bp.get("/dashboard")
@role_required("patient")
def dashboard():
    patient = _current_patient()
    if not patient:
        return jsonify({"error": "Patient profile not found"}), 404

    today = date.today()
    upcoming = (
        Appointment.query.filter(
            Appointment.patient_id == patient.id,
            Appointment.date >= today,
        )
        .order_by(Appointment.date.asc())
        .all()
    )

    doctors = Doctor.query.all()
    availability_map = {}
    start = today
    end = today + timedelta(days=7)
    for doctor in doctors:
        records = DoctorAvailability.query.filter(
            DoctorAvailability.doctor_id == doctor.id,
            DoctorAvailability.date >= start,
            DoctorAvailability.date <= end,
        ).all()
        availability_map[str(doctor.id)] = [
            {
                "date": r.date.isoformat(),
                "morning_start": r.morning_start,
                "morning_end": r.morning_end,
                "evening_start": r.evening_start,
                "evening_end": r.evening_end,
            }
            for r in records
        ]

    return jsonify(
        {
            "departments": [
                {"id": d.id, "name": d.name, "description": d.description}
                for d in Department.query.order_by(Department.name.asc()).all()
            ],
            "upcoming_appointments": [
                {
                    "id": a.id,
                    "doctor_id": a.doctor_id,
                    "doctor_name": a.doctor.name,
                    "department": a.department.name,
                    "date": a.date.isoformat(),
                    "time_slot": a.time_slot,
                    "status": a.status,
                }
                for a in upcoming
            ],
            "doctor_availability": availability_map,
        }
    )


@patient_bp.get("/doctors")
@role_required("patient")
def list_doctors():
    q = (request.args.get("q") or "").strip().lower()
    cache_key = f"patient_doctors_q_{q or 'none'}"
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    doctors_query = Doctor.query
    if q:
        doctors_query = doctors_query.filter(
            (Doctor.name.ilike(f"%{q}%")) | (Doctor.specialization.ilike(f"%{q}%"))
        )

    doctors = doctors_query.order_by(Doctor.name.asc()).all()
    payload = [
        {
            "id": d.id,
            "name": d.name,
            "specialization": d.specialization,
            "department": d.department.name,
            "experience_years": d.experience_years,
            "bio": d.bio,
            "is_active": d.user.is_active,
        }
        for d in doctors
        if d.user.is_active
    ]
    cache.set(cache_key, payload, timeout=300)
    return jsonify(payload)


@patient_bp.get("/doctors/<int:doctor_id>")
@role_required("patient")
def doctor_profile(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    today = date.today()
    end = today + timedelta(days=7)

    availability = DoctorAvailability.query.filter(
        DoctorAvailability.doctor_id == doctor.id,
        DoctorAvailability.date >= today,
        DoctorAvailability.date <= end,
    ).all()

    return jsonify(
        {
            "id": doctor.id,
            "name": doctor.name,
            "specialization": doctor.specialization,
            "department": doctor.department.name,
            "experience_years": doctor.experience_years,
            "bio": doctor.bio,
            "availability": [
                {
                    "date": a.date.isoformat(),
                    "morning_start": a.morning_start,
                    "morning_end": a.morning_end,
                    "evening_start": a.evening_start,
                    "evening_end": a.evening_end,
                }
                for a in availability
            ],
        }
    )


@patient_bp.post("/appointments")
@role_required("patient")
def book_appointment():
    patient = _current_patient()
    data = request.get_json() or {}

    required = ["doctor_id", "department_id", "date", "time_slot"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        appointment_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    doctor = Doctor.query.get(data["doctor_id"])
    department = Department.query.get(data["department_id"])
    if not doctor or not doctor.user.is_active:
        return jsonify({"error": "Doctor not found or inactive"}), 404
    if not department:
        return jsonify({"error": "Department not found"}), 404

    conflict = Appointment.query.filter_by(
        doctor_id=doctor.id,
        date=appointment_date,
        time_slot=data["time_slot"],
        status="Booked",
    ).first()
    if conflict:
        return jsonify({"error": "Selected doctor time slot already booked"}), 409

    appointment = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        department_id=department.id,
        date=appointment_date,
        time_slot=data["time_slot"],
        status="Booked",
    )
    db.session.add(appointment)
    db.session.commit()

    cache.delete("admin_dashboard")

    return jsonify({"message": "Appointment booked", "appointment_id": appointment.id}), 201


@patient_bp.put("/appointments/<int:appointment_id>")
@role_required("patient")
def reschedule_appointment(appointment_id):
    patient = _current_patient()
    appointment = Appointment.query.get_or_404(appointment_id)
    if appointment.patient_id != patient.id:
        return jsonify({"error": "Forbidden"}), 403
    if appointment.status != "Booked":
        return jsonify({"error": "Only Booked appointments can be rescheduled"}), 409

    data = request.get_json() or {}
    new_date_str = data.get("date")
    new_time_slot = data.get("time_slot")
    if not new_date_str or not new_time_slot:
        return jsonify({"error": "date and time_slot are required"}), 400

    try:
        new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    conflict = Appointment.query.filter(
        Appointment.id != appointment.id,
        Appointment.doctor_id == appointment.doctor_id,
        Appointment.date == new_date,
        Appointment.time_slot == new_time_slot,
        Appointment.status == "Booked",
    ).first()
    if conflict:
        return jsonify({"error": "Selected doctor time slot already booked"}), 409

    appointment.date = new_date
    appointment.time_slot = new_time_slot
    db.session.commit()

    return jsonify({"message": "Appointment rescheduled"})


@patient_bp.delete("/appointments/<int:appointment_id>")
@role_required("patient")
def cancel_appointment(appointment_id):
    patient = _current_patient()
    appointment = Appointment.query.get_or_404(appointment_id)
    if appointment.patient_id != patient.id:
        return jsonify({"error": "Forbidden"}), 403
    if appointment.status != "Booked":
        return jsonify({"error": "Only Booked appointments can be cancelled"}), 409

    appointment.status = "Cancelled"
    db.session.commit()
    return jsonify({"message": "Appointment cancelled"})


@patient_bp.get("/appointments")
@role_required("patient")
def list_upcoming_appointments():
    patient = _current_patient()
    today = date.today()
    appointments = (
        Appointment.query.filter(
            Appointment.patient_id == patient.id,
            Appointment.date >= today,
        )
        .order_by(Appointment.date.asc())
        .all()
    )
    return jsonify(
        [
            {
                "id": a.id,
                "doctor_name": a.doctor.name,
                "department": a.department.name,
                "date": a.date.isoformat(),
                "time_slot": a.time_slot,
                "status": a.status,
            }
            for a in appointments
        ]
    )


@patient_bp.get("/history")
@role_required("patient")
def appointment_history():
    patient = _current_patient()
    today = date.today()
    appointments = (
        Appointment.query.filter(
            Appointment.patient_id == patient.id,
            Appointment.date < today,
        )
        .order_by(Appointment.date.desc())
        .all()
    )

    return jsonify(
        [
            {
                "id": a.id,
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
        ]
    )


@patient_bp.put("/profile")
@role_required("patient")
def update_profile():
    patient = _current_patient()
    data = request.get_json() or {}

    if data.get("name") is not None:
        patient.name = data["name"].strip()
    if data.get("contact") is not None:
        patient.contact = data["contact"].strip()
    if data.get("address") is not None:
        patient.address = data["address"].strip() or None
    if data.get("date_of_birth") is not None:
        try:
            patient.date_of_birth = datetime.strptime(data["date_of_birth"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "date_of_birth must be YYYY-MM-DD"}), 400

    db.session.commit()
    return jsonify({"message": "Profile updated"})


@patient_bp.get("/export-csv")
@role_required("patient")
def trigger_export():
    patient = _current_patient()
    task = generate_patient_csv_export.delay(patient.id)
    return jsonify({"task_id": task.id, "message": "CSV export started"}), 202


@patient_bp.get("/export-csv/status/<task_id>")
@role_required("patient")
def export_status(task_id):
    task = AsyncResult(task_id, app=celery_app)
    response = {"task_id": task.id, "state": task.state}

    if task.state == "SUCCESS":
        result = task.result or {}
        response["result"] = result
        response["download_link"] = result.get("download_link")
    elif task.state == "FAILURE":
        response["error"] = str(task.info)

    return jsonify(response)
