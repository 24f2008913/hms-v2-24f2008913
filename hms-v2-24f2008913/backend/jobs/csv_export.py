import csv
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import current_app
from flask_mail import Message

from backend.celery_worker import celery_app
from backend.extensions import mail
from backend.models import Appointment, Patient


def _spool_message(message, error_text):
    spool_dir = Path(current_app.root_path).parent / "logs" / "mail_spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    file_path = spool_dir / f"{uuid4().hex}.eml"
    file_path.write_text(
        "\n".join(
            [
                f"Subject: {message.subject}",
                f"To: {', '.join(message.recipients)}",
                f"From: {message.sender or current_app.config.get('MAIL_DEFAULT_SENDER') or ''}",
                "",
                "[SMTP delivery failed; message spooled locally]",
                f"Error: {error_text}",
                "",
                message.body or "",
                "",
                message.html or "",
            ]
        ),
        encoding="utf-8",
    )
    return str(file_path)


@celery_app.task(name="jobs.csv_export.generate_patient_csv_export")
def generate_patient_csv_export(patient_id):
    patient = Patient.query.get(patient_id)
    if not patient:
        return {"error": "Patient not found"}

    appointments = (
        Appointment.query.filter_by(patient_id=patient.id)
        .order_by(Appointment.date.desc())
        .all()
    )

    treated_appointments = [
        appt for appt in appointments
        if appt.status == "Completed" and appt.treatment is not None
    ]

    export_dir = Path(current_app.root_path).parent / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"patient_{patient.id}_appointments_{timestamp}.csv"
    filepath = export_dir / filename

    with filepath.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "patient_id",
                "patient_name",
                "doctor_name",
                "appointment_date",
                "appointment_status",
                "diagnosis",
                "prescription",
                "doctor_notes",
                "next_visit_date",
            ]
        )
        for appt in treated_appointments:
            treatment = appt.treatment
            writer.writerow(
                [
                    patient.id,
                    patient.name,
                    appt.doctor.name,
                    appt.date.isoformat(),
                    appt.status,
                    treatment.diagnosis,
                    treatment.prescription,
                    treatment.notes or "",
                    treatment.next_visit_date.isoformat() if treatment.next_visit_date else "",
                ]
            )

    if patient.user.email:
        message = Message(
            subject="Your export is ready",
            recipients=[patient.user.email],
            body=(
                f"Hi {patient.name},\n\n"
                f"Your CSV export is ready.\n"
                f"Download path: {filepath}\n"
            ),
        )
        try:
            mail.send(message)
        except Exception as exc:
            _spool_message(message, str(exc))

    return {
        "download_link": f"/exports/{filename}",
        "filename": filename,
    }
