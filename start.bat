@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PORT=8080"
set "LOG_DIR=logs"
set "LOG_FILE=%LOG_DIR%\server.log"
set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

for %%A in (%*) do (
  set "ARG=%%~A"
  if /I "!ARG!"=="-h" goto :usage
  if /I "!ARG!"=="--help" goto :usage
  if /I "!ARG!"=="/h" goto :usage
  if /I "!ARG!"=="/?" goto :usage
)

for %%A in (%*) do (
  set "ARG=%%~A"
  if /I "!ARG:~0,7!"=="--port=" set "PORT=!ARG:~7!"
)

echo %PORT% | findstr /R "^[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [start] Invalid --port value, fallback to 8080
  set "PORT=8080"
)

where py >nul 2>&1
if %errorlevel%==0 (
  set "PY_CMD=py -3"
) else (
  set "PY_CMD=python"
)

if not exist "%VENV_PY%" (
  echo [start] Virtual environment not found, creating .venv...
  %PY_CMD% -m venv "%VENV_DIR%"
)

echo [start] Installing Python dependencies into .venv...
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install -r requirements.txt

echo [start] Checking old service on port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [start] Stopping old process PID %%P
  taskkill /PID %%P /F >nul 2>&1
)

if not exist "data\novels.db" (
  echo [start] Database not found, initializing...
  "%VENV_PY%" scripts\init_storage.py
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [start] Accessible URLs:
echo   - Local: http://127.0.0.1:%PORT%/index.html

set "LAN_PRINTED=0"

if "%LAN_PRINTED%"=="0" (
  for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /C:"IPv4"') do (
    set "IP=%%A"
    set "IP=!IP: =!"
    set "IP=!IP:(Preferred)=!"
    set "IP=!IP:(首选)=!"
    set "IP=!IP:(偏好)=!"
    if not "!IP!"=="" if /I not "!IP!"=="127.0.0.1" (
      echo   - LAN  : http://!IP!:%PORT%/index.html
      set "LAN_PRINTED=1"
    )
  )
)

if "%LAN_PRINTED%"=="0" (
  echo   - LAN  : ^(not detected automatically^)
)

echo [start] Starting server...
echo [start] Log file: %LOG_FILE%
set "NOVELSPEAKER_PORT=%PORT%"
"%VENV_PY%" app_server.py >> "%LOG_FILE%" 2>&1

endlocal
goto :eof

:usage
echo Usage:
echo   start.bat [--port=PORT] [-h^|--help^|/h^|/?]
echo.
echo Options:
echo   --port=PORT   Set HTTP port (default: 8080)
echo   -h, --help    Show this help message and exit
echo   /h, /?        Show this help message and exit
endlocal
