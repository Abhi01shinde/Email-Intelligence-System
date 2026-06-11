import os
import json
import base64
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, List, Callable
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv()
# Google may return equivalent/expanded scopes; avoid hard failure on benign scope variance.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels"
]

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TOKEN_FILE = str(DATA_DIR / "token.json")
TOKENS_FILE = str(DATA_DIR / "tokens.json")
ACCOUNT_STATE_FILE = str(DATA_DIR / "account_state.json")
DATA_DIR.mkdir(exist_ok=True)


def _normalize_account_email(account_email: Optional[str]) -> Optional[str]:
    normalized = (account_email or "").strip().lower()
    return normalized or None


def _load_token_store() -> dict:
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"Error loading token store: {e}")
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            return {"_legacy_default": legacy}
        except Exception as e:
            print(f"Error loading legacy token: {e}")
    return {}


def _active_account_from_state() -> Optional[str]:
    if not os.path.exists(ACCOUNT_STATE_FILE):
        return None
    try:
        with open(ACCOUNT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_account_email(data.get("email"))
    except Exception:
        return None


def _save_token_store(store: dict):
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f)


def list_known_accounts() -> list[str]:
    return sorted([key for key in _load_token_store().keys() if key and not key.startswith("_")])


def get_flow():
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    )
    return flow


def save_token(credentials: Credentials, account_email: Optional[str] = None):
    account_key = _normalize_account_email(account_email) or "_legacy_default"
    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }
    store = _load_token_store()
    store[account_key] = token_data
    _save_token_store(store)
    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
        except Exception:
            pass


def load_credentials(account_email: Optional[str] = None) -> Optional[Credentials]:
    store = _load_token_store()
    account_key = _normalize_account_email(account_email) or _active_account_from_state()
    if account_key:
        token_data = store.get(account_key)
    else:
        token_data = store.get("_legacy_default")
        if not token_data:
            known_accounts = list_known_accounts()
            token_data = store.get(known_accounts[0]) if len(known_accounts) == 1 else None
    if not token_data:
        return None
    try:
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id") or os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=token_data.get("client_secret") or os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=token_data.get("scopes")
        )
        return creds
    except Exception as e:
        print(f"Error loading credentials: {e}")
        return None


def is_authenticated(account_email: Optional[str] = None) -> bool:
    creds = load_credentials(account_email)
    return creds is not None


def get_gmail_service(account_email: Optional[str] = None, credentials: Optional[Credentials] = None):
    creds = credentials or load_credentials(account_email)
    if not creds:
        raise Exception("Not authenticated. Please connect Gmail first.")
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        if account_email or credentials is None:
            save_token(creds, account_email)
    return build("gmail", "v1", credentials=creds)


def decode_body(payload) -> str:
    """Decode email body from Gmail API payload."""
    body = ""
    if "body" in payload and payload["body"].get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
    elif "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body += base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
            elif part.get("mimeType") == "text/html" and not body and part.get("body", {}).get("data"):
                html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                body = re.sub(r'<[^>]+>', '', html)
            elif "parts" in part:
                for subpart in part["parts"]:
                    if subpart.get("mimeType") == "text/plain" and subpart.get("body", {}).get("data"):
                        body += base64.urlsafe_b64decode(subpart["body"]["data"]).decode("utf-8", errors="ignore")
    return body.strip()


def parse_email_address(header_value: str) -> tuple:
    """Extract name and email from header like 'John Doe <john@example.com>'"""
    match = re.match(r'^(.*?)\s*<([^>]+)>$', header_value.strip())
    if match:
        return match.group(1).strip().strip('"'), match.group(2).strip()
    return header_value.strip(), header_value.strip()


def _received_sort_key(email: dict) -> float:
    received_at = email.get("received_at")
    if not received_at:
        return 0.0
    try:
        return received_at.timestamp()
    except Exception:
        return 0.0


def _gmail_after_query_value(after_datetime: Optional[datetime]) -> Optional[str]:
    if not after_datetime:
        return None

    normalized = after_datetime
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return str(int(normalized.timestamp()))


