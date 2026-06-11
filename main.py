import os
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
import threading
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()

from database import get_db, init_db, SessionLocal
from models import (
    Email, Commitment, Task, DecisionLog, SenderProfile, CognitiveLoadSnapshot,
    EmailSchema, CommitmentSchema, TaskSchema, DecisionLogSchema, SenderProfileSchema
)
import ai_service
import gmail_service

app = FastAPI(
    title="Email Intelligence System",
    description="Autonomous AI-powered email management",
    version="1.0.0"
)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OAUTH_STATE_FILE = DATA_DIR / "oauth_state.json"
ACCOUNT_STATE_FILE = DATA_DIR / "account_state.json"
SYNC_LOCK = threading.Lock()
SYNC_STATUS = {
    "running": False,
    "stage": "idle",
    "message": "",
    "progress_percent": 0,
    "total_fetched": 0,
    "processed": 0,
    "skipped": 0,
    "started_at": None,
    "finished_at": None
}


def _set_sync_status(**updates):
    with SYNC_LOCK:
        SYNC_STATUS.update(updates)


def _get_sync_status():
    with SYNC_LOCK:
        return dict(SYNC_STATUS)


def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_file(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _load_oauth_state_store() -> dict:
    return _load_json_file(OAUTH_STATE_FILE)


def _save_oauth_state_store(store: dict):
    _save_json_file(OAUTH_STATE_FILE, store)


def _clear_oauth_state_store():
    if OAUTH_STATE_FILE.exists():
        try:
            OAUTH_STATE_FILE.unlink()
        except Exception:
            pass


def _load_account_state() -> dict:
    return _load_json_file(ACCOUNT_STATE_FILE)


def _save_account_state(state: dict):
    _save_json_file(ACCOUNT_STATE_FILE, state)


def _clear_account_state():
    if ACCOUNT_STATE_FILE.exists():
        try:
            ACCOUNT_STATE_FILE.unlink()
        except Exception:
            pass


def _get_current_account_email() -> Optional[str]:
    state = _load_account_state()
    email = (state.get("email") or "").strip().lower()
    return email or None


def _require_account_email() -> str:
    email = _get_current_account_email()
    if not email:
        raise HTTPException(status_code=401, detail="No Gmail account selected")
    return email


def _latest_received_email_time(db: Session, account_email: str) -> Optional[datetime]:
    latest_email = db.query(Email).filter(
        Email.account_email == account_email,
        Email.received_at.isnot(None)
    ).order_by(Email.received_at.desc()).first()
    return latest_email.received_at if latest_email else None


def _normalize_for_local_date(timestamp: Optional[datetime], local_tz) -> Optional[datetime]:
    if not timestamp:
        return None
    try:
        if timestamp.tzinfo:
            return timestamp.astimezone(local_tz)
        return timestamp.replace(tzinfo=ai_service.timezone.utc).astimezone(local_tz)
    except Exception:
        return None


def _build_reply_context(
    email: Email,
    sender_profile: Optional[SenderProfile] = None,
    commitments: Optional[list] = None,
    decision_logs: Optional[list] = None,
    thread_context: Optional[list] = None,
) -> dict:
    return {
        "sender": email.sender,
        "sender_email": email.sender_email,
        "recipient": email.recipient,
        "subject": email.subject,
        "body": email.body,
        "body_snippet": email.body_snippet,
        "classification": email.classification,
        "deadline": email.deadline,
        "ai_summary": email.ai_summary,
        "ai_reasoning": email.ai_reasoning,
        "mood": email.mood,
        "stress_score": email.stress_score,
        "action_items": email.action_items or [],
        "commitment_phrases": email.commitment_phrases or [],
        "sender_profile": SenderProfileSchema.model_validate(sender_profile).model_dump() if sender_profile else None,
        "commitments": [CommitmentSchema.model_validate(c).model_dump() for c in (commitments or [])],
        "decision_logs": [DecisionLogSchema.model_validate(log).model_dump() for log in (decision_logs or [])],
        "thread_context": thread_context or [],
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
def startup():
    init_db()
    print("🚀 Email Intelligence System started")
    print("📊 Dashboard: http://localhost:8000")
    _set_sync_status(
        running=False,
        stage="idle",
        message="Ready to sync Gmail data.",
        progress_percent=0,
        total_fetched=0,
        processed=0,
        skipped=0,
        started_at=None,
        finished_at=None
    )


# ─── Root → Serve Frontend ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open(FRONTEND_DIR / "index.html", "r", encoding="utf-8", errors="replace") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Frontend not found. Make sure frontend/index.html exists.</h1>")


# ─── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login():
    """Redirect user to Google OAuth."""
    try:
        _clear_oauth_state_store()
        flow = gmail_service.get_flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account consent"
        )
        store = {
            state: {
                "code_verifier": getattr(flow, "code_verifier", None),
                "created_at": datetime.utcnow().isoformat()
            }
        }
        _save_oauth_state_store(store)
        return RedirectResponse(url=auth_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/callback")
async def auth_callback(code: str, state: Optional[str] = None):
    """Handle Google OAuth callback."""
    try:
        store = _load_oauth_state_store()
        if not state or state not in store:
            _clear_oauth_state_store()
            return RedirectResponse(url="/?auth=error&msg=Missing%20or%20expired%20OAuth%20state.%20Please%20try%20again.")

        flow_record = store.pop(state, None)
        _save_oauth_state_store(store)
        flow = gmail_service.get_flow()
        code_verifier = (flow_record or {}).get("code_verifier")
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        user_info = gmail_service.get_user_profile(credentials=flow.credentials)
        current_email = (user_info.get("email") or "").strip().lower()
        gmail_service.save_token(flow.credentials, current_email)
        if current_email:
            _save_account_state({"email": current_email})
        return RedirectResponse(url="/?auth=success")
    except Exception as e:
        return RedirectResponse(url=f"/?auth=error&msg={str(e)[:100]}")


@app.get("/auth/status")
async def auth_status():
    """Check if user is authenticated."""
    current_email = _get_current_account_email()
    authenticated = gmail_service.is_authenticated(current_email)
    user_info = {}
    if authenticated:
        try:
            user_info = gmail_service.get_user_profile(current_email)
            current_email = (user_info.get("email") or current_email or "").strip().lower()
            if current_email:
                _save_account_state({"email": current_email})
        except Exception:
            pass
    return {"authenticated": authenticated, "user": user_info}


@app.post("/auth/logout")
async def auth_logout():
    """Remove stored token."""
    current_email = _get_current_account_email()
    gmail_service.remove_token(current_email)
    if not current_email:
        gmail_service.remove_token()
    _clear_account_state()
    _clear_oauth_state_store()
    return {"message": "Logged out successfully"}


# ─── EMAIL ROUTES ──────────────────────────────────────────────────────────────

@app.post("/emails/process")
async def process_emails(
    background_tasks: BackgroundTasks,
    max_emails: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """Fetch emails from Gmail and process them with AI."""
    account_email = _require_account_email()
    if not gmail_service.is_authenticated(account_email):
        raise HTTPException(status_code=401, detail="Not authenticated with Gmail")

    try:
        latest_received_at = _latest_received_email_time(db, account_email)
        sync_after = latest_received_at - timedelta(days=1) if latest_received_at else None
        _set_sync_status(
            running=True,
            stage="fetching",
            message="Fetching emails from Gmail...",
            progress_percent=5,
            total_fetched=0,
            processed=0,
            skipped=0,
            started_at=datetime.utcnow().isoformat(),
            finished_at=None
        )
        raw_emails = gmail_service.fetch_emails(
            account_email=account_email,
            max_results=max_emails,
            unread_only=False,
            received_only=True,
            include_body=True,
            newer_than_months=6,
            after_datetime=sync_after
        )
        _set_sync_status(
            stage="processing",
            message=f"Fetched {len(raw_emails)} emails. Processing with AI...",
            progress_percent=15,
            total_fetched=len(raw_emails)
        )
    except Exception as e:
        _set_sync_status(
            running=False,
            stage="error",
            message=f"Gmail fetch error: {str(e)}",
            progress_percent=0,
            finished_at=datetime.utcnow().isoformat()
        )
        raise HTTPException(status_code=500, detail=f"Gmail fetch error: {str(e)}")

    processed = 0
    skipped = 0
    active_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status.in_(["pending", "in_progress"])).count()
    overdue_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "missed").count()
    pending_commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.status == "pending").count()
    unread_urgent = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Urgent", Email.is_read == False).count()
    load = ai_service.calculate_cognitive_load(active_tasks, overdue_tasks, pending_commitments, unread_urgent)

    for raw in raw_emails:
        # Skip if already processed
        existing = db.query(Email).filter(Email.account_email == account_email, Email.gmail_id == raw["gmail_id"]).first()
        if existing:
            skipped += 1
            total_done = processed + skipped
            progress = 15 + int((total_done / max(len(raw_emails), 1)) * 80)
            _set_sync_status(
                message=f"Processing emails... {total_done}/{len(raw_emails)}",
                progress_percent=min(progress, 95),
                processed=processed,
                skipped=skipped
            )
            continue

        # AI Analysis
        try:
            analysis = await ai_service.analyze_email(
                subject=raw["subject"],
                body=raw["body"],
                sender=f"{raw['sender']} <{raw['sender_email']}>"
            )
        except Exception as e:
            print(f"AI error for {raw['gmail_id']}: {e}")
            analysis = {
                "classification": "Informational",
                "priority_score": 5,
                "deadline": None,
                "commitment_phrases": [],
                "action_items": [],
                "mood": "Neutral",
                "stress_score": 0,
                "suggested_action": "mark_read",
                "ai_summary": raw["body_snippet"],
                "ai_reply_draft": None,
                "language": "en",
                "reasoning": "AI analysis unavailable",
                "commitment_warning": None
            }

        # Cognitive load check
        active_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status.in_(["pending", "in_progress"])).count()
        overdue_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "missed").count()
        pending_commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.status == "pending").count()
        unread_urgent = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Urgent", Email.is_read == False).count()

        load = ai_service.calculate_cognitive_load(active_tasks, overdue_tasks, pending_commitments, unread_urgent)

        # Suppress low-priority actions under high load
        was_suppressed = False
        suppression_reason = None
        if load["is_suppressing"] and analysis["classification"] in ["Informational"] and analysis["priority_score"] < 4:
            was_suppressed = True
            suppression_reason = f"Cognitive load at {load['score']} (threshold: {load['threshold']}). Low-priority action deferred."
            analysis["suggested_action"] = "defer"

        # Save email to DB
        email_obj = Email(
            account_email=account_email,
            gmail_id=raw["gmail_id"],
            sender=raw["sender"],
            sender_email=raw["sender_email"],
            recipient=raw.get("recipient", ""),
            subject=raw["subject"],
            body=raw["body"],
            body_snippet=raw["body_snippet"],
            classification=analysis.get("classification", "Informational"),
            priority_score=analysis.get("priority_score", 5),
            deadline=analysis.get("deadline"),
            commitment_phrases=analysis.get("commitment_phrases", []),
            action_items=analysis.get("action_items", []),
            mood=analysis.get("mood", "Neutral"),
            stress_score=analysis.get("stress_score", 0),
            suggested_action=analysis.get("suggested_action", "mark_read"),
            ai_summary=analysis.get("ai_summary"),
            ai_reply_draft=None,
            ai_reasoning=analysis.get("reasoning"),
            language=analysis.get("language", "en"),
            received_at=raw.get("received_at")
        )
        db.add(email_obj)
        db.flush()

        # Save commitments
        for phrase in analysis.get("commitment_phrases", []):
            if phrase:
                commitment = Commitment(
                    account_email=account_email,
                    email_id=email_obj.id,
                    email_subject=raw["subject"],
                    sender=raw["sender"],
                    commitment_text=phrase,
                    deadline=analysis.get("deadline"),
                    status="pending"
                )
                db.add(commitment)

        # Create task for Task/Urgent emails
        if analysis.get("classification") in ["Urgent", "Task"] and analysis.get("action_items"):
            for action in analysis.get("action_items", [])[:2]:
                if action:
                    task = Task(
                        account_email=account_email,
                        email_id=email_obj.id,
                        title=action[:200],
                        description=f"From email: {raw['subject']}",
                        priority="critical" if analysis.get("classification") == "Urgent" else "high",
                        priority_score=analysis.get("priority_score", 7),
                        deadline=analysis.get("deadline"),
                        status="pending"
                    )
                    db.add(task)

        # Log decision
        log = DecisionLog(
            account_email=account_email,
            email_id=email_obj.id,
            email_subject=raw["subject"],
            sender=raw["sender"],
            decision_taken=analysis.get("suggested_action", "mark_read"),
            reason=analysis.get("reasoning", ""),
            classification=analysis.get("classification"),
            cognitive_load_at_time=load["score"],
            was_suppressed=was_suppressed,
            suppression_reason=suppression_reason
        )
        db.add(log)

        # Update sender profile (background)
        background_tasks.add_task(
            _update_sender_profile_bg,
            account_email, raw["sender_email"], raw["sender"], analysis
        )

        # Apply Gmail label
        try:
            label = f"AI/{analysis.get('classification', 'Informational')}"
            gmail_service.apply_label(raw["gmail_id"], label, account_email=account_email)
            email_obj.label_applied = label
        except Exception:
            pass

        # Mark as read if informational
        if analysis.get("suggested_action") == "mark_read":
            try:
                gmail_service.mark_as_read(raw["gmail_id"], account_email=account_email)
                email_obj.is_read = True
            except Exception:
                pass

        processed += 1
        total_done = processed + skipped
        progress = 15 + int((total_done / max(len(raw_emails), 1)) * 80)
        _set_sync_status(
            message=f"Processing emails... {total_done}/{len(raw_emails)}",
            progress_percent=min(progress, 95),
            processed=processed,
            skipped=skipped
        )

    # Save cognitive load snapshot
    snapshot = CognitiveLoadSnapshot(
        account_email=account_email,
        load_score=load["score"],
        active_tasks=active_tasks,
        overdue_tasks=overdue_tasks,
        pending_commitments=pending_commitments,
        unread_urgent=unread_urgent,
        is_suppressing=load["is_suppressing"]
    )
    db.add(snapshot)
    db.commit()

    _set_sync_status(
        running=False,
        stage="completed",
        message=f"Sync complete. Processed {processed} emails.",
        progress_percent=100,
        total_fetched=len(raw_emails),
        processed=processed,
        skipped=skipped,
        finished_at=datetime.utcnow().isoformat()
    )

    return {
        "processed": processed,
        "skipped": skipped,
        "total": len(raw_emails),
        "cognitive_load": load
    }


