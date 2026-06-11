import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_QUOTA_EXHAUSTED = False


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "insufficient_quota" in msg or "exceeded your current quota" in msg or "error code: 429" in msg


def _extract_commitment_phrases(text: str) -> list:
    if not text:
        return []
    patterns = [
        r"\b(?:i|we)\s+(?:will|can|shall)\s+[^.!?\n]{5,120}",
        r"\bplease\s+(?:review|confirm|send|share|complete|update|approve|submit|respond)[^.!?\n]{0,120}",
        r"\byou\s+need\s+to\s+[^.!?\n]{5,120}",
        r"\bkindly\s+[^.!?\n]{5,120}",
    ]
    phrases = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            phrase = match.group(0).strip(" \t\r\n.,;:-")
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) >= 4:
                return phrases
    return phrases


def _extract_deadline(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    now = datetime.utcnow()
    if "by eod" in lowered or "end of day" in lowered:
        return now.replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
    if "tomorrow" in lowered:
        return (now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
    if "today" in lowered:
        return now.replace(hour=17, minute=0, second=0, microsecond=0).isoformat()

    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if date_match:
        return f"{date_match.group(1)}T17:00:00"

    slash_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", text)
    if slash_match:
        raw = slash_match.group(1)
        for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y"):
            try:
                return datetime.strptime(raw, fmt).replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
            except ValueError:
                continue
    return None


def _fallback_inbox_answer(question: str, emails_context: list) -> str:
    q = (question or "").strip().lower()
    if not emails_context:
        return "Your inbox data is empty right now, so I do not have any emails to answer from yet."

    urgent = [e for e in emails_context if (e.get("classification") or "").lower() == "urgent"]
    tasks = [e for e in emails_context if (e.get("classification") or "").lower() == "task"]
    spam = [e for e in emails_context if (e.get("classification") or "").lower() == "spam"]
    unread = [e for e in emails_context if e.get("is_read") is False]
    sorted_by_priority = sorted(emails_context, key=lambda e: (e.get("priority_score") or 0), reverse=True)

    if "urgent" in q or "priority" in q:
        if urgent:
            top = "; ".join(f"{e.get('sender')}: {e.get('subject')}" for e in urgent[:3])
            return f"You currently have {len(urgent)} urgent emails. The top ones are {top}."
        top = "; ".join(f"{e.get('sender')}: {e.get('subject')}" for e in sorted_by_priority[:3])
        return f"I do not see any urgent emails right now. Your highest-priority emails are {top}."
    if "task" in q or "todo" in q:
        if tasks:
            top = "; ".join(f"{e.get('sender')}: {e.get('subject')}" for e in tasks[:3])
            return f"You currently have {len(tasks)} task-like emails. The main ones are {top}."
        return "I do not see any emails currently classified as tasks in the recent inbox data."
    if "unread" in q:
        if unread:
            top = "; ".join(f"{e.get('sender')}: {e.get('subject')}" for e in unread[:3])
            return f"You currently have {len(unread)} unread emails in the recent inbox sample. Examples include {top}."
        return "I do not see any unread emails in the recent inbox sample."
    if "spam" in q:
        if spam:
            top = "; ".join(f"{e.get('sender')}: {e.get('subject')}" for e in spam[:3])
            return f"I found {len(spam)} emails classified as spam. Examples include {top}."
        return "I do not see any recent emails classified as spam."
    top = "; ".join(f"{e.get('sender')}: {e.get('subject')}" for e in sorted_by_priority[:3])
    return f"I can help with urgent emails, tasks, stress, senders, spam, or inbox summaries. Based on your current inbox, the top emails are {top}."


def _fallback_reply_draft(email_data: dict) -> str:
    sender = email_data.get("sender") or "there"
    subject = email_data.get("subject") or "your email"
    body = " ".join((email_data.get("body") or "").split())
    classification = email_data.get("classification") or "Informational"
    action_items = email_data.get("action_items") or []
    commitments = email_data.get("commitments") or email_data.get("commitment_phrases") or []
    deadline = email_data.get("deadline")

    sender_name = sender.split("<")[0].strip().strip('"') or "there"
    cleaned_subject = " ".join(str(subject).replace("\n", " ").split()).strip(' "\'') or "your email"
    normalized_body = body.lower()
    question_sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[!?])\s+", " ".join((email_data.get("body") or "").split()))
        if sentence.strip().endswith("?")
    ]
    first_question = question_sentences[0][:180] if question_sentences else None
    mention_interview = any(keyword in normalized_body for keyword in ["interview", "shortlisted", "round", "assessment"])
    mention_schedule = any(keyword in normalized_body for keyword in ["meeting", "call", "session", "schedule", "timing", "tomorrow", "today"])
    mention_documents = any(keyword in normalized_body for keyword in ["document", "resume", "cv", "certificate", "attachment", "form"])
    mention_confirmation = any(keyword in normalized_body for keyword in ["confirm", "confirmation", "let me know", "please reply"])
    thread_context = email_data.get("thread_context") or []
    prior_thread = [item for item in thread_context if not item.get("is_target")]
    opener = f"Hi {sender_name},\n\n"

    if classification == "Urgent":
        response = f"Thank you for flagging \"{cleaned_subject}\". I have reviewed your message"
        if deadline:
            response += f" and noted the deadline of {deadline[:10]}"
        if first_question:
            response += f". Regarding your question, \"{first_question}\", I will review it carefully and get back to you with the right update shortly."
        else:
            response += ". I will prioritize this and follow up with the required update shortly."
    elif classification == "Task":
        focus = action_items[0] if action_items else cleaned_subject
        response = f"Thank you for the details about \"{focus}\". I have reviewed the request"
        if deadline:
            response += f" and noted the timeline for {deadline[:10]}"
        if mention_documents:
            response += ". I will check the required documents/details and send the needed update soon."
        elif mention_schedule:
            response += ". I will review the schedule and confirm the appropriate next step shortly."
        else:
            response += ". I will work on the next step and share an update soon."
    elif commitments:
        response = f"Thank you for your message about \"{cleaned_subject}\". I have noted the commitment points you mentioned and will respond with the appropriate follow-up shortly."
    elif mention_interview:
        response = f"Thank you for your email regarding \"{cleaned_subject}\". I have gone through the update about the interview or selection process and will respond after reviewing the details carefully."
    elif prior_thread:
        last_thread_msg = prior_thread[-1]
        prior_sender = last_thread_msg.get("sender") or "you"
        prior_subject = last_thread_msg.get("subject") or cleaned_subject
        response = (
            f"Thank you for the follow-up on \"{prior_subject}\". I have reviewed the latest message in this conversation"
            f" and noted the earlier context from {prior_sender}. I will reply with the right update based on this thread."
        )
    elif body:
        first_sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0][:180].strip()
        response = f"Thank you for your email about \"{cleaned_subject}\". I have reviewed the details"
        if first_sentence:
            response += f", including \"{first_sentence}\""
        if first_question:
            response += f". I have also noted your question, \"{first_question}\", and I will get back to you with a proper response."
        elif mention_confirmation:
            response += ", and I will confirm the appropriate next step shortly."
        else:
            response += ", and I will get back to you with the appropriate response."
    else:
        response = f"Thank you for your email about \"{cleaned_subject}\". I have reviewed it and will follow up with the appropriate next step."

    return opener + response + "\n\nBest regards,"


