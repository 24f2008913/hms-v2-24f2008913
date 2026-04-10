from datetime import date, datetime, timedelta
import re

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from backend.extensions import cache
from backend.models import Appointment, Doctor, DoctorAvailability, Patient, Treatment, db
from backend.security import role_required


doctor_bp = Blueprint("doctor", __name__)

TIME_24H_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _minutes(v):
    h, m = v.split(":")
    return int(h) * 60 + int(m)


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


def _current_doctor():
    user_id = int(get_jwt_identity())
    return Doctor.query.filter_by(user_id=user_id).first()


@doctor_bp.get("/dashboard")
@role_required("doctor")
def doctor_dashboard():
    doctor = _current_doctor()
    if not doctor:
        return jsonify({"error": "Doctor profile not found"}), 404

    today = date.today()
    week_end = today + timedelta(days=7)

    upcoming = (
        Appointment.query.filter(
            Appointment.doctor_id == doctor.id,
            Appointment.date >= today,
            Appointment.date <= week_end,
        )
        .order_by(Appointment.date.asc())
        .all()
    )

    patient_ids = {appt.patient_id for appt in Appointment.query.filter_by(doctor_id=doctor.id).all()}
    assigned_patients = Patient.query.filter(Patient.id.in_(patient_ids)).all() if patient_ids else []

    return jsonify(
        {
            "upcoming_appointments": [
                {
                    "id": a.id,
                    "patient_id": a.patient_id,
                    "patient_name": a.patient.name,
                    "date": a.date.isoformat(),
                    "time_slot": a.time_slot,
                    "status": a.status,
                }
                for a in upcoming
            ],
            "assigned_patients": [
                {
                    "id": p.id,
                    "name": p.name,
                    "contact": p.contact,
                }
                for p in assigned_patients
            ],
        }
    )


@doctor_bp.get("/appointments")
@role_required("doctor")
def doctor_appointments():
    doctor = _current_doctor()
    if not doctor:
        return jsonify({"error": "Doctor profile not found"}), 404

    appts = Appointment.query.filter_by(doctor_id=doctor.id).order_by(Appointment.date.desc()).all()
    return jsonify(
        [
            {
                "id": a.id,
                "patient_id": a.patient_id,
                "patient_name": a.patient.name,
                "department": a.department.name,
                "date": a.date.isoformat(),
                "time_slot": a.time_slot,
                "status": a.status,
            }
            for a in appts
        ]
    )


@doctor_bp.put("/appointments/<int:appointment_id>/complete")
@role_required("doctor")
def mark_completed(appointment_id):
    doctor = _current_doctor()
    appt = Appointment.query.get_or_404(appointment_id)

    if appt.doctor_id != doctor.id:
        return jsonify({"error": "Forbidden"}), 403
    if appt.status != "Booked":
        return jsonify({"error": "Only Booked appointments can be completed"}), 409

    appt.status = "Completed"
    db.session.commit()
    cache.delete("admin_dashboard")
    return jsonify({"message": "Appointment marked as completed"})


@doctor_bp.put("/appointments/<int:appointment_id>/cancel")
@role_required("doctor")
def mark_cancelled(appointment_id):
    doctor = _current_doctor()
    appt = Appointment.query.get_or_404(appointment_id)

    if appt.doctor_id != doctor.id:
        return jsonify({"error": "Forbidden"}), 403
    if appt.status != "Booked":
        return jsonify({"error": "Only Booked appointments can be cancelled"}), 409

    appt.status = "Cancelled"
    db.session.commit()
    cache.delete("admin_dashboard")
    return jsonify({"message": "Appointment marked as cancelled"})


@doctor_bp.post("/appointments/<int:appointment_id>/treatment")
@role_required("doctor")
def add_treatment(appointment_id):
    doctor = _current_doctor()
    appt = Appointment.query.get_or_404(appointment_id)
    if appt.doctor_id != doctor.id:
        return jsonify({"error": "Forbidden"}), 403
    if appt.status == "Cancelled":
        return jsonify({"error": "Cannot save treatment for a cancelled appointment"}), 409

    data = request.get_json() or {}
    required = ["diagnosis", "prescription"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    next_visit = None
    if data.get("next_visit_date"):
        try:
            next_visit = datetime.strptime(data["next_visit_date"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "next_visit_date must be YYYY-MM-DD"}), 400

    treatment = Treatment.query.filter_by(appointment_id=appt.id).first()
    if not treatment:
        treatment = Treatment(appointment_id=appt.id, diagnosis="", prescription="")
        db.session.add(treatment)

    treatment.diagnosis = data["diagnosis"].strip()
    treatment.prescription = data["prescription"].strip()
    treatment.notes = (data.get("notes") or "").strip() or None
    treatment.next_visit_date = next_visit

    if appt.status == "Booked":
        appt.status = "Completed"

    db.session.commit()
    cache.delete("admin_dashboard")
    return jsonify(
        {
            "message": "Treatment saved",
            "treatment": {
                "appointment_id": appt.id,
                "diagnosis": treatment.diagnosis,
                "prescription": treatment.prescription,
                "notes": treatment.notes,
                "next_visit_date": treatment.next_visit_date.isoformat() if treatment.next_visit_date else None,
            },
        }
    ), 201


@doctor_bp.get("/patients/<int:patient_id>/history")
@role_required("doctor")
def patient_history(patient_id):
    doctor = _current_doctor()
    has_relationship = Appointment.query.filter_by(doctor_id=doctor.id, patient_id=patient_id).first()
    if not has_relationship:
        return jsonify({"error": "Forbidden"}), 403

    appts = (
        Appointment.query.filter_by(patient_id=patient_id)
        .order_by(Appointment.date.desc(), Appointment.created_at.desc())
        .all()
    )
    if not appts:
        return jsonify([])

    payload = []
    for a in appts:
        payload.append(
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
        )

    return jsonify(payload)


@doctor_bp.post("/availability")
@role_required("doctor")
def set_availability():
    doctor = _current_doctor()
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

        availability = DoctorAvailability.query.filter_by(doctor_id=doctor.id, date=avail_date).first()
        if not availability:
            availability = DoctorAvailability(doctor_id=doctor.id, date=avail_date)
            db.session.add(availability)

        availability.morning_start = morning_start
        availability.morning_end = morning_end
        availability.evening_start = evening_start
        availability.evening_end = evening_end

    db.session.commit()
    cache.delete(f"doctor_availability_{doctor.id}")
    cache.clear()

    return jsonify({"message": "Availability updated"})


@doctor_bp.get("/availability")
@role_required("doctor")
def get_availability():
    doctor = _current_doctor()
    cache_key = f"doctor_availability_{doctor.id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    today = date.today()
    end = today + timedelta(days=7)
    records = (
        DoctorAvailability.query.filter(
            DoctorAvailability.doctor_id == doctor.id,
            DoctorAvailability.date >= today,
            DoctorAvailability.date <= end,
        )
        .order_by(DoctorAvailability.date.asc())
        .all()
    )
    payload = [
        {
            "id": r.id,
            "date": r.date.isoformat(),
            "morning_start": r.morning_start,
            "morning_end": r.morning_end,
            "evening_start": r.evening_start,
            "evening_end": r.evening_end,
        }
        for r in records
    ]
    cache.set(cache_key, payload, timeout=120)
    return jsonify(payload)