@app.get("/emails/sync/status")
async def emails_sync_status():
    return _get_sync_status()


async def _update_sender_profile_bg(account_email: str, sender_email: str, sender_name: str, analysis: dict):
    """Background task to update sender profile."""
    db = SessionLocal()
    try:
        existing = db.query(SenderProfile).filter(
            SenderProfile.account_email == account_email,
            SenderProfile.sender_email == sender_email
        ).first()
        profile_data = await ai_service.update_sender_profile(
            sender_email, sender_name, analysis,
            existing.__dict__ if existing else None
        )

        if existing:
            existing.email_count += 1
            existing.communication_style = profile_data.get("communication_style", existing.communication_style)
            existing.typical_urgency = profile_data.get("typical_urgency", existing.typical_urgency)
            existing.common_topics = profile_data.get("common_topics", existing.common_topics)
            existing.reliability_score = profile_data.get("reliability_score", existing.reliability_score)
            existing.notes = profile_data.get("notes", existing.notes)
            existing.last_email_at = datetime.utcnow()
            # Update averages
            existing.avg_priority = (existing.avg_priority + analysis.get("priority_score", 5)) / 2
            existing.avg_stress_score = (existing.avg_stress_score + analysis.get("stress_score", 0)) / 2
        else:
            profile = SenderProfile(
                account_email=account_email,
                email=f"{account_email}::{sender_email}",
                sender_email=sender_email,
                name=sender_name,
                email_count=1,
                avg_priority=analysis.get("priority_score", 5),
                avg_stress_score=analysis.get("stress_score", 0),
                common_topics=profile_data.get("common_topics", []),
                communication_style=profile_data.get("communication_style"),
                typical_urgency=profile_data.get("typical_urgency"),
                reliability_score=profile_data.get("reliability_score", 50.0),
                notes=profile_data.get("notes"),
                last_email_at=datetime.utcnow()
            )
            db.add(profile)

        db.commit()
    except Exception as e:
        print(f"Sender profile update error: {e}")
    finally:
        db.close()


