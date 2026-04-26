@echo off
setlocal

set "KAIROS_HOME=%~dp0"
set "KAIROS_HOME=%KAIROS_HOME:~0,-1%"

echo Adding Kairos to your user PATH...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$kairosHome = [Environment]::GetEnvironmentVariable('KAIROS_HOME', 'User'); if ($kairosHome -ne '%KAIROS_HOME%') { [Environment]::SetEnvironmentVariable('KAIROS_HOME', '%KAIROS_HOME%', 'User') }; $path = [Environment]::GetEnvironmentVariable('Path', 'User'); $items = @(); if ($path) { $items = $path -split ';' | Where-Object { $_ } }; if ($items -notcontains '%KAIROS_HOME%') { $items += '%KAIROS_HOME%'; [Environment]::SetEnvironmentVariable('Path', ($items -join ';'), 'User') }"

set "PATH=%KAIROS_HOME%;%PATH%"

echo.
echo Running Kairos setup...
python "%KAIROS_HOME%\setup.py"

echo.
echo Kairos is ready.
echo Open a new terminal, then run:
echo   kairos gateway
echo   kairos cli

endlocal
