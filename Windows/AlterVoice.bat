@echo off
:: Si sposta nella cartella padre (MiaCartella)
cd /d "%~dp0.."

:: Attiva l'ambiente virtuale
call venv\Scripts\activate.bat

:: Esegue lo script Python
python voice_anonymizer.py

:: Mantiene la finestra del prompt aperta per vedere eventuali errori o output
pause