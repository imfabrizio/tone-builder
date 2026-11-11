@echo off
rem Solo installazione/aggiornamento e collegamento sul desktop, senza avviare
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\avvia.ps1" -SoloInstalla