@app.get("/emails", response_model=List[EmailSchema])
async def get_emails(
    skip: int = 0,
    limit: int = 50,
    classification: Optional[str] = None,
    db: Session = Depends(get_db)
):
    account_email = _require_account_email()
    query = db.query(Email).filter(Email.account_email == account_email)
    if classification:
        # Allow case-insensitive filtering so UI tabs work regardless of how classification was stored
        query = query.filter(func.lower(Email.classification) == classification.lower())
    return query.order_by(func.coalesce(Email.received_at, Email.processed_at).desc()).offset(skip).limit(limit).all()


@app.get("/emails/{email_id}")
async def get_email(email_id: int, db: Session = Depends(get_db)):
    account_email = _require_account_email()
    email = db.query(Email).filter(Email.account_email == account_email, Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    # Get sender profile
    sender_profile = db.query(SenderProfile).filter(
        SenderProfile.account_email == account_email,
        SenderProfile.sender_email == email.sender_email
    ).first()
    commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.email_id == email_id).all()
    decision_logs = db.query(DecisionLog).filter(DecisionLog.account_email == account_email, DecisionLog.email_id == email_id).all()
    thread_context = gmail_service.fetch_thread_context(email.gmail_id, account_email=account_email)

    return {
        "email": EmailSchema.model_validate(email).model_dump(),
        "sender_profile": SenderProfileSchema.model_validate(sender_profile).model_dump() if sender_profile else None,
        "commitments": [CommitmentSchema.model_validate(c).model_dump() for c in commitments],
        "decision_logs": [DecisionLogSchema.model_validate(d).model_dump() for d in decision_logs],
        "thread_context": thread_context,
    }


