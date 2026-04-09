from datetime import date

from flask_mail import Message

from backend.celery_worker import celery_app
from backend.extensions import mail
from backend.models import Appointment


@celery_app.task(name="jobs.daily_reminder.send_daily_reminders")
def send_daily_reminders():
    today = date.today()
    appointments = Appointment.query.filter_by(date=today, status="Booked").all()

    sent_count = 0
    for appt in appointments:
        patient_email = appt.patient.user.email
        if not patient_email:
            continue

        message = Message(
            subject="Reminder: Your appointment today",
            recipients=[patient_email],
            body=(
                f"Hi {appt.patient.name},\n\n"
                f"This is a reminder for your appointment today.\n"
                f"Doctor: {appt.doctor.name}\n"
                f"Department: {appt.department.name}\n"
                f"Time Slot: {appt.time_slot}\n\n"
                f"Please arrive 10 minutes early."
            ),
        )
        mail.send(message)
        sent_count += 1

    return {"sent_reminders": sent_count}
