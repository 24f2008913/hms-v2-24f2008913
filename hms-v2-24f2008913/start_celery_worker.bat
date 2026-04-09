@echo off
setlocal
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
celery -A backend.celery_worker.celery_app worker --pool=solo --loglevel=info
