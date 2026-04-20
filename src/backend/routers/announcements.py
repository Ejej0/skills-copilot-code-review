"""
Announcement endpoints for the High School Management System API
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson.errors import InvalidId
from bson.objectid import ObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..database import announcements_collection, teachers_collection

router = APIRouter(
    prefix="/announcements",
    tags=["announcements"]
)


class AnnouncementUpsert(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    expiration_date: str
    start_date: Optional[str] = None


class AnnouncementOut(BaseModel):
    id: str
    message: str
    expiration_date: str
    start_date: Optional[str] = None
    status: str


def parse_iso_datetime(date_value: Optional[str], field_name: str) -> Optional[datetime]:
    if date_value is None:
        return None

    try:
        parsed = datetime.fromisoformat(date_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}. Use ISO 8601 format."
        ) from exc

    # Normalize to UTC and compare consistently.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed


def require_teacher_session(teacher_username: Optional[str]) -> Dict[str, Any]:
    if not teacher_username:
        raise HTTPException(status_code=401, detail="Authentication required")

    teacher = teachers_collection.find_one({"_id": teacher_username})
    if not teacher:
        raise HTTPException(status_code=401, detail="Invalid teacher credentials")

    return teacher


def get_announcement_status(
    now_utc: datetime,
    start_date_utc: Optional[datetime],
    expiration_date_utc: datetime
) -> str:
    if now_utc > expiration_date_utc:
        return "expired"
    if start_date_utc and now_utc < start_date_utc:
        return "scheduled"
    return "active"


def map_announcement_document(doc: Dict[str, Any], now_utc: datetime) -> Dict[str, Any]:
    start_date = doc.get("start_date")
    expiration_date = doc["expiration_date"]

    start_date_utc = parse_iso_datetime(start_date, "start_date") if start_date else None
    expiration_date_utc = parse_iso_datetime(expiration_date, "expiration_date")

    return {
        "id": str(doc["_id"]),
        "message": doc["message"],
        "start_date": start_date,
        "expiration_date": expiration_date,
        "status": get_announcement_status(now_utc, start_date_utc, expiration_date_utc)
    }


@router.get("", response_model=List[AnnouncementOut])
def list_announcements(
    include_inactive: bool = False,
    teacher_username: Optional[str] = Query(None)
) -> List[AnnouncementOut]:
    now_utc = datetime.now(timezone.utc)

    if include_inactive:
        require_teacher_session(teacher_username)

    announcements: List[AnnouncementOut] = []
    for doc in announcements_collection.find({}).sort("expiration_date", 1):
        mapped = map_announcement_document(doc, now_utc)

        if not include_inactive and mapped["status"] != "active":
            continue

        announcements.append(mapped)

    return announcements


@router.post("", response_model=AnnouncementOut)
def create_announcement(
    payload: AnnouncementUpsert,
    teacher_username: Optional[str] = Query(None)
) -> AnnouncementOut:
    require_teacher_session(teacher_username)

    start_date_utc = parse_iso_datetime(payload.start_date, "start_date") if payload.start_date else None
    expiration_date_utc = parse_iso_datetime(payload.expiration_date, "expiration_date")

    if start_date_utc and start_date_utc > expiration_date_utc:
        raise HTTPException(
            status_code=400,
            detail="start_date must be before or equal to expiration_date"
        )

    clean_message = payload.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    insert_result = announcements_collection.insert_one(
        {
            "message": clean_message,
            "start_date": payload.start_date,
            "expiration_date": payload.expiration_date
        }
    )

    created = announcements_collection.find_one({"_id": insert_result.inserted_id})
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create announcement")

    return map_announcement_document(created, datetime.now(timezone.utc))


@router.put("/{announcement_id}", response_model=AnnouncementOut)
def update_announcement(
    announcement_id: str,
    payload: AnnouncementUpsert,
    teacher_username: Optional[str] = Query(None)
) -> AnnouncementOut:
    require_teacher_session(teacher_username)

    try:
        announcement_object_id = ObjectId(announcement_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid announcement id") from exc

    start_date_utc = parse_iso_datetime(payload.start_date, "start_date") if payload.start_date else None
    expiration_date_utc = parse_iso_datetime(payload.expiration_date, "expiration_date")

    if start_date_utc and start_date_utc > expiration_date_utc:
        raise HTTPException(
            status_code=400,
            detail="start_date must be before or equal to expiration_date"
        )

    clean_message = payload.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    update_result = announcements_collection.update_one(
        {"_id": announcement_object_id},
        {
            "$set": {
                "message": clean_message,
                "start_date": payload.start_date,
                "expiration_date": payload.expiration_date
            }
        }
    )

    if update_result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Announcement not found")

    updated = announcements_collection.find_one({"_id": announcement_object_id})
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to load updated announcement")

    return map_announcement_document(updated, datetime.now(timezone.utc))


@router.delete("/{announcement_id}")
def delete_announcement(
    announcement_id: str,
    teacher_username: Optional[str] = Query(None)
) -> Dict[str, str]:
    require_teacher_session(teacher_username)

    try:
        announcement_object_id = ObjectId(announcement_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid announcement id") from exc

    delete_result = announcements_collection.delete_one({"_id": announcement_object_id})
    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Announcement not found")

    return {"message": "Announcement deleted"}
