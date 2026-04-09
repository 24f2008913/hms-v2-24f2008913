from datetime import date, datetime, timedelta
from pathlib import Path
import re
from uuid import uuid4

from celery.result import AsyncResult
from flask import Blueprint, current_app, jsonify, request, send_from_directory
from flask_jwt_extended import get_jwt_identity
from werkzeug.utils import secure_filename

from backend.extensions import cache
from backend.models import (
    Appointment,
    Department,
    Doctor,
    DoctorAvailability,
    MedicalDocument,
    Patient,
    Treatment,
    User,
    db,
)
from backend.security import role_required


patient_bp = Blueprint("patient", __name__)

TIME_24H_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
APPOINTMENT_DURATION_MINUTES = 30
SLOT_STEP_MINUTES = 30
ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "txt"}


def _minutes(v):
    h, m = v.split(":")
    return int(h) * 60 + int(m)


def _to_hhmm(total_minutes):
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _parse_slot_range(slot_value):
    value = (slot_value or "").strip()
    if not value:
        return None

    if "-" in value:
        start_value, end_value = [part.strip() for part in value.split("-", 1)]
        if not TIME_24H_RE.match(start_value) or not TIME_24H_RE.match(end_value):
            return None
        start_min = _minutes(start_value)
        end_min = _minutes(end_value)
        if start_min >= end_min:
            return None
        return (start_min, end_min)

    if not TIME_24H_RE.match(value):
        return None
    start_min = _minutes(value)
    return (start_min, start_min + APPOINTMENT_DURATION_MINUTES)


def _ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def _generate_slots(start_value, end_value):
    if not start_value or not end_value:
        return []

    start_min = _minutes(start_value)
    end_min = _minutes(end_value)
    slots = []
    current = start_min

    while current + APPOINTMENT_DURATION_MINUTES <= end_min:
        slots.append(_to_hhmm(current))
        current += SLOT_STEP_MINUTES

    return slots


def _booked_ranges(doctor_id, appointment_date, exclude_appointment_id=None):
    query = Appointment.query.filter(
        Appointment.doctor_id == doctor_id,
        Appointment.date == appointment_date,
        Appointment.status == "Booked",
    )
    if exclude_appointment_id is not None:
        query = query.filter(Appointment.id != exclude_appointment_id)

    ranges = []
    for appt in query.all():
        parsed = _parse_slot_range(appt.time_slot)
        if parsed:
            ranges.append(parsed)
    return ranges


def _doctor_available_slots(doctor_id, appointment_date, exclude_appointment_id=None):
    availability = DoctorAvailability.query.filter_by(doctor_id=doctor_id, date=appointment_date).first()
    if not availability:
        return []

    candidate_slots = []
    candidate_slots.extend(_generate_slots(availability.morning_start, availability.morning_end))
    candidate_slots.extend(_generate_slots(availability.evening_start, availability.evening_end))

    booked = _booked_ranges(doctor_id, appointment_date, exclude_appointment_id=exclude_appointment_id)
    free_slots = []

    for slot in candidate_slots:
        slot_start = _minutes(slot)
        slot_end = slot_start + APPOINTMENT_DURATION_MINUTES
        conflicts = any(_ranges_overlap(slot_start, slot_end, b_start, b_end) for b_start, b_end in booked)
        if not conflicts:
            free_slots.append(slot)

    return free_slots


def _patient_has_overlap(patient_id, appointment_date, slot_value, exclude_appointment_id=None):
    requested = _parse_slot_range(slot_value)
    if not requested:
        return False

    query = Appointment.query.filter(
        Appointment.patient_id == patient_id,
        Appointment.date == appointment_date,
        Appointment.status == "Booked",
    )
    if exclude_appointment_id is not None:
        query = query.filter(Appointment.id != exclude_appointment_id)

    req_start, req_end = requested
    for appt in query.all():
        existing = _parse_slot_range(appt.time_slot)
        if not existing:
            continue
        ex_start, ex_end = existing
        if _ranges_overlap(req_start, req_end, ex_start, ex_end):
            return True
    return False


def _is_within_range(slot_value, start_value, end_value):
    if not start_value or not end_value:
        return False
    return _minutes(start_value) <= _minutes(slot_value) < _minutes(end_value)


