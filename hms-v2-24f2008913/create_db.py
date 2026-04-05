import os

from dotenv import load_dotenv

from backend.app import create_app
from backend.models import Department, User, db


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


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        seed_departments()
        seed_admin()
        db.session.commit()
        print("Database created and admin seeded successfully.")


if __name__ == "__main__":
    main()