def _heuristic_analysis(subject: str, body: str, sender: str = "") -> dict:
    text = f"{subject} {body}".lower()
    sender_l = (sender or "").lower()
    urgent_keywords = ["urgent", "asap", "immediately", "right away", "by eod", "deadline", "final notice", "critical", "action required", "respond today"]
    task_keywords = ["please review", "can you", "need you to", "kindly", "submit", "complete", "update", "upload", "approve", "confirm", "schedule", "share", "follow up", "action item", "next steps"]
    spam_keywords = ["unsubscribe", "offer", "discount", "sale", "promo", "winner", "free trial", "cashback", "lottery", "claim reward", "bitcoin giveaway"]
    info_keywords = ["newsletter", "digest", "statement", "invoice", "receipt", "confirmation", "notification", "announcement", "summary", "thank you", "welcome"]

    urgent_score = sum(2 for k in urgent_keywords if k in text)
    task_score = sum(2 for k in task_keywords if k in text)
    spam_score = sum(2 for k in spam_keywords if k in text)
    info_score = sum(1 for k in info_keywords if k in text)

    if "noreply@" in text or "no-reply@" in text:
        info_score += 2
    if "?" in subject:
        task_score += 1
    if "tomorrow" in text or "today" in text or "by " in text:
        urgent_score += 1
    if "meeting" in text and ("invite" in text or "calendar" in text):
        task_score += 2
    if "invoice" in text or "payment" in text:
        info_score += 1

    trusted_domains = ["google.com", "microsoft.com", "linkedin.com", "github.com", "amazon.in", "amazon.com", "noreply@tm.openai.com", "openai.com", "icici", "hdfc", "sbi"]
    promo_domains = ["mailer", "news", "offers", "promo", "marketing", "deal", "coupon"]
    if any(d in sender_l for d in trusted_domains):
        spam_score = max(0, spam_score - 2)
        info_score += 2
    if any(d in sender_l for d in promo_domains):
        spam_score += 2

    if spam_score >= 4 and spam_score >= urgent_score + task_score:
        classification, priority, stress, action = "Spam", 1, 8, "ignore"
    elif urgent_score >= 4:
        classification, priority, stress, action = "Urgent", (8 if urgent_score < 7 else 9), (70 if urgent_score < 7 else 82), "reply"
    elif task_score >= 3:
        classification, priority, stress, action = "Task", (6 if task_score < 6 else 7), (35 if task_score < 6 else 50), "create_task"
    else:
        classification, priority, stress, action = "Informational", (3 if info_score >= 2 else 4), (15 if info_score >= 2 else 22), "mark_read"

    summary = (body or subject or "No content").strip()
    if len(summary) > 180:
        summary = summary[:180].rstrip() + "..."

    deadline = _extract_deadline(f"{subject}\n{body}")
    commitments = _extract_commitment_phrases(body or subject or "")
    mood = "Demanding" if stress >= 70 else "Professional" if stress >= 40 else "Neutral"
    return {
        "classification": classification,
        "priority_score": priority,
        "deadline": deadline,
        "commitment_phrases": commitments,
        "action_items": [subject[:120]] if classification in ["Task", "Urgent"] and subject else [],
        "mood": mood,
        "stress_score": stress,
        "suggested_action": action,
        "ai_summary": summary if summary else "Could not analyze this email automatically.",
        "ai_reply_draft": None,
        "language": "en",
        "reasoning": "Heuristic fallback classification based on urgency/task/spam keywords.",
        "commitment_warning": "This email may be asking for a concrete commitment." if commitments else None,
    }


