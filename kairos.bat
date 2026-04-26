@echo off
setlocal

set "KAIROS_HOME=%~dp0"
set "KAIROS_HOME=%KAIROS_HOME:~0,-1%"

if "%~1"=="" goto help
if /I "%~1"=="gateway" goto gateway
if /I "%~1"=="cli" goto cli
if /I "%~1"=="configure" goto configure
if /I "%~1"=="setup" goto configure
if /I "%~1"=="help" goto help
if /I "%~1"=="/?" goto help
if /I "%~1"=="-h" goto help
if /I "%~1"=="--help" goto help

echo Unknown command: %~1
echo.
goto help

:gateway
cd /d "%KAIROS_HOME%"
python -m agent.gateway
goto end

:cli
cd /d "%KAIROS_HOME%"
shift
python -m agent.cli %*
goto end

:configure
cd /d "%KAIROS_HOME%"
python setup.py
goto end

:help
echo Kairos commands:
echo.
echo   kairos gateway      Start the local Kairos gateway
echo   kairos cli          Open the Kairos terminal chat UI
echo   kairos configure    Re-run setup
echo.
echo First-time setup:
echo   kairos_start.bat
echo.

:end
endlocal
