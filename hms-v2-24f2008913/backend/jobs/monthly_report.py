from datetime import date, timedelta

from flask_mail import Message

from backend.app import mail
from backend.celery_worker import celery_app
from backend.models import Appointment, Doctor


def _previous_month_range():
    today = date.today().replace(day=1)
    end = today - timedelta(days=1)
    start = end.replace(day=1)
    return start, end


@celery_app.task(name="jobs.monthly_report.send_monthly_reports")
def send_monthly_reports():
    start, end = _previous_month_range()
    active_doctors = Doctor.query.join(Doctor.user).filter_by(is_active=True).all()

    sent_count = 0
    for doctor in active_doctors:
        appointments = (
            Appointment.query.filter(
                Appointment.doctor_id == doctor.id,
                Appointment.status == "Completed",
                Appointment.date >= start,
                Appointment.date <= end,
            )
            .order_by(Appointment.date.asc())
            .all()
        )

        patient_names = sorted({a.patient.name for a in appointments})
        diagnoses = [a.treatment.diagnosis for a in appointments if a.treatment and a.treatment.diagnosis]
        prescriptions = [a.treatment.prescription for a in appointments if a.treatment and a.treatment.prescription]

        html = f"""
        <h3>Monthly Activity Report</h3>
        <p>Doctor: <b>{doctor.name}</b></p>
        <p>Period: {start.isoformat()} to {end.isoformat()}</p>
        <p>Total completed appointments: <b>{len(appointments)}</b></p>
        <p>Patients treated: {", ".join(patient_names) if patient_names else "None"}</p>
        <p>Diagnoses summary:</p>
        <ul>{''.join([f'<li>{d}</li>' for d in diagnoses]) or '<li>No diagnosis records</li>'}</ul>
        <p>Prescriptions given:</p>
        <ul>{''.join([f'<li>{p}</li>' for p in prescriptions]) or '<li>No prescriptions recorded</li>'}</ul>
        """

        email = doctor.user.email
        if not email:
            continue

        message = Message(
            subject="Monthly Doctor Activity Report",
            recipients=[email],
            html=html,
        )
        mail.send(message)
        sent_count += 1

    return {"reports_sent": sent_count, "period_start": start.isoformat(), "period_end": end.isoformat()}