async def analyze_email(subject: str, body: str, sender: str) -> dict:
    global OPENAI_QUOTA_EXHAUSTED
    if OPENAI_QUOTA_EXHAUSTED or client is None:
        return _heuristic_analysis(subject, body, sender)

    prompt = f"""You are an expert email intelligence system. Analyze the following email and respond ONLY with a valid JSON object.

Email Details:
- From: {sender}
- Subject: {subject}
- Body: {body[:3000]}

Respond with this exact JSON structure:
{{
  "classification": "<Urgent|Task|Informational|Spam>",
  "priority_score": <1-10 integer>,
  "deadline": "<ISO date string or null>",
  "commitment_phrases": ["<phrase1>", "<phrase2>"],
  "action_items": ["<action1>", "<action2>"],
  "mood": "<Stressed|Professional|Friendly|Demanding|Neutral|Anxious|Aggressive>",
  "stress_score": <0-100 integer>,
  "suggested_action": "<reply|create_task|mark_read|defer|ignore>",
  "ai_summary": "<2-3 sentence plain English summary>",
  "language": "<ISO 639-1 language code e.g. en, hi, fr>",
  "reasoning": "<Explain in 2-3 sentences: WHY you chose this classification, priority, and action>",
  "commitment_warning": "<null or a warning if the sender is trying to extract a commitment from you>"
}}"""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are an expert email intelligence system. Always respond with valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        result["ai_reply_draft"] = None
        return result
    except Exception as e:
        if _is_quota_error(e):
            OPENAI_QUOTA_EXHAUSTED = True
        print(f"AI analysis error: {e}")
        return _heuristic_analysis(subject, body, sender)