@app.get("/emails/{email_id}/voice-summary")
async def voice_summary(email_id: int, db: Session = Depends(get_db)):
    """UNIQUE FEATURE: Get AI-generated spoken summary for voice playback."""
    account_email = _require_account_email()
    email = db.query(Email).filter(Email.account_email == account_email, Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    email_data = {
        "sender": email.sender,
        "subject": email.subject,
        "classification": email.classification,
        "priority_score": email.priority_score,
        "ai_summary": email.ai_summary,
        "deadline": email.deadline,
        "stress_score": email.stress_score,
        "mood": email.mood
    }
    summary = await ai_service.generate_voice_summary(email_data)
    return {"voice_text": summary}


@app.post("/emails/{email_id}/reply")
async def send_reply(
    email_id: int,
    reply_body: str = Query(...),
    db: Session = Depends(get_db)
):
    account_email = _require_account_email()
    email = db.query(Email).filter(Email.account_email == account_email, Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    try:
        success = gmail_service.send_reply(
            email.gmail_id, email.sender_email, email.subject, reply_body, account_email=account_email
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if success:
        email.is_replied = True
        db.commit()
        # Log decision
        log = DecisionLog(
            account_email=account_email,
            email_id=email_id,
            email_subject=email.subject,
            sender=email.sender,
            decision_taken="replied",
            reason="User manually approved and sent AI-drafted reply",
            classification=email.classification,
        )
        db.add(log)
        db.commit()
        return {"success": True, "message": "Reply sent successfully"}
    raise HTTPException(status_code=500, detail="Failed to send reply")


@app.post("/emails/{email_id}/draft-reply")
async def draft_reply(email_id: int, db: Session = Depends(get_db)):
    account_email = _require_account_email()
    email = db.query(Email).filter(Email.account_email == account_email, Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.classification == "Spam":
        raise HTTPException(status_code=400, detail="Draft reply is not available for spam emails")

    if not email.body or len((email.body or "").strip()) < 40:
        try:
            latest = gmail_service.fetch_email_detail(email.gmail_id, account_email=account_email)
            email.body = latest.get("body") or email.body
            email.body_snippet = latest.get("body_snippet") or email.body_snippet
            email.recipient = latest.get("recipient") or email.recipient
            email.received_at = latest.get("received_at") or email.received_at
            db.commit()
        except Exception:
            db.rollback()

    sender_profile = db.query(SenderProfile).filter(
        SenderProfile.account_email == account_email,
        SenderProfile.sender_email == email.sender_email
    ).first()
    commitments = db.query(Commitment).filter(
        Commitment.account_email == account_email,
        Commitment.email_id == email_id
    ).all()
    decision_logs = db.query(DecisionLog).filter(
        DecisionLog.account_email == account_email,
        DecisionLog.email_id == email_id
    ).order_by(DecisionLog.timestamp.desc()).limit(3).all()
    thread_context = gmail_service.fetch_thread_context(email.gmail_id, account_email=account_email)

    email.ai_reply_draft = await ai_service.generate_reply_draft(
        _build_reply_context(email, sender_profile, commitments, decision_logs, thread_context)
    )
    db.commit()
    return {"draft": email.ai_reply_draft}


# ─── DASHBOARD ROUTES ──────────────────────────────────────────────────────────

@app.get("/dashboard/stats")
async def dashboard_stats(db: Session = Depends(get_db)):
    """Comprehensive dashboard statistics."""
    account_email = _require_account_email()
    total_emails = db.query(Email).filter(Email.account_email == account_email).count()
    urgent = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Urgent").count()
    tasks = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Task").count()
    informational = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Informational").count()
    spam = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Spam").count()
    unread = db.query(Email).filter(Email.account_email == account_email, Email.is_read == False).count()
    replied = db.query(Email).filter(Email.account_email == account_email, Email.is_replied == True).count()

    total_tasks = db.query(Task).filter(Task.account_email == account_email).count()
    pending_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "pending").count()
    completed_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "completed").count()
    missed_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "missed").count()

    total_commitments = db.query(Commitment).filter(Commitment.account_email == account_email).count()
    pending_commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.status == "pending").count()
    missed_commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.status == "missed").count()

    # Recent emails
    recent_emails = db.query(Email).filter(Email.account_email == account_email).order_by(func.coalesce(Email.received_at, Email.processed_at).desc()).limit(5).all()

    # Average stress score
    emails_with_stress = db.query(Email).filter(Email.account_email == account_email, Email.stress_score > 0).all()
    avg_stress = sum(e.stress_score for e in emails_with_stress) / len(emails_with_stress) if emails_with_stress else 0

    # Cognitive load
    active_task_count = db.query(Task).filter(Task.account_email == account_email, Task.status.in_(["pending", "in_progress"])).count()
    overdue_count = db.query(Task).filter(Task.account_email == account_email, Task.status == "missed").count()
    unread_urgent = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Urgent", Email.is_read == False).count()
    load = ai_service.calculate_cognitive_load(active_task_count, overdue_count, pending_commitments, unread_urgent)

    # Mood distribution
    mood_counts = {}
    for email in db.query(Email).filter(Email.account_email == account_email).all():
        mood = email.mood or "Neutral"
        mood_counts[mood] = mood_counts.get(mood, 0) + 1

    # Daily receive volume over the last 7 days based on Gmail receive time.
    local_now = datetime.now().astimezone()
    local_tz = local_now.tzinfo
    day_buckets = {}
    for i in range(6, -1, -1):
        day = (local_now - timedelta(days=i)).date()
        day_buckets[day] = 0

    volume_emails = db.query(Email).filter(Email.account_email == account_email).all()
    for email in volume_emails:
        local_timestamp = _normalize_for_local_date(email.received_at or email.processed_at, local_tz)
        if not local_timestamp:
            continue
        day_key = local_timestamp.date()
        if day_key in day_buckets:
            day_buckets[day_key] += 1

    if sum(day_buckets.values()) == 0 and volume_emails:
        latest_timestamp = None
        for email in volume_emails:
            candidate = _normalize_for_local_date(email.received_at or email.processed_at, local_tz)
            if candidate and (latest_timestamp is None or candidate > latest_timestamp):
                latest_timestamp = candidate
        if latest_timestamp:
            day_buckets = {}
            for i in range(6, -1, -1):
                day = (latest_timestamp - timedelta(days=i)).date()
                day_buckets[day] = 0
            for email in volume_emails:
                local_timestamp = _normalize_for_local_date(email.received_at or email.processed_at, local_tz)
                if not local_timestamp:
                    continue
                day_key = local_timestamp.date()
                if day_key in day_buckets:
                    day_buckets[day_key] += 1

    daily_stats = [
        {"date": day.strftime("%m/%d"), "count": day_buckets[day]}
        for day in day_buckets
    ]

    return {
        "emails": {
            "total": total_emails,
            "urgent": urgent,
            "tasks": tasks,
            "informational": informational,
            "spam": spam,
            "unread": unread,
            "replied": replied
        },
        "tasks": {
            "total": total_tasks,
            "pending": pending_tasks,
            "completed": completed_tasks,
            "missed": missed_tasks
        },
        "commitments": {
            "total": total_commitments,
            "pending": pending_commitments,
            "missed": missed_commitments
        },
        "cognitive_load": load,
        "avg_stress_score": round(avg_stress, 1),
        "mood_distribution": mood_counts,
        "daily_volume": daily_stats,
        "recent_emails": [
            {
                "id": e.id,
                "sender": e.sender,
                "subject": e.subject,
                "classification": e.classification,
                "priority_score": e.priority_score,
                "stress_score": e.stress_score,
                "mood": e.mood,
                "processed_at": e.processed_at.isoformat() if e.processed_at else None,
                "received_at": e.received_at.isoformat() if e.received_at else None
            } for e in recent_emails
        ]
    }


