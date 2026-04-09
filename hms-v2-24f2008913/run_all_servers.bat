@echo off
setlocal ENABLEDELAYEDEXPANSION

echo ==========================================
echo HMS V2 - Start All Services
echo ==========================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

if not exist ".venv\Scripts\activate.bat" (
    echo [WARN] Virtual environment not found. Running setup now...
    call "%ROOT%setup.bat"
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] setup.bat failed. Cannot continue.
        exit /b 1
    )
)

set "REDIS_STARTED=0"
set "REDIS_CLI_FOUND=0"

if not exist "logs" mkdir logs
set "RUN_ID=%RANDOM%%RANDOM%"

echo [INFO] Ensuring database tables and admin seed...
".venv\Scripts\python.exe" create_db.py > logs\seed.log 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Database setup failed. Check logs\seed.log
    exit /b 1
)

where redis-cli >nul 2>nul
if %ERRORLEVEL% EQU 0 set "REDIS_CLI_FOUND=1"

netstat -ano | findstr /R /C:":6379 .*LISTENING" >nul
if %ERRORLEVEL% EQU 0 (
    if "%REDIS_CLI_FOUND%"=="1" (
        redis-cli ping | findstr /I "PONG" >nul
        if !ERRORLEVEL! EQU 0 (
            echo [INFO] Redis already running on port 6379.
            set "REDIS_STARTED=1"
        ) else (
            echo [WARN] Port 6379 is in use but Redis did not respond to PING.
        )
    ) else (
        echo [INFO] Port 6379 is in use. redis-cli not found, assuming Redis is available.
        set "REDIS_STARTED=1"
    )
)

if "%REDIS_STARTED%"=="0" where redis-server >nul 2>nul
if "%REDIS_STARTED%"=="0" if %ERRORLEVEL% EQU 0 (
    echo [INFO] Starting Redis server in background...
    start "HMS-V2 Redis" /B redis-server > logs\redis.log 2>&1
    if "%REDIS_CLI_FOUND%"=="1" (
        redis-cli ping | findstr /I "PONG" >nul
        if !ERRORLEVEL! EQU 0 (
            echo [INFO] Redis startup verified.
            set "REDIS_STARTED=1"
        ) else (
            echo [WARN] Redis process started but PING check failed. See logs\redis.log
        )
    ) else (
        set "REDIS_STARTED=1"
    )
) else (
    if "%REDIS_STARTED%"=="0" where docker >nul 2>nul
    if "%REDIS_STARTED%"=="0" if %ERRORLEVEL% EQU 0 (
        echo [INFO] redis-server not found. Trying Docker Redis...
        docker ps -a --format "{{.Names}}" | findstr /i /x "hms-v2-redis" >nul
        if !ERRORLEVEL! EQU 0 (
            docker start hms-v2-redis >nul
        ) else (
            docker run -d --name hms-v2-redis -p 6379:6379 redis:7-alpine >nul
        )
        if !ERRORLEVEL! EQU 0 set "REDIS_STARTED=1"
    )
)

if "%REDIS_STARTED%"=="0" (
    echo [WARN] Redis could not be auto-started.
    echo Please start Redis manually, then continue.
    exit /b 1
)

netstat -ano | findstr /R /C:":5000 .*LISTENING" >nul
if %ERRORLEVEL% EQU 0 (
    echo [INFO] Flask port 5000 already in use. Skipping Flask start.
) else (
    echo [INFO] Starting Flask API server in background...
    start "HMS-V2 Flask" /B ".venv\Scripts\python.exe" -m backend.app > logs\flask.log 2>&1
)

echo [INFO] Starting Celery worker in background (pool=solo)...
start "HMS-V2 Celery Worker" /B ".venv\Scripts\celery.exe" -A backend.celery_worker.celery_app worker --pool=solo --loglevel=info > logs\celery_worker_%RUN_ID%.log 2>&1

echo [INFO] Starting Celery beat in background...
start "HMS-V2 Celery Beat" /B ".venv\Scripts\celery.exe" -A backend.celery_worker.celery_app beat --loglevel=info > logs\celery_beat_%RUN_ID%.log 2>&1

echo.
echo [SUCCESS] Startup commands executed.
echo Logs: logs\flask.log, logs\celery_worker_%RUN_ID%.log, logs\celery_beat_%RUN_ID%.log, logs\redis.log
echo Open http://127.0.0.1:5000 in your browser.
echo.
exit /b 0
