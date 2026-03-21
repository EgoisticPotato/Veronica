#!/bin/bash
echo "Starting Veronica..."

# Backend
cd backend
source venv/bin/activate 2>/dev/null || python3 -m venv venv && source venv/bin/activate
python main.py &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"

# Wait for backend to be ready
sleep 2

# Frontend
cd ../frontend
npm start &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"

echo ""
echo "Veronica is running:"
echo "  Backend:  http://127.0.0.1:5000"
echo "  Frontend: http://127.0.0.1:3000"
echo ""
echo "Press Ctrl+C to stop"

# Wait and cleanup
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Stopped.'" SIGINT SIGTERM
wait