@app.get("/dashboard/cognitive-load")
async def cognitive_load(db: Session = Depends(get_db)):
    """Current cognitive load with history."""
    account_email = _require_account_email()
    active_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status.in_(["pending", "in_progress"])).count()
    overdue_tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "missed").count()
    pending_commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.status == "pending").count()
    unread_urgent = db.query(Email).filter(Email.account_email == account_email, Email.classification == "Urgent", Email.is_read == False).count()

    load = ai_service.calculate_cognitive_load(active_tasks, overdue_tasks, pending_commitments, unread_urgent)
    load["breakdown"] = {
        "active_tasks": active_tasks,
        "overdue_tasks": overdue_tasks,
        "pending_commitments": pending_commitments,
        "unread_urgent": unread_urgent
    }

    # Last 10 snapshots
    snapshots = db.query(CognitiveLoadSnapshot).filter(CognitiveLoadSnapshot.account_email == account_email).order_by(
        CognitiveLoadSnapshot.recorded_at.desc()
    ).limit(10).all()
    load["history"] = [
        {
            "score": s.load_score,
            "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
            "is_suppressing": s.is_suppressing
        } for s in reversed(snapshots)
    ]

    return load


@app.get("/commitments")
async def get_commitments(status: Optional[str] = None, db: Session = Depends(get_db)):
    account_email = _require_account_email()
    query = db.query(Commitment).filter(Commitment.account_email == account_email)
    if status:
        query = query.filter(Commitment.status == status)
    return query.order_by(Commitment.created_at.desc()).all()