async def update_sender_profile(sender_email: str, sender_name: str, email_analysis: dict, existing_profile: Optional[dict] = None) -> dict:
    global OPENAI_QUOTA_EXHAUSTED
    if OPENAI_QUOTA_EXHAUSTED or client is None:
        return {
            "communication_style": "Unknown",
            "typical_urgency": "Medium",
            "common_topics": [],
            "reliability_score": 50.0,
            "notes": "Profile generated from fallback analysis.",
        }

    history_context = ""
    if existing_profile:
        history_context = f"""
Previous profile:
- Email count: {existing_profile.get('email_count', 0)}
- Communication style: {existing_profile.get('communication_style', 'Unknown')}
- Typical urgency: {existing_profile.get('typical_urgency', 'Unknown')}
- Common topics: {existing_profile.get('common_topics', [])}
- Notes: {existing_profile.get('notes', '')}
"""
    prompt = f"""Based on this email analysis, build/update an intelligence profile for this sender.

Sender: {sender_name} <{sender_email}>
This email's classification: {email_analysis.get('classification')}
Mood detected: {email_analysis.get('mood')}
Stress score: {email_analysis.get('stress_score')}
Topics/actions: {email_analysis.get('action_items')}
{history_context}

Respond ONLY with this JSON:
{{
  "communication_style": "<e.g. Formal & Direct | Casual & Friendly | Passive-Aggressive | Concise & Efficient>",
  "typical_urgency": "<Low|Medium|High>",
  "common_topics": ["<topic1>", "<topic2>"],
  "reliability_score": <0-100>,
  "notes": "<1-2 sentence insight about this sender's communication patterns>"
}}"""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a communication intelligence analyst. Respond with valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        if _is_quota_error(e):
            OPENAI_QUOTA_EXHAUSTED = True
        print(f"Sender profile error: {e}")
        return {
            "communication_style": "Unknown",
            "typical_urgency": "Medium",
            "common_topics": [],
            "reliability_score": 50.0,
            "notes": "Profile could not be generated.",
        }


async def generate_voice_summary(email_data: dict) -> str:
    global OPENAI_QUOTA_EXHAUSTED
    if OPENAI_QUOTA_EXHAUSTED or client is None:
        return f"You have an email from {email_data.get('sender')} with subject {email_data.get('subject')}. It is classified as {email_data.get('classification')} with a priority of {email_data.get('priority_score')} out of 10."

    prompt = f"""Generate a natural, conversational spoken summary of this email as if you're an AI assistant reading it to someone.

Email:
- From: {email_data.get('sender')}
- Subject: {email_data.get('subject')}
- Classification: {email_data.get('classification')}
- Priority: {email_data.get('priority_score')}/10
- Summary: {email_data.get('ai_summary')}
- Deadline: {email_data.get('deadline') or 'none'}
- Stress Score: {email_data.get('stress_score')}/100
- Mood: {email_data.get('mood')}

Write 3-5 sentences as if speaking to the person. Start with "You have an email from...".
Mention if it's urgent, has a deadline, or has a high stress score. Be conversational and natural.
Keep it under 80 words."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return f"You have an email from {email_data.get('sender')} with subject {email_data.get('subject')}. It is classified as {email_data.get('classification')} with a priority of {email_data.get('priority_score')} out of 10."


async def ask_inbox_question(question: str, emails_context: list) -> str:
    global OPENAI_QUOTA_EXHAUSTED
    if OPENAI_QUOTA_EXHAUSTED or client is None:
        return _fallback_inbox_answer(question, emails_context)

    email_summaries = "\n".join(
        f"- [{e['classification']}] From: {e['sender']} | Subject: {e['subject']} | Priority: {e['priority_score']}/10 | {e.get('ai_summary', '')[:100]}"
        for e in emails_context[:20]
    )
    prompt = f"""You are an AI assistant with access to the user's email inbox. Answer the user's question based on the email data below.

Inbox Summary (recent emails):
{email_summaries}

User Question: {question}

Answer conversationally in 2-4 sentences. Be specific and reference actual emails when relevant."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful email assistant. Answer questions about the user's inbox concisely."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        if _is_quota_error(e):
            OPENAI_QUOTA_EXHAUSTED = True
        return _fallback_inbox_answer(question, emails_context)


