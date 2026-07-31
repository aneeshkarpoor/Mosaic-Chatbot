from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.claude_client import ClaudeClient, ClaudeError
from app.prompts import (
    PATHWAY_SCHEMA,
    chat_system,
    chat_user,
    pathway_system,
    pathway_user,
)
from app.rag import KnowledgeBase, Resource


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "prototype.db"
CITATION_RE = re.compile(r"\[(R\d{3})\]")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(BASE_DIR / ".env")
KB = KnowledgeBase(Path(os.getenv("MOSAIC_KB_PATH", DATA_DIR / "mosaic_resources.csv")))
CLAUDE = ClaudeClient()


def init_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                useful INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def parse_ages(profile: dict) -> list[int]:
    value = profile.get("ages", [])
    if isinstance(value, list):
        candidates = value
    else:
        candidates = re.findall(r"\d+", str(value))
    ages: list[int] = []
    for candidate in candidates:
        try:
            age = int(candidate)
        except (TypeError, ValueError):
            continue
        if 0 <= age <= 30:
            ages.append(age)
    return ages


def profile_query(profile: dict, message: str = "") -> str:
    fields = [
        message,
        profile.get("learning_needs", ""),
        profile.get("interests", ""),
        profile.get("leave_behind", ""),
        profile.get("preserve", ""),
        profile.get("add", ""),
        profile.get("values", ""),
    ]
    return " ".join(str(value) for value in fields if value)


def family_conversation_priorities(history: list[dict]) -> list[str]:
    return [
        str(item.get("content", "")).strip()[:1000]
        for item in history[-8:]
        if item.get("role") == "user" and str(item.get("content", "")).strip()
    ]


def latest_assistant_insight(history: list[dict]) -> str:
    responses = [
        str(item.get("content", "")).strip()
        for item in history[-8:]
        if item.get("role") == "assistant" and str(item.get("content", "")).strip()
    ]
    if not responses:
        return ""
    response = CITATION_RE.sub("", responses[-1]).split("\n\n", 1)[0].strip()
    if len(response) <= 300:
        return response
    return f"{response[:300].rsplit(' ', 1)[0].rstrip(' ,;:-')}…"


def clean_citations(text: str, allowed_ids: set[str]) -> str:
    return CITATION_RE.sub(lambda match: match.group(0) if match.group(1) in allowed_ids else "", text)


def demo_chat(resources: list[Resource]) -> str:
    first = resources[0]
    second = resources[1] if len(resources) > 1 else resources[0]
    return (
        "One possibility is to begin by noticing what already holds your learner's attention, "
        "then make one small change that creates more room for it. Mosaic's library emphasizes "
        f"{first.key_takeaway[:220].rstrip('.')} [{first.id}]. You might also consider the "
        f"perspective in “{second.title}” as a gentle conversation starter [{second.id}].\n\n"
        "This is local demo wording because the Claude request was unavailable."
    )


def demo_pathway(
    profile: dict,
    resources: list[Resource],
    community: Resource,
    conversation_priorities: list[str],
    assistant_insight: str,
) -> dict:
    ages = parse_ages(profile)
    age_phrase = f"for ages {', '.join(str(age) for age in ages)}" if ages else "for your family"
    intentions = profile.get("values") or profile.get("add") or "more trust, curiosity, and connection"
    interests = profile.get("interests") or "the interests already showing up"
    current_priority = conversation_priorities[-1][:240] if conversation_priorities else ""
    priority_reflection = (
        f" In your conversation, you identified this immediate focus: “{current_priority}”."
        if current_priority
        else ""
    )
    response_reflection = (
        f" The conversation also surfaced this possibility: “{assistant_insight}”."
        if assistant_insight
        else ""
    )
    first_practice = (
        f"Choose one small, low-pressure experiment connected to this priority: {current_priority}"
        if current_priority
        else "Notice and jot down one moment of sustained interest each day, without redirecting it."
    )
    return {
        "title": "A gentle two-week starting pathway",
        "reflection": (
            f"You are looking for a learning rhythm {age_phrase} that makes more room for {interests}. "
            f"The intentions you named—{intentions}—can serve as a compass while you experiment."
            f"{priority_reflection}{response_reflection}"
        ),
        "rhythm": [
            {
                "when": "Days 1–3",
                "practice": first_practice,
                "why_it_fits": "This begins with the priority you named and keeps the first step manageable.",
            },
            {
                "when": "Days 4–7",
                "practice": "Offer one low-pressure invitation connected to a noticed interest and let participation be optional.",
                "why_it_fits": "An invitation creates possibility while preserving learner agency.",
            },
            {
                "when": "Week 2",
                "practice": "Hold a ten-minute family check-in: what felt energizing, heavy, or worth trying again?",
                "why_it_fits": "A short reflection helps the rhythm adapt to the family's real experience.",
            },
        ],
        "resources": [
            {"resource_id": resource.id, "why_it_fits": resource.parent_need}
            for resource in resources[:3]
        ],
        "community": {
            "resource_id": community.id,
            "why_it_fits": "A low-pressure place to listen, connect, and hear how other families are navigating their paths.",
        },
        "closing_note": "add closing message later",
    }


