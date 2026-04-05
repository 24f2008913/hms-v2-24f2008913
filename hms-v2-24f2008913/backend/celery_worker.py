from celery import Celery
from celery.schedules import crontab

from backend.app import create_app


flask_app = create_app()


def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=app.config["CELERY_BROKER_URL"],
        backend=app.config["CELERY_RESULT_BACKEND"],
    )
    celery.conf.update(app.config)
    celery.conf.beat_schedule = {
        "daily-reminder-8am": {
            "task": "jobs.daily_reminder.send_daily_reminders",
            "schedule": crontab(hour=8, minute=0),
        },
        "monthly-report-7am": {
            "task": "jobs.monthly_report.send_monthly_reports",
            "schedule": crontab(day_of_month=1, hour=7, minute=0),
        },
    }

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


celery_app = make_celery(flask_app)

# Import tasks so Celery can discover them.
import backend.jobs.csv_export  # noqa: E402,F401
import backend.jobs.daily_reminder  # noqa: E402,F401
import backend.jobs.monthly_report  # noqa: E402,F401
