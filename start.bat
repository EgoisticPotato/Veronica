@echo off
echo Starting Veronica...

start "Veronica Backend" cmd /k "cd backend && venv\Scripts\activate && python main.py"
timeout /t 3
start "Veronica Frontend" cmd /k "cd frontend && npm start"

echo Both services starting...
echo Backend: http://127.0.0.1:5000
echo Frontend: http://127.0.0.1:3000
