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
print(f"[Mosaic RAG] Active knowledge base: {KB.source} ({len(KB.resources)} resources)")


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


def save_feedback(session_id: str, useful: bool | None, notes: str) -> dict:
    feedback_id = str(uuid.uuid4())
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        try:
            import psycopg

            with psycopg.connect(database_url, connect_timeout=15) as connection:
                connection.execute(
                    """
                    INSERT INTO public.mosaic_feedback
                        (id, session_id, useful, notes)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (feedback_id, session_id[:100], useful, notes[:2000]),
                )
            return {"saved": True, "feedback_id": feedback_id, "storage": "supabase"}
        except Exception as error:
            print(
                "[Mosaic feedback] Supabase save failed; using SQLite fallback: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
            (
                feedback_id,
                session_id[:100],
                1 if useful is True else 0 if useful is False else None,
                notes[:2000],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return {"saved": True, "feedback_id": feedback_id, "storage": "sqlite"}


def delete_feedback(session_id: str) -> dict:
    database_url = os.getenv("DATABASE_URL", "").strip()
    supabase_count = 0

    if database_url:
        try:
            import psycopg

            with psycopg.connect(database_url, connect_timeout=15) as connection:
                cursor = connection.execute(
                    "DELETE FROM public.mosaic_feedback WHERE session_id = %s",
                    (session_id[:100],),
                )
                supabase_count = cursor.rowcount
        except Exception as error:
            print(
                "[Mosaic feedback] Supabase deletion failed: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )

    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "DELETE FROM feedback WHERE session_id = ?",
            (session_id[:100],),
        )
        sqlite_count = cursor.rowcount

    return {
        "deleted": True,
        "feedback_records": supabase_count + sqlite_count,
        "supabase_records": supabase_count,
        "sqlite_records": sqlite_count,
    }


def parse_ages(profile: dict) -> list[int]:
    value = profile.get("child_age", profile.get("ages", []))
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


def family_metadata(profile: dict) -> dict:
    parent_name = str(profile.get("parent_name", "")).strip() or "Your family"
    child_name = str(profile.get("child_name", "")).strip() or "Your learner"
    ages = parse_ages(profile)
    surname_parts = parent_name.split()
    family_name = f"{surname_parts[-1]} family" if surname_parts else "Your family"
    return {
        "parent_name": parent_name,
        "parent_first_name": parent_name.split()[0],
        "child_name": child_name,
        "child_first_name": child_name.split()[0],
        "child_age": ages[0] if ages else None,
        "family_name": family_name,
    }


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


def name_daily_guidance(pathway: dict, profile: dict) -> None:
    family = family_metadata(profile)
    child_name = family["child_first_name"]
    parent_name = family["parent_first_name"]
    full_names = (
        (family["child_name"], child_name),
        (family["parent_name"], parent_name),
    )
    def use_first_names(value: object) -> str:
        text = str(value or "").strip()
        for full_name, first_name in full_names:
            text = re.sub(
                rf"\b{re.escape(full_name)}\b",
                first_name,
                text,
                flags=re.IGNORECASE,
            )
        return text

    def remove_for_name_prefix(value: str, first_name: str) -> str:
        return re.sub(
            rf"^\s*For\s+{re.escape(first_name)}\s*:\s*",
            "",
            value,
            count=1,
            flags=re.IGNORECASE,
        )

    for week in pathway.get("weeks", []):
        week["theme"] = use_first_names(week.get("theme"))
        week["introduction"] = use_first_names(week.get("introduction"))
        for day in week.get("days", []):
            day["title"] = use_first_names(day.get("title"))
            activity = use_first_names(day.get("child_activity"))
            day["child_activity"] = remove_for_name_prefix(activity, child_name)

            prompt = use_first_names(day.get("parent_prompt"))
            prompt = re.sub(r"\byour\b", f"{parent_name}'s", prompt, flags=re.IGNORECASE)
            prompt = re.sub(r"\byou\b", parent_name, prompt, flags=re.IGNORECASE)
            day["parent_prompt"] = remove_for_name_prefix(prompt, parent_name)


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
    family = family_metadata(profile)
    child = family["child_name"]
    child_first_name = family["child_first_name"]
    age_phrase = f", age {family['child_age']}," if family["child_age"] is not None else ""
    intentions = profile.get("values") or profile.get("add") or "more trust, curiosity, and connection"
    interests = profile.get("interests") or "the interests already showing up"
    current_priority = conversation_priorities[-1][:240] if conversation_priorities else ""
    learning_needs = profile.get("learning_needs") or ""
    conversation_note = f" You also named this immediate focus: {current_priority}" if current_priority else ""
    assistant_note = f" One possibility already surfaced in your conversation: {assistant_insight}" if assistant_insight else ""

    activities = [
        ("Notice the spark", f"{child_first_name} might choose one part of {interests} to explore for twenty minutes.", "What held their attention without prompting?"),
        ("Make the choice visible", f"Offer two simple ways to explore {interests}, and let {child_first_name} choose or suggest another.", "What changed when the choice belonged to them?"),
        ("Follow a question", "Write down one question that comes up and explore it together using a Mosaic resource.", "Did the question grow, shift, or lead somewhere unexpected?"),
        ("Share the lead", f"Ask {child_first_name} to show or explain something they enjoy about {interests}.", "What did you notice when you became the learner?"),
        ("Pause and look back", "Choose one moment from the week that felt energizing and one that felt heavy.", "What might you keep, change, or release next week?"),
        ("Begin with connection", "Start with a short shared activity before offering an independent suggested activity.", "Did connection change how the suggested activity was received?"),
        ("Try it with others", f"Explore a cooperative version of {interests} with a friend, sibling, or community member.", "What supported participation and belonging?"),
        ("Build something useful", "Turn a current interest into a small project that matters to your family or community.", "Where did purpose or pride show up?"),
        ("Leave room to revise", f"{child_first_name} might change one part of the plan or replace it entirely.", "What did their revision reveal about what they need?"),
        ("Name what to carry forward", "Together, choose one practice from these two weeks that feels worth repeating.", "What is the smallest version that could fit naturally next week?"),
    ]
    weeks = []
    for week_number in (1, 2):
        start = (week_number - 1) * 5
        weeks.append({
            "week_number": week_number,
            "theme": "Notice and explore" if week_number == 1 else "Connect and adapt",
            "introduction": (
                "Begin by observing what creates energy, then try small suggested activities without requiring a particular outcome."
                if week_number == 1
                else "Build gently on what you noticed, with more connection, learner choice, and room to revise."
            ),
            "days": [
                {
                    "day": index + 1,
                    "title": activities[index][0],
                    "child_activity": activities[index][1],
                    "parent_prompt": activities[index][2],
                }
                for index in range(start, start + 5)
            ],
        })

    return {
        "family": family,
        "family_welcome": (
            f"Welcome, {family['family_name']}. You are making space for {child}{age_phrase} to learn through "
            f"{interests}, while holding {intentions} as a compass. This plan is a starting point, not a test. "
            "Notice what brings energy, adapt what does not fit, and let your family's real experience guide the next step."
            f"{conversation_note}{assistant_note}"
        ),
        "learner_support": {
            "show": bool(str(learning_needs).strip()),
            "heading": f"Made with {child} in mind",
            "message": f"You shared that {learning_needs}. You might treat this as useful context for pacing, communication, and choice—not as a limit on what is possible.",
        },
        "guide_preparation": (
            f"You might bring your observations about {interests}, the family's wish to leave behind "
            f"{profile.get('leave_behind') or 'unhelpful pressure'}, and what support would make the next experiment feel sustainable."
        ),
        "weeks": weeks,
        "resources": [
            {
                "resource_id": resource.id,
                "why_it_fits": resource.parent_need or resource.key_takeaway,
                "section": "watch_explore" if index == 0 else "reading_corner",
            }
            for index, resource in enumerate(resources[:3])
        ],
        "community": {
            "resource_id": community.id,
            "why_it_fits": "A low-pressure place to listen, connect, and hear how other families are navigating their paths.",
        },
        "when_it_wobbles": [
            {
                "moment": "A suggested activity is met with a no",
                "response": "You might pause without persuading, then return later with a smaller choice or ask what would make it feel more inviting.",
            },
            {
                "moment": "The plan starts to feel like another schedule",
                "response": "Consider keeping only the one practice that creates connection and letting the rest wait. The plan is meant to serve your family.",
            },
        ],
        "what_comes_next": (
            "At the end of two weeks, notice what your learner asked to repeat, what supported connection, and what felt heavy. "
            "Bring those observations to Mosaic as the starting point for your next pathway."
        ),
    }


def enrich_and_validate_pathway(
    pathway: dict,
    retrieved_resources: list[Resource],
    community_candidates: list[Resource],
) -> dict:
    weeks = pathway.get("weeks", [])
    if len(weeks) != 2 or any(len(week.get("days", [])) != 5 for week in weeks):
        raise ValueError("Claude did not return exactly two weeks with five days each")
    if len(pathway.get("when_it_wobbles", [])) != 2:
        raise ValueError("Claude did not return exactly two when-it-wobbles entries")
    for week_index, week in enumerate(weeks):
        week["week_number"] = week_index + 1
        for day_index, day in enumerate(week["days"]):
            day["day"] = week_index * 5 + day_index + 1

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
                "section": item.get("section", "watch_explore"),
            })
            seen.add(resource_id)
    for resource in retrieved_resources:
        if len(selections) >= 2:
            break
        if resource.id not in seen and not resource.is_community:
            selections.append({
                **resource.public_dict(),
                "why_it_fits": resource.parent_need,
                "section": "watch_explore" if not selections else "reading_corner",
            })
            seen.add(resource.id)
    pathway["resources"] = selections[:3]
    sections = {item["section"] for item in pathway["resources"]}
    if pathway["resources"] and "watch_explore" not in sections:
        pathway["resources"][0]["section"] = "watch_explore"
    if len(pathway["resources"]) > 1 and "reading_corner" not in sections:
        pathway["resources"][1]["section"] = "reading_corner"

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
    resources = KB.search(query, ages=ages, limit=9, community=False)
    communities = KB.search(query, ages=ages, limit=3, community=True)
    context = KB.context([*resources, *communities])

    mode = "claude"
    warning = None
    try:
        raw = CLAUDE.create_message(
            system=pathway_system(context),
            user=pathway_user(profile, history),
            max_tokens=3200,
            json_schema=PATHWAY_SCHEMA,
        )
        pathway = json.loads(raw)
        pathway = enrich_and_validate_pathway(pathway, resources, communities)
        name_daily_guidance(pathway, profile)
    except (ClaudeError, json.JSONDecodeError, ValueError) as error:
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
        name_daily_guidance(pathway, profile)
    pathway["family"] = family_metadata(profile)
    pathway["citation_sources"] = [
        resource.public_dict()
        for resource in [*resources, *communities]
    ]
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
                "knowledge_base_source": KB.source,
                "feedback_storage": "supabase" if os.getenv("DATABASE_URL", "").strip() else "sqlite",
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
                useful = payload.get("useful")
                self._send_json(save_feedback(
                    session_id=str(payload.get("session_id", "")),
                    useful=useful if isinstance(useful, bool) else None,
                    notes=str(payload.get("notes", "")),
                ))
            elif path == "/api/delete":
                self._send_json(delete_feedback(str(payload.get("session_id", ""))))
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
    print(
        f"Mosaic prototype running at http://{host}:{port} "
        f"({mode}, {len(KB.resources)} resources from {KB.source})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
