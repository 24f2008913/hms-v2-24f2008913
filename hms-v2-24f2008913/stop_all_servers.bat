@echo off
setlocal

echo ==========================================
echo HMS V2 - Stop Services
echo ==========================================

for %%P in (python.exe celery.exe) do (
    taskkill /F /IM %%P >nul 2>nul
)

if /I "%~1"=="--with-redis" (
    taskkill /F /IM redis-server.exe >nul 2>nul
)

docker ps --format "{{.Names}}" | findstr /i /x "hms-v2-redis" >nul
if %ERRORLEVEL%==0 docker stop hms-v2-redis >nul

echo [INFO] Stop commands sent.
if /I not "%~1"=="--with-redis" echo [INFO] Redis was not stopped. Use stop_all_servers.bat --with-redis to stop it.
echo If any terminal still runs, close it manually.
exit /b 0
