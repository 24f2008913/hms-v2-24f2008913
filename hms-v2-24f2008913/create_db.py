import os
from datetime import date

from dotenv import load_dotenv

from backend.app import create_app
from backend.models import Appointment, Department, Patient, Doctor, Treatment, User, db


load_dotenv()


def seed_departments():
    defaults = [
        ("Cardiology", "Heart and cardiovascular care"),
        ("Neurology", "Brain, spine and nervous system care"),
        ("Orthopedics", "Bones, joints and musculoskeletal care"),
        ("General Medicine", "Primary and internal medicine care"),
    ]

    for name, description in defaults:
        if not Department.query.filter_by(name=name).first():
            db.session.add(Department(name=name, description=description))


def seed_admin():
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@hms.local")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")

    existing_admin = User.query.filter_by(role="admin").first()
    if existing_admin:
        return

    admin_user = User(
        username=admin_username,
        email=admin_email,
        role="admin",
        is_active=True,
    )
    admin_user.set_password(admin_password)
    db.session.add(admin_user)


def seed_previous_month_mock_data():
    if Appointment.query.filter(Appointment.date.between(date(2026, 3, 1), date(2026, 3, 31))).first():
        return

    doctor_one = Doctor.query.filter_by(id=1).first()
    doctor_two = Doctor.query.filter_by(id=2).first()
    patient_one = Patient.query.filter_by(id=1).first()
    patient_two = Patient.query.filter_by(id=2).first()
    patient_three = Patient.query.filter_by(id=3).first()

    if not all([doctor_one, doctor_two, patient_one, patient_two, patient_three]):
        return

    mock_appointments = [
        {
            "doctor": doctor_one,
            "patient": patient_one,
            "department_id": doctor_one.department_id,
            "date": date(2026, 3, 5),
            "time_slot": "09:30",
            "diagnosis": "Seasonal flu",
            "prescription": "Rest, fluids, and paracetamol",
            "notes": "Advised home care and hydration.",
        },
        {
            "doctor": doctor_one,
            "patient": patient_two,
            "department_id": doctor_one.department_id,
            "date": date(2026, 3, 12),
            "time_slot": "11:00",
            "diagnosis": "Tension headache",
            "prescription": "Light analgesic and reduced screen time",
            "notes": "Follow-up if symptoms continue.",
        },
        {
            "doctor": doctor_two,
            "patient": patient_three,
            "department_id": doctor_two.department_id,
            "date": date(2026, 3, 18),
            "time_slot": "14:30",
            "diagnosis": "Routine review",
            "prescription": "Continue current medication",
            "notes": "Stable vitals, no new complaints.",
        },
    ]

    for payload in mock_appointments:
        existing = Appointment.query.filter_by(
            doctor_id=payload["doctor"].id,
            patient_id=payload["patient"].id,
            date=payload["date"],
            time_slot=payload["time_slot"],
        ).first()
        if existing:
            continue

        appointment = Appointment(
            doctor_id=payload["doctor"].id,
            patient_id=payload["patient"].id,
            department_id=payload["department_id"],
            date=payload["date"],
            time_slot=payload["time_slot"],
            status="Completed",
        )
        db.session.add(appointment)
        db.session.flush()
        db.session.add(
            Treatment(
                appointment_id=appointment.id,
                diagnosis=payload["diagnosis"],
                prescription=payload["prescription"],
                notes=payload["notes"],
            )
        )


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        seed_departments()
        seed_admin()
        seed_previous_month_mock_data()
        db.session.commit()
        print("Database created and admin seeded successfully.")


if __name__ == "__main__":
    main()
