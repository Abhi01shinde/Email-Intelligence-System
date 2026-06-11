# 🧠 Email Intelligence System

> Autonomous AI-powered Gmail management with cognitive load protection, voice summaries, and sender intelligence profiling.

## ✨ Features

| Feature | Description |
|---|---|
| ⚡ AI Email Classification | Urgent / Task / Informational / Spam with priority scoring |
| 🔊 **Voice Summary** *(Unique)* | Browser reads emails aloud with AI-generated spoken summary |
| 😤 **Pressure Score** *(Unique)* | Detects how much stress/pressure an email is designed to create |
| 🕵️ **Sender Intelligence** *(Unique)* | AI builds behavioral profiles for every sender |
| 🧠 Cognitive Load Guard | Suppresses low-priority actions when you're overwhelmed |
| 🤝 Commitment Tracker | Extracts and tracks every promise/commitment in emails |
| 📋 Task Manager | Auto-creates tasks from action items in emails |
| 💬 Ask Your Inbox | Natural language Q&A about your emails |
| 📋 Decision Logs | Full explainability for every AI decision |
| ⏰ Accountability Check | Flags overdue commitments and missed deadlines |

## 🚀 Quick Start (VS Code)

### Step 1: Download & Open
1. Extract the `email_intelligence` folder
2. Open VS Code → File → Open Folder → select `email_intelligence`

### Step 2: Google Cloud Setup (Required)
1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable **Gmail API**: APIs & Services → Library → Search "Gmail API" → Enable
4. Go to **APIs & Services → Credentials**
5. Click **Create Credentials → OAuth 2.0 Client IDs**
6. Application type: **Web application**
7. Add Authorized redirect URI: `http://localhost:8000/auth/callback`
8. Download credentials — copy **Client ID** and **Client Secret**
9. Paste them in `.env` file (already filled if you used the provided .env)

> **OAuth Consent Screen**: Go to OAuth consent screen → Set to "External" → Add your Gmail as test user

### Step 3: Run the Project

**Windows:**
```
Double-click start.bat
```
OR in VS Code terminal:
```cmd
start.bat
```

**Mac/Linux:**
```bash
chmod +x start.sh
./start.sh
```

**Manual (any OS):**
```bash
# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start server
python main.py
```

### Step 4: Open Dashboard
Go to: **http://localhost:8000**

### Step 5: Connect Gmail
1. Click "Connect Gmail Account" on the login screen
2. Sign in with your Google account
3. Allow the requested permissions
4. You'll be redirected back to the dashboard

### Step 6: Process Emails
1. Go to **Emails** page
2. Click **"⚡ Fetch & Process Emails"**
3. Wait for AI to classify each email
4. Explore the dashboard!

## 📁 Project Structure

```
email_intelligence/
├── main.py              # FastAPI backend (all routes)
├── database.py          # SQLite database setup
├── models.py            # Database models & schemas
├── ai_service.py        # OpenAI GPT-4o-mini integration
├── gmail_service.py     # Gmail OAuth & API
├── requirements.txt     # Python dependencies
├── .env                 # Configuration (API keys)
├── start.bat            # Windows start script
├── start.sh             # Mac/Linux start script
├── frontend/
│   └── index.html       # Complete React dashboard (no build needed!)
└── data/
    ├── email_intelligence.db   # SQLite database (auto-created)
    └── token.json              # Gmail auth token (auto-created)
```

## 🔧 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Dashboard UI |
| GET | `/auth/login` | Gmail OAuth login |
| GET | `/auth/status` | Check auth status |
| POST | `/emails/process` | Fetch & AI-process emails |
| GET | `/emails` | List processed emails |
| GET | `/emails/{id}` | Email detail + AI analysis |
| GET | `/emails/{id}/voice-summary` | Voice summary text |
| POST | `/emails/{id}/reply` | Send reply |
| GET | `/dashboard/stats` | Full stats |
| GET | `/dashboard/cognitive-load` | Cognitive load |
| GET | `/commitments` | All commitments |
| GET | `/tasks` | All tasks |
| GET | `/decision-logs` | AI decision logs |
| GET | `/sender-profiles` | Sender profiles |
| POST | `/inbox/ask` | Ask inbox a question |
| POST | `/accountability/check` | Run accountability check |

Full API docs: http://localhost:8000/docs

## ⚠️ Troubleshooting

**"redirect_uri_mismatch" error:**
→ In Google Cloud Console, make sure your redirect URI is exactly: `http://localhost:8000/auth/callback`

**"Access blocked: This app's request is invalid":**
→ Go to OAuth Consent Screen → Add your email as a Test User

**ModuleNotFoundError:**
→ Make sure your virtual environment is activated before running

**Port 8000 already in use:**
→ Change port in main.py: `uvicorn.run("main:app", port=8001, ...)`

**AI analysis slow:**
→ Normal — GPT-4o-mini takes 1-3 seconds per email. Processing 10 emails takes ~30 seconds.

## 💡 Usage Tips

1. **First run**: Process 5-10 emails to populate the dashboard
2. **Voice Summary**: Click "🔊 Voice Summary" on any email in detail view
3. **Ask Inbox**: Try "Which emails need my attention today?" in the chat widget
4. **Accountability Check**: Run it daily to catch missed commitments
5. **Cognitive Load**: When it hits "Critical", the system automatically defers low-priority emails

## 🔒 Privacy

- All email data is stored **locally** in SQLite (`data/email_intelligence.db`)
- Only email text is sent to OpenAI for classification
- Gmail credentials never leave your machine
- No third-party tracking or analytics

*Built with FastAPI + SQLite + OpenAI GPT-4o-mini + React*
=======
# Email Intelligence System

## Overview

Email Intelligence System is an AI-powered email management platform designed to help users efficiently manage their inbox by automatically classifying emails, detecting priorities, extracting tasks and commitments, and generating intelligent summaries.

The system integrates Artificial Intelligence with Gmail API to transform traditional email management into a smart productivity solution.


## Features

* Email Classification
* Priority Detection
* Task Extraction
* Commitment Detection
* AI-based Email Summarization
* Gmail API Integration
* Interactive Dashboard
* Decision Logs
* Sender Profile Analysis

## System Architecture

Email Retrieval (Gmail API)
↓
Email Analysis
↓
Priority Detection
↓
Task & Commitment Extraction
↓
Email Summarization
↓
Dashboard Visualization

## Technology Stack

### Frontend

* HTML
* Tailwind CSS
* JavaScript

### Backend

* Python
* FastAPI

### Database

* SQLite

### APIs

* Gmail API

### AI Components

* Email Classification
* Priority Detection
* Task Extraction
* Summarization

## Installation

### Clone Repository

git clone https://github.com/Abhi01shinde/Email-Intelligence-System.git

### Navigate to Project

cd Email-Intelligence-System

### Install Dependencies

pip install -r requirements.txt

### Run Application

uvicorn main:app --reload

## Future Enhancements

* Smart Reply Generation
* Calendar Integration
* User Preference Learning
* Mobile Application
* Multi-Email Provider Support

## License

This project is developed for educational and research purposes.

