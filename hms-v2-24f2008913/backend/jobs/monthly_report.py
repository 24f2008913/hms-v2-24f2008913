from datetime import date, timedelta
from html import escape
from pathlib import Path
from uuid import uuid4

from flask_mail import Message
from flask import current_app

from backend.celery_worker import celery_app
from backend.extensions import mail
from backend.models import Appointment, Doctor, User


def _previous_month_range():
    today = date.today().replace(day=1)
    end = today - timedelta(days=1)
    start = end.replace(day=1)
    return start, end


def _spool_message(message, error_text):
    spool_dir = Path(current_app.root_path).parent / "logs" / "mail_spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    file_path = spool_dir / f"{uuid4().hex}.eml"
    body = [
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
    file_path.write_text("\n".join(body), encoding="utf-8")
    return str(file_path)


def _render_email_shell(title, subtitle, stats, table_title, table_headers, table_rows, notes=None):
        stats_cells = []
        for label, value in stats:
                stats_cells.append(
                        f"""
                        <td style=\"width:50%;padding:0 6px 12px 0;vertical-align:top;\">
                            <div style=\"background:#f8fbff;border:1px solid #dbe5f2;border-radius:14px;padding:14px 16px;\">
                                <div style=\"font-size:12px;letter-spacing:.3px;text-transform:uppercase;color:#64748b;margin-bottom:6px;\">{escape(label)}</div>
                                <div style=\"font-size:26px;line-height:1.1;font-weight:800;color:#0f172a;\">{escape(value)}</div>
                            </div>
                        </td>
                        """
                )
        stats_rows = []
        for index in range(0, len(stats_cells), 2):
                left = stats_cells[index]
                right = stats_cells[index + 1] if index + 1 < len(stats_cells) else '<td style="width:50%;padding:0 6px 12px 0;"></td>'
                stats_rows.append(f"<tr>{left}{right}</tr>")
        stats_html = "".join(stats_rows)
        rows_html = "".join(table_rows) if table_rows else f"<tr><td colspan=\"{len(table_headers)}\" class=\"empty\">No records for this period</td></tr>"
        notes_html = ""
        if notes:
                notes_html = f"<div style=\"margin-top:16px;padding:14px 16px;background:#f8fbff;border:1px solid #dbe5f2;border-radius:14px;font-size:14px;line-height:1.6;color:#334155;\">{notes}</div>"

        return f"""
        <html>
            <body style=\"margin:0;padding:0;background:#f3f6fb;font-family:Arial,Helvetica,sans-serif;color:#1f2937;\">
                <div style=\"max-width:900px;margin:0 auto;padding:24px;\">
                    <div style=\"background:linear-gradient(135deg,#0f172a 0%,#2563eb 100%);color:#fff;border-radius:18px 18px 0 0;padding:28px 32px;\">
                        <div style=\"font-size:12px;letter-spacing:1px;text-transform:uppercase;opacity:.8;\">Hospital Management System</div>
                        <h2 style=\"margin:8px 0 4px;font-size:28px;line-height:1.2;\">{escape(title)}</h2>
                        <div style=\"font-size:15px;opacity:.9;\">{escape(subtitle)}</div>
                    </div>

                    <div style=\"background:#fff;border:1px solid #dbe5f2;border-top:none;border-radius:0 0 18px 18px;padding:28px 28px 30px;box-shadow:0 10px 30px rgba(15,23,42,.06);\">
                        <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin:0 0 20px;border-collapse:collapse;\">
                            {stats_html}
                        </table>

                        <div style=\"margin:0 0 12px;font-size:18px;font-weight:700;color:#0f172a;line-height:1.3;\">{escape(table_title)}</div>
                        <div style=\"overflow-x:auto;border:1px solid #e2e8f0;border-radius:14px;margin-bottom:16px;\">
                            <table style=\"width:100%;border-collapse:collapse;background:#fff;table-layout:fixed;\">
                                <thead>
                                    <tr style=\"background:#eff6ff;color:#0f172a;\">{''.join(f'<th style="text-align:left;padding:12px 14px;font-size:13px;border-bottom:1px solid #dbe5f2;white-space:nowrap;">{escape(h)}</th>' for h in table_headers)}</tr>
                                </thead>
                                <tbody>
                                    {rows_html}
                                </tbody>
                            </table>
                        </div>

                        {notes_html}
                    </div>
                </div>
            </body>
        </html>
        """


@celery_app.task(name="jobs.monthly_report.send_monthly_reports")
def send_monthly_reports():
    start, end = _previous_month_range()
    active_doctors = Doctor.query.join(Doctor.user).filter_by(is_active=True).all()

    sent_count = 0
    failed_count = 0
    failures = []

    # Keep the monthly task robust even when SMTP is temporarily unavailable.
    def _safe_send(subject, recipients, html):
        nonlocal sent_count, failed_count, failures
        message = Message(
            subject=subject,
            recipients=recipients,
            html=html,
        )
        try:
            mail.send(message)
            sent_count += 1
            return True
        except Exception as exc:
            failed_count += 1
            spool_path = _spool_message(message, str(exc))
            failures.append(
                {
                    "subject": subject,
                    "recipients": recipients,
                    "error": str(exc),
                    "spooled_to": spool_path,
                }
            )
            return False

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

        appointment_rows = []
        for appointment in appointments:
            treatment = appointment.treatment
            appointment_rows.append(
                f"""
                <tr>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape(appointment.date.isoformat())}</td>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape(appointment.patient.name)}</td>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape(appointment.time_slot)}</td>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape((treatment.diagnosis if treatment and treatment.diagnosis else '—'))}</td>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape((treatment.prescription if treatment and treatment.prescription else '—'))}</td>
                </tr>
                """
            )

        html = _render_email_shell(
            title=f"Monthly report for Dr. {doctor.name}",
            subtitle=f"Period: {start.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}",
            stats=[
                ("Completed appointments", str(len(appointments))),
                ("Patients treated", str(len(patient_names))),
                ("Diagnosis entries", str(len(diagnoses))),
                ("Prescription entries", str(len(prescriptions))),
            ],
            table_title="Completed appointments overview",
            table_headers=["Date", "Patient", "Time", "Diagnosis", "Prescription"],
            table_rows=appointment_rows,
            notes=f"<strong>Patients seen:</strong> {escape(', '.join(patient_names) if patient_names else 'None')}<br><strong>Top diagnoses:</strong> {escape(', '.join(diagnoses[:5]) if diagnoses else 'No diagnosis records')}<br><strong>Top prescriptions:</strong> {escape(', '.join(prescriptions[:5]) if prescriptions else 'No prescriptions recorded')}",
        )

        email = doctor.user.email
        if not email:
            continue

        _safe_send(
            subject="Monthly Doctor Activity Report",
            recipients=[email],
            html=html,
        )

    admin_emails = [
        u.email
        for u in User.query.filter_by(role="admin", is_active=True).all()
        if u.email
    ]
    if admin_emails:
        total_completed = (
            Appointment.query.filter(
                Appointment.status == "Completed",
                Appointment.date >= start,
                Appointment.date <= end,
            ).count()
        )
        per_doctor_rows = []
        for doctor in active_doctors:
            doctor_completed = Appointment.query.filter(
                Appointment.doctor_id == doctor.id,
                Appointment.status == "Completed",
                Appointment.date >= start,
                Appointment.date <= end,
            ).count()
            per_doctor_rows.append(
                f"""
                <tr>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape(doctor.name)}</td>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{escape(doctor.user.email or '—')}</td>
                  <td style=\"padding:12px 14px;border-bottom:1px solid #eef2f7;\">{doctor_completed}</td>
                </tr>
                """
            )

        summary_html = _render_email_shell(
            title="Monthly hospital summary",
            subtitle=f"Period: {start.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}",
            stats=[
                ("Completed appointments", str(total_completed)),
                ("Active doctors", str(len(active_doctors))),
                ("Admin recipients", str(len(admin_emails))),
                ("Reporting status", "Ready"),
            ],
            table_title="Doctor performance snapshot",
            table_headers=["Doctor", "Email", "Completed"],
            table_rows=per_doctor_rows,
            notes="This summary is generated from completed appointments only and sent to active admin users.",
        )
        _safe_send(
            subject="Monthly Admin Appointment Summary",
            recipients=admin_emails,
            html=summary_html,
        )

    return {
        "reports_sent": sent_count,
        "reports_failed": failed_count,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "failures": failures,
    }
