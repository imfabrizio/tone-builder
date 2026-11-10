@echo off
rem Avvio su Windows: usa il venv in %USERPROFILE%\.venvs\tone-builder (fuori da OneDrive) o, se c'e', .venv nel progetto
cd /d "%~dp0"
if exist "%USERPROFILE%\.venvs\tone-builder\Scripts\python.exe" (
  "%USERPROFILE%\.venvs\tone-builder\Scripts\python.exe" -m tonebuilder
) else if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m tonebuilder
) else (
  py -3.12 -m tonebuilder
)
pause
