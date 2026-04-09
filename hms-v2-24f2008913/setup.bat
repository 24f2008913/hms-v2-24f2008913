@echo off
setlocal ENABLEDELAYEDEXPANSION

echo ==========================================
echo HMS V2 - Windows Setup
echo ==========================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python is not installed or not in PATH.
    echo Install Python 3.10+ and re-run this file.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PY_CMD% -m venv .venv
    if not %ERRORLEVEL%==0 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
)

echo [INFO] Upgrading pip...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if not %ERRORLEVEL%==0 (
    echo [ERROR] pip upgrade failed.
    exit /b 1
)

echo [INFO] Installing dependencies from requirements.txt...
pip install -r requirements.txt
if not %ERRORLEVEL%==0 (
    echo [ERROR] Dependency installation failed.
    exit /b 1
)

echo [INFO] Initializing database and seeding admin user...
python create_db.py
if not %ERRORLEVEL%==0 (
    echo [ERROR] Database initialization failed.
    exit /b 1
)

echo.
echo [SUCCESS] Setup complete.
echo Next step: run run_all_servers.bat
echo.
exit /b 0