def enrich_and_validate_pathway(
    pathway: dict,
    retrieved_resources: list[Resource],
    community_candidates: list[Resource],
) -> dict:
    allowed = {resource.id: resource for resource in retrieved_resources}
    allowed_community = {resource.id: resource for resource in community_candidates}

    selections: list[dict] = []
    seen: set[str] = set()
    for item in pathway.get("resources", []):
        resource_id = item.get("resource_id")
        if resource_id in allowed and resource_id not in seen and not allowed[resource_id].is_community:
            selections.append({
                **allowed[resource_id].public_dict(),
                "why_it_fits": item.get("why_it_fits", ""),
            })
            seen.add(resource_id)
    for resource in retrieved_resources:
        if len(selections) >= 2:
            break
        if resource.id not in seen and not resource.is_community:
            selections.append({**resource.public_dict(), "why_it_fits": resource.parent_need})
            seen.add(resource.id)
    pathway["resources"] = selections[:3]

    requested_community = pathway.get("community", {})
    community_id = requested_community.get("resource_id")
    community = allowed_community.get(community_id) or community_candidates[0]
    pathway["community"] = {
        **community.public_dict(),
        "why_it_fits": requested_community.get("why_it_fits") or community.parent_need,
    }
    return pathway


def generate_chat(payload: dict) -> dict:
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("message is required")
    profile = payload.get("profile") or {}
    history = payload.get("history") or []
    ages = parse_ages(profile)
    resources = KB.search(profile_query(profile, message), ages=ages, limit=6)
    allowed_ids = {resource.id for resource in resources}

    mode = "claude"
    warning = None
    try:
        response = CLAUDE.create_message(
            system=chat_system(KB.context(resources)),
            user=chat_user(message, profile, history),
            max_tokens=500,
        )
        response = clean_citations(response, allowed_ids)
    except ClaudeError as error:
        if os.getenv("ALLOW_DEMO_FALLBACK", "true").lower() != "true":
            raise
        print(f"Claude chat fallback: {error}", file=sys.stderr)
        mode = "demo"
        warning = str(error)
        response = demo_chat(resources)

    return {
        "message": response,
        "mode": mode,
        "warning": warning,
        "sources": [resource.public_dict() for resource in resources],
    }


def generate_pathway(payload: dict) -> dict:
    profile = payload.get("profile") or {}
    history = payload.get("history") or []
    conversation_priorities = family_conversation_priorities(history)
    assistant_insight = latest_assistant_insight(history)
    query = profile_query(profile, " ".join(conversation_priorities))
    ages = parse_ages(profile)
    resources = KB.search(query, ages=ages, limit=7, community=False)
    communities = KB.search(query, ages=ages, limit=3, community=True)
    context = KB.context([*resources, *communities])

    mode = "claude"
    warning = None
    try:
        raw = CLAUDE.create_message(
            system=pathway_system(context),
            user=pathway_user(profile, history),
            max_tokens=1800,
            json_schema=PATHWAY_SCHEMA,
        )
        pathway = json.loads(raw)
    except (ClaudeError, json.JSONDecodeError) as error:
        if os.getenv("ALLOW_DEMO_FALLBACK", "true").lower() != "true":
            raise
        print(f"Claude pathway fallback: {error}", file=sys.stderr)
        mode = "demo"
        warning = str(error)
        pathway = demo_pathway(
            profile,
            resources,
            communities[0],
            conversation_priorities,
            assistant_insight,
        )

    pathway = enrich_and_validate_pathway(pathway, resources, communities)
    return {"pathway": pathway, "mode": mode, "warning": warning}


class MosaicHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        allowed_origin = os.getenv("ALLOWED_ORIGIN", "")
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("request body must be between 1 byte and 1 MB")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_static(self, request_path: str) -> None:
        routes = {
            "/": STATIC_DIR / "index.html",
            "/app.js": STATIC_DIR / "app.js",
            "/styles.css": STATIC_DIR / "styles.css",
            "/pathway-garden.png": STATIC_DIR / "pathway-garden.png",
            "/pathway-together.png": STATIC_DIR / "pathway-together.png",
            "/pathway-maker.png": STATIC_DIR / "pathway-maker.png",
        }
        file_path = routes.get(request_path)
        if not file_path or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({
                "status": "ok",
                "mode": "claude" if CLAUDE.configured else "demo",
                "model": CLAUDE.model if CLAUDE.configured else None,
                "resource_count": len(KB.resources),
                "persistence": "feedback only; chat and intake are not stored server-side",
            })
            return
        self._serve_static(path)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        allowed_origin = os.getenv("ALLOWED_ORIGIN", "")
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/chat":
                self._send_json(generate_chat(payload))
            elif path == "/api/pathway":
                self._send_json(generate_pathway(payload))
            elif path == "/api/feedback":
                feedback_id = str(uuid.uuid4())
                with sqlite3.connect(DB_PATH) as connection:
                    connection.execute(
                        "INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
                        (
                            feedback_id,
                            str(payload.get("session_id", ""))[:100],
                            1 if payload.get("useful") is True else 0 if payload.get("useful") is False else None,
                            str(payload.get("notes", ""))[:2000],
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                self._send_json({"saved": True, "feedback_id": feedback_id})
            elif path == "/api/delete":
                session_id = str(payload.get("session_id", ""))[:100]
                with sqlite3.connect(DB_PATH) as connection:
                    cursor = connection.execute("DELETE FROM feedback WHERE session_id = ?", (session_id,))
                self._send_json({"deleted": True, "feedback_records": cursor.rowcount})
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # Prototype boundary: return a safe message, log detail locally.
            print(f"Request failed: {type(error).__name__}: {error}", file=sys.stderr)
            self._send_json({"error": "The prototype could not complete that request."}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    init_database()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), MosaicHandler)
    mode = "Claude API" if CLAUDE.configured else "local demo fallback"
    print(f"Mosaic prototype running at http://{host}:{port} ({mode}, {len(KB.resources)} resources)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
