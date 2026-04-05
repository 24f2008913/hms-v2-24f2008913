import csv
from datetime import datetime
from pathlib import Path

from flask import current_app
from flask_mail import Message

from backend.app import mail
from backend.celery_worker import celery_app
from backend.models import Appointment, Patient


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
                "diagnosis",
                "prescription",
                "next_visit_date",
            ]
        )
        for appt in appointments:
            writer.writerow(
                [
                    patient.id,
                    patient.name,
                    appt.doctor.name,
                    appt.date.isoformat(),
                    appt.treatment.diagnosis if appt.treatment else "",
                    appt.treatment.prescription if appt.treatment else "",
                    appt.treatment.next_visit_date.isoformat() if appt.treatment and appt.treatment.next_visit_date else "",
                ]
            )

    if patient.user.email:
        mail.send(
            Message(
                subject="Your export is ready",
                recipients=[patient.user.email],
                body=(
                    f"Hi {patient.name},\n\n"
                    f"Your CSV export is ready.\n"
                    f"Download path: {filepath}\n"
                ),
            )
        )

    return {
        "download_link": f"/exports/{filename}",
        "filename": filename,
    }