def _slot_available_for_doctor(doctor_id, appointment_date, slot_value):
    return slot_value in _doctor_available_slots(doctor_id, appointment_date)


def _current_patient():
    user_id = int(get_jwt_identity())
    return Patient.query.filter_by(user_id=user_id).first()


def _documents_base_dir():
    return Path(current_app.root_path).parent / "uploads" / "medical_documents"


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
    active_count = db.session.query(Doctor).join(User).filter(User.is_active.is_(True)).count()
    if cached is not None and (len(cached) > 0 or active_count == 0):
        return jsonify(cached)

    doctors_query = Doctor.query
    if q:
        doctors_query = doctors_query.filter(
            (Doctor.name.ilike(f"%{q}%")) | (Doctor.specialization.ilike(f"%{q}%"))
        )

    doctors = doctors_query.order_by(Doctor.name.asc()).all()
    today = date.today()
    end = today + timedelta(days=6)
    doctor_ids = [d.id for d in doctors if d.user.is_active]
    availability_map = {doctor_id: [] for doctor_id in doctor_ids}

    if doctor_ids:
        availability_rows = (
            DoctorAvailability.query.filter(
                DoctorAvailability.doctor_id.in_(doctor_ids),
                DoctorAvailability.date >= today,
                DoctorAvailability.date <= end,
            )
            .order_by(DoctorAvailability.date.asc())
            .all()
        )
        for row in availability_rows:
            availability_map[row.doctor_id].append(
                {
                    "date": row.date.isoformat(),
                    "morning_start": row.morning_start,
                    "morning_end": row.morning_end,
                    "evening_start": row.evening_start,
                    "evening_end": row.evening_end,
                }
            )

    payload = [
        {
            "id": d.id,
            "name": d.name,
            "specialization": d.specialization,
            "department": d.department.name,
            "experience_years": d.experience_years,
            "bio": d.bio,
            "is_active": d.user.is_active,
            "availability": availability_map.get(d.id, []),
        }
        for d in doctors
        if d.user.is_active
    ]
    cache.set(cache_key, payload, timeout=300)
    return jsonify(payload)


