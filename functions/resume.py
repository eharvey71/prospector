"""Resume ingestion: uploaded PDF -> LLM-extracted profile suggestion.

Triggered by a Storage finalize event on users/{uid}/resume.pdf. Extraction
never overwrites the live profile directly — human review stays the gate,
same as every other AI-generated artifact in this system. The result lands
in users/{uid}/resume_extraction/latest; the profile UI surfaces it and lets
the user apply fields into the editable form before saving.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from google.cloud import firestore, storage
from pydantic import BaseModel, Field
from pypdf import PdfReader

from llm import generate_structured
from schemas import WorkHistoryItem

log = logging.getLogger("resume")

EXTRACT_SYSTEM = """You extract structured facts from a resume's raw text. \
Only include what's explicitly stated — never infer skills, dates, or \
titles that aren't written. Leave a field empty/null rather than guess."""


class ResumeExtraction(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    work_auth: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    work_history: list[WorkHistoryItem] = Field(default_factory=list)


def uid_from_resume_path(object_name: str) -> Optional[str]:
    """"users/{uid}/resume.pdf" -> uid; anything else -> None."""
    parts = object_name.split("/")
    if len(parts) == 3 and parts[0] == "users" and parts[2] == "resume.pdf":
        return parts[1]
    return None


def process_resume_upload(db: firestore.Client, bucket_name: str, object_name: str) -> None:
    uid = uid_from_resume_path(object_name)
    if uid is None:
        return

    blob = storage.Client().bucket(bucket_name).blob(object_name)
    pdf_bytes = blob.download_as_bytes()
    text = _extract_text(pdf_bytes)
    if not text.strip():
        log.warning("resume upload uid=%s produced no extractable text", uid)
        return

    extraction = generate_structured(
        f"Resume text:\n\n{text[:12000]}",
        ResumeExtraction,
        system=EXTRACT_SYSTEM,
        max_tokens=2000,
    )

    doc = extraction.model_dump(mode="json")
    doc["extracted_at"] = datetime.now(timezone.utc)
    (
        db.collection("users").document(uid)
        .collection("resume_extraction").document("latest")
        .set(doc)
    )
    log.info(
        "resume extraction ready uid=%s (%d skills, %d roles)",
        uid, len(extraction.skills), len(extraction.work_history),
    )


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
