@echo off
rem Installazione su Windows: crea il venv in %USERPROFILE%\.venvs\tone-builder e installa lapp.
rem Si rilancia senza problemi: aggiorna i pacchetti se ci sono gia.
cd /d "%~dp0"
set VENV=%USERPROFILE%\.venvs\tone-builder
where py >nul 2>nul || (echo Python non trovato: installalo con  winget install Python.Python.3.12  e rilancia. & pause & exit /b 1)
if not exist "%VENV%\Scripts\python.exe" (
  echo Creo lambiente in %VENV% ...
  py -3.12 -m venv "%VENV%" || py -3 -m venv "%VENV%" || (echo Creazione del venv non riuscita & pause & exit /b 1)
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -e ".[all]" "yt-dlp[default]" librosa || (echo Installazione non riuscita: leggi lerrore qui sopra & pause & exit /b 1)
echo.
echo Installazione completata. Dora in poi avvia lapp con avvia.bat
echo Facoltativo, separazione della chitarra:  "%VENV%\Scripts\python.exe" -m pip install demucs
pause