def fetch_emails(
    account_email: Optional[str] = None,
    max_results: int = 20,
    label: Optional[str] = None,
    unread_only: bool = False,
    received_only: bool = True,
    include_body: bool = False,
    newer_than_months: Optional[int] = None,
    after_datetime: Optional[datetime] = None,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    item_callback: Optional[Callable[[dict], None]] = None
) -> List[dict]:
    """Fetch emails from Gmail."""
    service = get_gmail_service(account_email=account_email)
    query_parts = []
    if unread_only:
        query_parts.append("is:unread")
    if received_only:
        query_parts.append("-from:me")
    if newer_than_months and newer_than_months > 0:
        query_parts.append(f"newer_than:{newer_than_months}m")
    after_value = _gmail_after_query_value(after_datetime)
    if after_value:
        query_parts.append(f"after:{after_value}")
    query = " ".join(query_parts)

    try:
        page_token = None
        fetch_all = max_results <= 0
        remaining = max_results
        listed_count = 0
        fetched_count = 0
        emails = []
        msg_format = "full" if include_body else "metadata"

        while fetch_all or remaining > 0:
            batch_size = 500 if fetch_all else min(500, remaining)
            req_kwargs = {
                "userId": "me",
                "maxResults": batch_size,
                "q": query,
                "pageToken": page_token
            }
            if label:
                req_kwargs["labelIds"] = [label]

            request = service.users().messages().list(**req_kwargs)
            results = request.execute()
            page_messages = results.get("messages", [])
            listed_count += len(page_messages)
            if progress_callback:
                progress_callback("listed", listed_count)
            if not fetch_all:
                remaining -= len(page_messages)
            page_token = results.get("nextPageToken")
            for msg_ref in page_messages:
                try:
                    get_kwargs = {
                        "userId": "me",
                        "id": msg_ref["id"],
                        "format": msg_format
                    }
                    if not include_body:
                        get_kwargs["metadataHeaders"] = ["From", "To", "Subject", "Date"]

                    msg = service.users().messages().get(**get_kwargs).execute()

                    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
                    sender_raw = headers.get("from", "Unknown")
                    sender_name, sender_email = parse_email_address(sender_raw)
                    body = decode_body(msg["payload"]) if include_body else ""
                    snippet = msg.get("snippet", "")

                    date_str = headers.get("date", "")
                    received_at = None
                    try:
                        from email.utils import parsedate_to_datetime
                        received_at = parsedate_to_datetime(date_str)
                    except Exception:
                        pass
                    if not received_at:
                        try:
                            received_at = datetime.fromtimestamp(
                                int(msg.get("internalDate", "0")) / 1000,
                                tz=timezone.utc
                            )
                        except Exception:
                            received_at = None

                    email_data = {
                        "gmail_id": msg["id"],
                        "sender": sender_name or sender_email,
                        "sender_email": sender_email,
                        "recipient": headers.get("to", ""),
                        "subject": headers.get("subject", "(No Subject)"),
                        "body": body,
                        "body_snippet": snippet[:200],
                        "received_at": received_at,
                        "label_ids": msg.get("labelIds", [])
                    }
                    if item_callback:
                        item_callback(email_data)
                    else:
                        emails.append(email_data)
                    fetched_count += 1
                    if progress_callback:
                        progress_callback("fetched", fetched_count)
                except Exception as e:
                    print(f"Error fetching message {msg_ref['id']}: {e}")
                    continue

            if not page_messages or not page_token:
                break

        emails.sort(key=_received_sort_key, reverse=True)
        return emails
    except HttpError as e:
        print(f"Gmail API error: {e}")
        raise Exception(f"Gmail API error: {str(e)}")


def fetch_email_detail(message_id: str, account_email: Optional[str] = None) -> dict:
    """Fetch a single email with full body/content by Gmail message id."""
    service = get_gmail_service(account_email=account_email)
    try:
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        sender_raw = headers.get("from", "Unknown")
        sender_name, sender_email = parse_email_address(sender_raw)
        body = decode_body(msg["payload"])
        snippet = msg.get("snippet", "")

        date_str = headers.get("date", "")
        received_at = None
        try:
            from email.utils import parsedate_to_datetime
            received_at = parsedate_to_datetime(date_str)
        except Exception:
            pass

        return {
            "gmail_id": msg["id"],
            "thread_id": msg.get("threadId"),
            "sender": sender_name or sender_email,
            "sender_email": sender_email,
            "recipient": headers.get("to", ""),
            "subject": headers.get("subject", "(No Subject)"),
            "body": body,
            "body_snippet": snippet[:200],
            "received_at": received_at,
            "label_ids": msg.get("labelIds", [])
        }
    except Exception as e:
        raise Exception(f"Failed to fetch email detail for {message_id}: {str(e)}")


def fetch_thread_context(
    message_id: str,
    account_email: Optional[str] = None,
    max_messages: int = 6,
    max_body_chars: int = 800,
) -> list[dict]:
    """Fetch recent messages from the same Gmail thread for reply context."""
    service = get_gmail_service(account_email=account_email)
    try:
        original = service.users().messages().get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject"]
        ).execute()
        thread_id = original.get("threadId")
        if not thread_id:
            return []

        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        messages = thread.get("messages", [])
        context = []
        for msg in messages[-max_messages:]:
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender_raw = headers.get("from", "Unknown")
            sender_name, sender_email = parse_email_address(sender_raw)
            body = decode_body(msg.get("payload", {}))
            snippet = msg.get("snippet", "")
            body_text = (body or snippet or "").strip()
            if len(body_text) > max_body_chars:
                body_text = body_text[:max_body_chars].rstrip() + "..."

            context.append({
                "gmail_id": msg.get("id"),
                "thread_id": thread_id,
                "sender": sender_name or sender_email,
                "sender_email": sender_email,
                "recipient": headers.get("to", ""),
                "subject": headers.get("subject", "(No Subject)"),
                "body": body_text,
                "is_target": msg.get("id") == message_id,
            })
        return context
    except Exception as e:
        print(f"Thread context fetch error for {message_id}: {e}")
        return []


