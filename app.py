import os
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from postgrest import APIError
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def error(message: str, status: int):
    return jsonify({"error": message}), status


def get_event(event_id: str) -> dict[str, Any] | None:
    response = supabase.table("events").select("*").eq("id", event_id).limit(1).execute()
    return response.data[0] if response.data else None


@app.get("/")
def index():
    return jsonify({"message": "Event API is running", "docs": "/api"})


@app.get("/api")
def api_index():
    return jsonify(
        {
            "authentication": "disabled",
            "endpoints": {
                "GET /api/events": "Browse events",
                "GET /api/events/<event_id>": "Get event details",
                "POST /api/registrations": "Create a registration",
                "GET /api/me/registrations?user_id=<uuid>": "List a user's registrations",
                "GET /api/events/<event_id>/registrations?organizer_id=<uuid>": "List event attendees",
            },
        }
    )


@app.get("/api/events")
def list_events():
    query = supabase.table("events").select("*").order("title")
    if status := request.args.get("status"):
        query = query.eq("status", status)
    return jsonify(query.execute().data)


@app.get("/api/events/<event_id>")
def event_details(event_id: str):
    event = get_event(event_id)
    if not event:
        return error("Event not found", 404)

    registrations = supabase.table("registrations").select("id", count="exact").eq("event_id", event_id).execute()
    event["registration_count"] = registrations.count or 0
    event["remaining_capacity"] = max(event["capacity"] - event["registration_count"], 0)
    return jsonify(event)


@app.post("/api/registrations")
def create_registration():
    """Register a user for an event. Authentication is deliberately disabled for now."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Request body must be JSON", 400)

    user_id = data.get("user_id")
    event_id = data.get("event_id")
    if not user_id or not event_id:
        return error("user_id and event_id are required", 400)

    event = get_event(event_id)
    if not event:
        return error("Event not found", 404)

    user = supabase.table("users").select("id").eq("id", user_id).limit(1).execute()
    if not user.data:
        return error("User not found", 404)

    duplicate = supabase.table("registrations").select("id").eq("event_id", event_id).eq("user_id", user_id).limit(1).execute()
    if duplicate.data:
        return error("User is already registered for this event", 409)

    registration_count = supabase.table("registrations").select("id", count="exact").eq("event_id", event_id).execute().count or 0
    if registration_count >= event["capacity"]:
        return error("Event is at capacity", 409)

    created = supabase.table("registrations").insert({"user_id": user_id, "event_id": event_id}).execute()
    return jsonify(created.data[0]), 201


@app.get("/api/me/registrations")
def my_registrations():
    """Temporary replacement for auth: provide the current user's id as a query parameter."""
    user_id = request.args.get("user_id")
    if not user_id:
        return error("user_id query parameter is required while authentication is disabled", 400)

    registrations = supabase.table("registrations").select("id, user_id, event_id, created_at, events(*)").eq("user_id", user_id).order("created_at", desc=True).execute()
    return jsonify(registrations.data)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id: str):
    """List attendees. organizer_id is a temporary stand-in for organizer authentication."""
    organizer_id = request.args.get("organizer_id")
    if not organizer_id:
        return error("organizer_id query parameter is required while authentication is disabled", 400)

    event = get_event(event_id)
    if not event:
        return error("Event not found", 404)
    if event["organizer_id"] != organizer_id:
        return error("Only the event organizer can view attendees", 403)

    registrations = supabase.table("registrations").select("id, user_id, created_at, users(id, email, role)").eq("event_id", event_id).order("created_at").execute()
    return jsonify(registrations.data)


@app.errorhandler(APIError)
def handle_supabase_error(exc: APIError):
    app.logger.exception("Supabase request failed")
    return error("Database request failed", 502)


if __name__ == "__main__":
    # Render provides the port at runtime. Gunicorn is used in production,
    # while this keeps direct local runs working as well.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)