@app.put("/commitments/{commitment_id}/status")
async def update_commitment_status(
    commitment_id: int,
    status: str = Query(..., pattern="^(pending|completed|missed)$"),
    db: Session = Depends(get_db)
):
    account_email = _require_account_email()
    commitment = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.id == commitment_id).first()
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    commitment.status = status
    db.commit()
    return {"success": True}


@app.get("/tasks")
async def get_tasks(status: Optional[str] = None, db: Session = Depends(get_db)):
    account_email = _require_account_email()
    query = db.query(Task).filter(Task.account_email == account_email)
    if status:
        query = query.filter(Task.status == status)
    return query.order_by(Task.created_at.desc()).all()


@app.put("/tasks/{task_id}/status")
async def update_task_status(
    task_id: int,
    status: str = Query(...),
    db: Session = Depends(get_db)
):
    account_email = _require_account_email()
    task = db.query(Task).filter(Task.account_email == account_email, Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = status
    db.commit()
    return {"success": True}


@app.get("/decision-logs")
async def get_decision_logs(
    limit: int = 50,
    db: Session = Depends(get_db)
):
    account_email = _require_account_email()
    logs = db.query(DecisionLog).filter(DecisionLog.account_email == account_email).order_by(DecisionLog.timestamp.desc()).limit(limit).all()
    return [DecisionLogSchema.model_validate(log).model_dump() for log in logs]


@app.get("/sender-profiles")
async def get_sender_profiles(db: Session = Depends(get_db)):
    account_email = _require_account_email()
    profiles = db.query(SenderProfile).filter(SenderProfile.account_email == account_email).order_by(SenderProfile.email_count.desc()).all()
    return [
        {**SenderProfileSchema.model_validate(p).model_dump(), "email": p.sender_email or p.email}
        for p in profiles
    ]


@app.get("/sender-profiles/{email}")
async def get_sender_profile(email: str, db: Session = Depends(get_db)):
    account_email = _require_account_email()
    profile = db.query(SenderProfile).filter(
        SenderProfile.account_email == account_email,
        SenderProfile.sender_email == email
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Sender profile not found")
    return {**SenderProfileSchema.model_validate(profile).model_dump(), "email": profile.sender_email or profile.email}


@app.get("/inbox/ask")
@app.post("/inbox/ask")
async def ask_inbox(question: str = Query(...), db: Session = Depends(get_db)):
    """UNIQUE FEATURE: Ask questions about your inbox in natural language."""
    account_email = _require_account_email()
    emails = db.query(Email).filter(Email.account_email == account_email).order_by(func.coalesce(Email.received_at, Email.processed_at).desc()).limit(30).all()
    emails_context = [
        {
            "sender": e.sender,
            "subject": e.subject,
            "classification": e.classification,
            "priority_score": e.priority_score,
            "ai_summary": e.ai_summary,
            "mood": e.mood,
            "stress_score": e.stress_score,
            "deadline": e.deadline,
            "commitment_phrases": e.commitment_phrases or [],
            "is_read": e.is_read
        } for e in emails
    ]
    answer = await ai_service.ask_inbox_question(question, emails_context)
    return {"answer": answer, "question": question}


# ─── Accountability Checker ─────────────────────────────────────────────────────

@app.post("/accountability/check")
async def run_accountability_check(db: Session = Depends(get_db)):
    """Check for missed deadlines and overdue commitments."""
    account_email = _require_account_email()
    now = datetime.now(ai_service.timezone.utc)
    updated = 0

    commitments = db.query(Commitment).filter(Commitment.account_email == account_email, Commitment.status == "pending").all()
    for c in commitments:
        if c.deadline:
            deadline = ai_service.parse_deadline_iso(c.deadline)
            if deadline and deadline < now:
                c.status = "missed"
                log = DecisionLog(
                    account_email=account_email,
                    email_id=c.email_id,
                    email_subject=c.email_subject,
                    sender=c.sender,
                    decision_taken="marked_missed",
                    reason=f"Commitment deadline {c.deadline} has passed without completion.",
                    classification="Accountability"
                )
                db.add(log)
                updated += 1

    tasks = db.query(Task).filter(Task.account_email == account_email, Task.status == "pending").all()
    for t in tasks:
        if t.deadline:
            deadline = ai_service.parse_deadline_iso(t.deadline)
            if deadline and deadline < now:
                t.status = "missed"
                updated += 1

    db.commit()
    return {"updated": updated, "message": f"{updated} items marked as missed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
