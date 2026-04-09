# HMS V2 - IITM BS MAD-2 (Jan 2026)

Roll Number: 24f2008913

## Tech Stack
- Backend: Flask REST API
- Frontend: Vue (CDN) + Bootstrap
- Database: SQLite via SQLAlchemy ORM
- Cache: Redis + Flask-Caching
- Async jobs: Celery + Redis

## Project Structure
- `backend/app.py` - Flask entry point and route registration
- `backend/config.py` - Environment-driven configuration
- `backend/models.py` - SQLAlchemy ORM models
- `backend/routes/*.py` - Role-based REST APIs
- `backend/jobs/*.py` - Celery tasks (daily reminder, monthly report, CSV export)
- `backend/celery_worker.py` - Celery app + beat schedules
- `frontend/index.html` - Single Jinja2 entry point with Vue CDN SPA
- `create_db.py` - Create tables and seed admin account

## Environment Variables
Use `.env` at project root. Keep placeholders for submission.

Required placeholders:
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `MAIL_DEFAULT_SENDER`
- `SECRET_KEY`
- `JWT_SECRET_KEY`
- `REDIS_URL`

Additional:
- `ADMIN_USERNAME`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

## Setup
1. Create and activate virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start Redis locally (`redis-server`).
4. Create DB and seed admin:
   ```bash
   python create_db.py
   ```

### Windows One-Time Setup
Run:
```bat
setup.bat
```

## Run Application
From project root:
```bash
python -m backend.app
```

Open `http://127.0.0.1:5000/`.

### Windows Run All Services
Start Flask + Celery worker + Celery beat + Redis (if available):
```bat
run_all_servers.bat
```

Stop processes:
```bat
stop_all_servers.bat
```

## Run Celery Worker
```bash
celery -A backend.celery_worker.celery_app worker --loglevel=info
```

## Run Celery Beat
```bash
celery -A backend.celery_worker.celery_app beat --loglevel=info
```

## Notes
- Admin registration route does not exist.
- Patients can self-register.
- JWT token is stored in memory on frontend runtime.
- Double booking prevention is enforced server-side with `409` response.
- Status transition rules are enforced on both route logic and dashboard actions.

## Appointment and Treatment Rules
- New appointments always start as `Booked`.
- Doctors can mark `Booked` appointments as `Completed` or `Cancelled`.
- Patients can cancel only `Booked` appointments.
- No route allows moving a `Completed` or `Cancelled` appointment back to `Booked`.
- Treatment save endpoint also marks a `Booked` appointment as `Completed`.
