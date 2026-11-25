@echo off
REM Starts Requora AI on http://127.0.0.1:8001  (run from the project folder)
cd /d "%~dp0"
if not exist venv\Scripts\activate.bat (
  echo No venv found. Run these once first:
  echo   python -m venv venv
  echo   venv\Scripts\activate
  echo   pip install -r requirements.txt
  echo   python -m app.rag.ingest
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python -m uvicorn app.main:app --reload --port 8001