async def generate_reply_draft(email_data: dict) -> str:
    global OPENAI_QUOTA_EXHAUSTED
    sender = email_data.get("sender") or "there"
    subject = email_data.get("subject") or "your email"
    summary = email_data.get("ai_summary") or email_data.get("body_snippet") or ""
    body = email_data.get("body") or ""
    classification = email_data.get("classification") or "Informational"
    deadline = email_data.get("deadline") or "none"
    sender_profile = email_data.get("sender_profile") or {}
    commitments = email_data.get("commitments") or email_data.get("commitment_phrases") or []
    action_items = email_data.get("action_items") or []
    prior_reasoning = email_data.get("ai_reasoning") or ""
    thread_context = email_data.get("thread_context") or []
    thread_lines = []
    for item in thread_context[-6:]:
        marker = "TARGET EMAIL" if item.get("is_target") else "THREAD CONTEXT"
        thread_lines.append(
            f"- {marker} | From: {item.get('sender')} | To: {item.get('recipient')} | Subject: {item.get('subject')} | Body: {(item.get('body') or '')[:800]}"
        )
    thread_summary = "\n".join(thread_lines) if thread_lines else "No earlier thread context available."

    prompt = f"""Write a professional email reply draft for the user.

Incoming email:
- From: {sender}
- Subject: {subject}
- Classification: {classification}
- Deadline: {deadline}
- Summary: {summary}
- Body: {body[:2500]}
- Action items: {action_items[:5]}
- Commitment phrases: {commitments[:5]}
- Sender profile style: {sender_profile.get('communication_style', 'Unknown')}
- Sender profile notes: {sender_profile.get('notes', '')}
- Earlier analysis: {prior_reasoning}
- Conversation thread context:
{thread_summary}

Requirements:
- Write only the reply body, no markdown
- Keep it clear, polite, and ready to edit
- Acknowledge the sender's request or information
- If the email asks for action, indicate a reasonable next step
- Do not invent facts or commitments beyond a cautious acknowledgement
- Tailor the wording to this specific email instead of using a generic template
- If the sender sounds formal, keep the reply formal; if friendly, keep it warm but professional
- Mention the concrete topic/request from the email
- If the email asks one or more questions, answer them cautiously and directly based on the message context
- Use the thread context to understand what this email is replying to before drafting
- Treat the line marked TARGET EMAIL as the message that needs a reply right now
- Avoid generic phrases like "I have seen it" or "I will take the needed next step shortly" unless they are truly the best fit
- Make this draft feel materially different from replies to unrelated emails
- End with a professional sign-off placeholder"""
    try:
        if OPENAI_QUOTA_EXHAUSTED or client is None:
            raise RuntimeError("OpenAI quota exhausted")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You write concise professional email replies."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=300,
        )
        content = response.choices[0].message.content
        if content:
            return content.strip()
    except Exception as e:
        if _is_quota_error(e):
            OPENAI_QUOTA_EXHAUSTED = True
    return _fallback_reply_draft(email_data)


def calculate_cognitive_load(active_tasks: int, overdue_tasks: int, pending_commitments: int, unread_urgent: int) -> dict:
    score = int(active_tasks + (overdue_tasks * 2) + (pending_commitments * 1.5) + (unread_urgent * 3))
    threshold = 15
    if score < 5:
        level, color, advice = "Low", "green", "Your cognitive load is low. You're in a great state to handle complex emails."
    elif score < 10:
        level, color, advice = "Moderate", "yellow", "Moderate load. Focus on urgent items first."
    elif score < threshold:
        level, color, advice = "High", "orange", "High cognitive load. Consider deferring non-critical tasks."
    else:
        level, color, advice = "Critical", "red", "Critical overload! Low-priority actions are being suppressed to protect your focus."
    return {
        "score": score,
        "level": level,
        "color": color,
        "threshold": threshold,
        "is_suppressing": score >= threshold,
        "advice": advice,
        "percentage": min(int((score / (threshold * 1.5)) * 100), 100),
    }


def parse_deadline_iso(deadline_value: Optional[str]) -> Optional[datetime]:
    if not deadline_value:
        return None
    try:
        parsed = datetime.fromisoformat(deadline_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
