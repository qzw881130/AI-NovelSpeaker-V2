@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "PORT=8080"

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
  echo [stop] Invalid --port value, fallback to 8080
  set "PORT=8080"
)

echo [stop] Checking service on port %PORT%...
set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  if "!FOUND!"=="0" set "FOUND=1"
  echo [stop] Stopping process PID %%P
  taskkill /PID %%P /F >nul 2>&1
)

if "%FOUND%"=="0" (
  echo [stop] No running service found on port %PORT%.
  endlocal
  goto :eof
)

echo [stop] Service on port %PORT% stopped.
endlocal
goto :eof

:usage
echo Usage:
echo   stop.bat [--port=PORT] [-h^|--help^|/h^|/?]
echo.
echo Options:
echo   --port=PORT   Set HTTP port to stop ^(default: 8080^)
echo   -h, --help    Show this help message and exit
echo   /h, /?        Show this help message and exit
endlocal