def send_reply(original_message_id: str, to: str, subject: str, body: str, account_email: Optional[str] = None) -> bool:
    """Send a reply to an email."""
    service = get_gmail_service(account_email=account_email)
    recipient_name, recipient_email = parse_email_address(to or "")
    normalized_recipient = recipient_email or to
    normalized_subject = subject or ""
    if normalized_subject and not normalized_subject.lower().startswith("re:"):
        normalized_subject = f"Re: {normalized_subject}"

    msg = EmailMessage()
    msg["To"] = normalized_recipient
    msg.set_content(body)

    try:
        original = service.users().messages().get(
            userId="me",
            id=original_message_id,
            format="metadata",
            metadataHeaders=["Message-ID", "References", "Subject"]
        ).execute()

        thread_id = original.get("threadId")
        headers = {h["name"].lower(): h["value"] for h in original.get("payload", {}).get("headers", [])}
        message_id_header = headers.get("message-id")
        references = headers.get("references", "")
        normalized_subject = normalized_subject or headers.get("subject", "")
        if not normalized_subject.lower().startswith("re:"):
            normalized_subject = f"Re: {normalized_subject}"

        msg["Subject"] = normalized_subject
        if message_id_header:
            msg["In-Reply-To"] = message_id_header
            msg["References"] = f"{references} {message_id_header}".strip() if references else message_id_header

        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message = {"raw": encoded}
        if thread_id:
            message["threadId"] = thread_id

        service.users().messages().send(userId="me", body=message).execute()
        return True
    except HttpError as e:
        try:
            if getattr(e, "resp", None) is not None and getattr(e.resp, "status", None) == 404:
                if "Subject" not in msg:
                    msg["Subject"] = normalized_subject or "Re: your email"
                fallback_encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                service.users().messages().send(userId="me", body={"raw": fallback_encoded}).execute()
                return True
        except Exception:
            pass
        try:
            error_text = e.error_details if getattr(e, "error_details", None) else e.content.decode("utf-8", errors="ignore")
        except Exception:
            error_text = str(e)
        raise Exception(f"Gmail API error while sending reply: {error_text or str(e)}")
    except Exception as e:
        raise Exception(f"Send reply error: {str(e)}")


def apply_label(message_id: str, label_name: str, account_email: Optional[str] = None) -> bool:
    """Apply a Gmail label to an email."""
    service = get_gmail_service(account_email=account_email)
    try:
        # Get or create label
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        label_id = next((l["id"] for l in labels if l["name"] == label_name), None)

        if not label_id:
            new_label = service.users().labels().create(
                userId="me",
                body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
            ).execute()
            label_id = new_label["id"]

        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]}
        ).execute()
        return True
    except Exception as e:
        print(f"Apply label error: {e}")
        return False


def mark_as_read(message_id: str, account_email: Optional[str] = None) -> bool:
    """Mark an email as read."""
    service = get_gmail_service(account_email=account_email)
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except Exception as e:
        print(f"Mark read error: {e}")
        return False


def get_user_profile(account_email: Optional[str] = None, credentials: Optional[Credentials] = None) -> dict:
    """Get authenticated user's Gmail profile."""
    service = get_gmail_service(account_email=account_email, credentials=credentials)
    try:
        profile = service.users().getProfile(userId="me").execute()
        return {
            "email": profile.get("emailAddress", ""),
            "messages_total": profile.get("messagesTotal", 0),
            "threads_total": profile.get("threadsTotal", 0)
        }
    except Exception as e:
        return {"email": "unknown@gmail.com", "messages_total": 0, "threads_total": 0}


def remove_token(account_email: Optional[str] = None):
    store = _load_token_store()
    account_key = _normalize_account_email(account_email)
    changed = False
    if account_key:
        changed = store.pop(account_key, None) is not None
    else:
        if "_legacy_default" in store:
            changed = store.pop("_legacy_default", None) is not None
        elif len(list_known_accounts()) == 1:
            only_account = list_known_accounts()[0]
            changed = store.pop(only_account, None) is not None
    if changed:
        _save_token_store(store)
    if not store and os.path.exists(TOKENS_FILE):
        try:
            os.remove(TOKENS_FILE)
        except Exception:
            pass
