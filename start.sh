#!/bin/bash

echo "========================================"
echo "  Email Intelligence System - Startup"
echo "========================================"
echo ""

# Create virtual environment if not exists
if [ ! -d "venv" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
echo "[2/4] Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "[3/4] Installing dependencies..."
pip install -r requirements.txt --quiet

# Create data directory
mkdir -p data

# Start server
echo "[4/4] Starting server..."
echo ""
echo "✅ Dashboard: http://localhost:8000"
echo "✅ API Docs:   http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""
python main.py