@patient_bp.get("/available-doctors")
@role_required("patient")
def available_doctors_by_date():
    date_str = (request.args.get("date") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    if not date_str:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400

    try:
        appointment_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    doctors_query = Doctor.query.join(User).filter(User.is_active.is_(True))
    if q:
        doctors_query = doctors_query.filter(
            (Doctor.name.ilike(f"%{q}%")) | (Doctor.specialization.ilike(f"%{q}%"))
        )

    doctors = doctors_query.order_by(Doctor.name.asc()).all()
    payload = []
    for doctor in doctors:
        slots = _doctor_available_slots(doctor.id, appointment_date)
        if not slots:
            continue
        payload.append(
            {
                "id": doctor.id,
                "name": doctor.name,
                "specialization": doctor.specialization,
                "department": doctor.department.name,
                "department_id": doctor.department_id,
                "experience_years": doctor.experience_years,
                "bio": doctor.bio,
                "available_slots": slots,
            }
        )

    return jsonify(payload)


@patient_bp.get("/doctors/<int:doctor_id>/available-slots")
@role_required("patient")
def doctor_available_slots(doctor_id):
    date_str = (request.args.get("date") or "").strip()
    if not date_str:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400

    try:
        appointment_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    doctor = Doctor.query.get_or_404(doctor_id)
    if not doctor.user.is_active:
        return jsonify({"error": "Doctor is inactive"}), 409

    slots = _doctor_available_slots(doctor.id, appointment_date)
    return jsonify({
        "doctor_id": doctor.id,
        "date": appointment_date.isoformat(),
        "available_slots": slots,
    })


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

    slot_value = (data.get("time_slot") or "").strip()
    if not TIME_24H_RE.match(slot_value):
        return jsonify({"error": "time_slot must be HH:MM in 24-hour format"}), 400

    doctor = Doctor.query.get(data["doctor_id"])
    department = Department.query.get(data["department_id"])
    if not doctor or not doctor.user.is_active:
        return jsonify({"error": "Doctor not found or inactive"}), 404
    if not department:
        return jsonify({"error": "Department not found"}), 404
    available_slots = _doctor_available_slots(doctor.id, appointment_date)
    if slot_value not in available_slots:
        return jsonify({"error": "Selected time is unavailable for this doctor/date"}), 409
    if _patient_has_overlap(patient.id, appointment_date, slot_value):
        return jsonify({"error": "You already have an overlapping booked appointment"}), 409

    appointment = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        department_id=department.id,
        date=appointment_date,
        time_slot=slot_value,
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
    if not TIME_24H_RE.match(new_time_slot):
        return jsonify({"error": "time_slot must be HH:MM in 24-hour format"}), 400
    available_slots = _doctor_available_slots(appointment.doctor_id, new_date, exclude_appointment_id=appointment.id)
    if new_time_slot not in available_slots:
        return jsonify({"error": "Selected time is unavailable for this doctor/date"}), 409
    if _patient_has_overlap(patient.id, new_date, new_time_slot, exclude_appointment_id=appointment.id):
        return jsonify({"error": "You already have an overlapping booked appointment"}), 409

    appointment.date = new_date
    appointment.time_slot = new_time_slot
    db.session.commit()
    cache.delete("admin_dashboard")

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
    cache.delete("admin_dashboard")
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
                "doctor_id": a.doctor_id,
                "department_id": a.department_id,
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
    appointments = (
        Appointment.query.filter(
            Appointment.patient_id == patient.id,
            Appointment.status != "Booked",
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


@patient_bp.post("/medical-documents")
@role_required("patient")
def upload_medical_document():
    patient = _current_patient()
    if not patient:
        return jsonify({"error": "Patient profile not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "No file selected"}), 400

    original_filename = secure_filename(file.filename)
    if "." not in original_filename:
        return jsonify({"error": "File extension is required"}), 400

    extension = original_filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type: .{extension}"}), 400

    stored_filename = f"{patient.id}_{uuid4().hex}.{extension}"
    patient_dir = _documents_base_dir() / str(patient.id)
    patient_dir.mkdir(parents=True, exist_ok=True)
    file_path = patient_dir / stored_filename
    file.save(file_path)

    document = MedicalDocument(
        patient_id=patient.id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=file.mimetype,
    )
    db.session.add(document)
    db.session.commit()

    return jsonify(
        {
            "message": "Medical document uploaded",
            "document": {
                "id": document.id,
                "filename": document.original_filename,
                "content_type": document.content_type,
                "uploaded_at": document.uploaded_at.isoformat(),
            },
        }
    ), 201


@patient_bp.get("/medical-documents")
@role_required("patient")
def list_medical_documents():
    patient = _current_patient()
    if not patient:
        return jsonify({"error": "Patient profile not found"}), 404

    docs = MedicalDocument.query.filter_by(patient_id=patient.id).order_by(MedicalDocument.uploaded_at.desc()).all()
    return jsonify(
        [
            {
                "id": doc.id,
                "filename": doc.original_filename,
                "content_type": doc.content_type,
                "uploaded_at": doc.uploaded_at.isoformat(),
                "download_url": f"/api/patient/medical-documents/{doc.id}/download",
            }
            for doc in docs
        ]
    )


@patient_bp.get("/medical-documents/<int:document_id>/download")
@role_required("patient")
def download_medical_document(document_id):
    patient = _current_patient()
    if not patient:
        return jsonify({"error": "Patient profile not found"}), 404

    doc = MedicalDocument.query.get_or_404(document_id)
    if doc.patient_id != patient.id:
        return jsonify({"error": "Forbidden"}), 403

    patient_dir = _documents_base_dir() / str(patient.id)
    return send_from_directory(patient_dir, doc.stored_filename, as_attachment=True, download_name=doc.original_filename)


@patient_bp.get("/export-csv")
@role_required("patient")
def trigger_export():
    from backend.jobs.csv_export import generate_patient_csv_export

    patient = _current_patient()
    task = generate_patient_csv_export.delay(patient.id)
    return jsonify({"task_id": task.id, "message": "CSV export started"}), 202


@patient_bp.get("/export-csv/status/<task_id>")
@role_required("patient")
def export_status(task_id):
    from backend.celery_worker import celery_app

    task = AsyncResult(task_id, app=celery_app)
    response = {"task_id": task.id, "state": task.state}

    if task.state == "SUCCESS":
        result = task.result or {}
        response["result"] = result
        response["download_link"] = result.get("download_link")
    elif task.state == "FAILURE":
        response["error"] = str(task.info)

    return jsonify(response)
