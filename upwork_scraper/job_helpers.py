"""Helpers for identifying and parsing scraped Upwork jobs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import unquote


def extract_job_id_from_url(url: str | None) -> str | None:
    """Extract the Upwork job cipher from a job URL."""

    if not url:
        return None
    match = re.search(r"_~([a-f0-9]+)", url)
    return match.group(1) if match else None


def extract_title_from_url(url: str | None) -> str | None:
    """Extract and clean the job title slug from a job URL."""

    if not url:
        return None
    try:
        path = url.split("/jobs/")[-1]
        slug = path.split("_~")[0]
        return unquote(slug.replace("-", " "))
    except Exception:
        return None


def _word_set(text: str) -> set[str]:
    """Split text into lowercase words, ignoring punctuation and hyphens."""

    return set(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def generate_job_id(job_title: str, job_url: str | None = None, job_description: str = "") -> str:
    """Generate a stable job ID, preferring Upwork's URL cipher."""

    cipher = extract_job_id_from_url(job_url)
    if cipher:
        return cipher
    content = f"{job_title.lower()}|{job_description[:100].lower()}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def calculate_posted_datetime(timestamp: str) -> datetime:
    """Convert Upwork's relative timestamp into a local datetime."""

    now = datetime.now()
    t_lower = timestamp.lower()
    try:
        if "just now" in t_lower:
            return now
        if "yesterday" in t_lower:
            return now - timedelta(days=1)
        if "hour" in t_lower:
            return now - timedelta(hours=int(re.findall(r"\d+", timestamp)[0]))
        if "day" in t_lower:
            return now - timedelta(days=int(re.findall(r"\d+", timestamp)[0]))
        if "last week" in t_lower:
            return now - timedelta(weeks=1)
        if "week" in t_lower:
            return now - timedelta(weeks=int(re.findall(r"\d+", timestamp)[0]))
        if "minute" in t_lower:
            return now - timedelta(minutes=int(re.findall(r"\d+", timestamp)[0]))
    except (IndexError, ValueError):
        pass
    return now


def clean_job_proposals(job_proposals_text: str | None) -> str:
    """Normalize the proposals text shown on a job card."""

    if not job_proposals_text:
        return ""
    # The label is followed by a space in one layout and by nothing in another.
    text = _PROPOSALS_LABEL.sub("", job_proposals_text)
    if "freelancers" in text:
        proposals = text.split(" Nu")[0]
    elif " ago" in text:
        proposals = ""
    else:
        proposals = text.replace("Load More Jobs", "").replace("Featured", "")
    return proposals.strip()


_PROPOSALS_LABEL = re.compile(r"Proposals:[ \t]*")
_PROPOSALS_BUCKET = re.compile(
    r"^(?:\d+\s*to\s*\d+|\d+\+|(?:fewer|less)\s+than\s+\d+)$", re.IGNORECASE
)
# Lines that end the skills block in a job tile (client block follows the skills).
_SKILLS_END_MARKERS = ("Verified", "Payment", "Rating", "$", "United States")


def _find_proposals(rows: list[str]) -> tuple[int | None, str]:
    """Return the proposals line index and its bucket text ("" when none is shown).

    Older tiles put the bucket on the label's line (``Proposals: 5 to 10``);
    newer ones put it after the label with no space or on the following line.
    """

    for index, row in enumerate(rows):
        if "Proposals:" not in row:
            continue
        inline = clean_job_proposals(row)
        if inline:
            return index, inline
        for offset in (1, 2):
            if index + offset >= len(rows):
                break
            value = rows[index + offset].strip()
            if not value:
                continue
            if _PROPOSALS_BUCKET.match(value):
                return index + offset, value
            break
        return index, ""
    return None, ""


def _find_skills(rows: list[str], description_index: int) -> list[str]:
    """Return the raw skill lines of a tile, whichever layout it uses."""

    skills_heading = next((i for i, item in enumerate(rows) if item.strip() == "Skills"), None)
    # Older layout: skills follow a "Skills" heading. Newer layout has no
    # heading; the skills are the lines between the description and the
    # client block.
    start = skills_heading if skills_heading is not None else description_index
    for index in range(start + 1, len(rows)):
        if any(marker in rows[index] for marker in _SKILLS_END_MARKERS):
            return rows[start + 1 : index]
    # No client block to bound the skills: refuse to guess.
    return rows[start + 1 :] if skills_heading is not None else []


def clean_skills(skills: list[str]) -> list[str]:
    """Remove non-skill labels from the skills section."""

    exclude_list = {
        "more",
        "Next skills. Update list",
        "Skip skills",
        "  Payment verified",
        "  Payment unverified",
        "Skills",
        "Verified",
        "Payment verified",
        "Payment unverified",
    }
    cleaned = []
    for skill in skills:
        value = skill.strip()
        if not value or value in exclude_list:
            continue
        if "Rating is" in value or "$" in value or value.startswith('about "'):
            continue
        cleaned.append(value)
    return cleaned


def parse_job_details(rows: list[str], job_url: str | None = None) -> dict:
    """Parse one job card represented as a list of visible text lines."""

    if not rows:
        return {
            "posted_date": datetime.now(),
            "job_title": "",
            "job_description": "",
            "job_proposals": "",
            "job_tags": "[]",
            "job_id": generate_job_id("", job_url),
        }

    time_keywords = ["ago", "yesterday", "week", "day", "hour", "minute", "just now"]
    posted_date_text = next(
        (item for item in rows if any(keyword in item.lower() for keyword in time_keywords)),
        rows[0],
    )
    proposals_index, proposals = _find_proposals(rows)
    description_index = max(range(len(rows)), key=lambda index: len(rows[index]))
    description = rows[description_index]
    # The bucket line of the newer layout must not be mistaken for the title.
    title_rows = [item for index, item in enumerate(rows) if index != proposals_index]

    title = ""
    url_title_hint = extract_title_from_url(job_url)
    if url_title_hint:
        hint_words = _word_set(url_title_hint)
        for item in title_rows:
            value = item.strip()
            if not value or len(value) > 150:
                continue
            item_words = _word_set(value)
            if not item_words:
                continue
            if hint_words.issubset(item_words) or item_words.issubset(hint_words):
                title = value
                break

    if not title:
        title = next((item for item in title_rows if f"Job feedback {item}" in rows), "")

    if not title:
        blacklist = [
            "•",
            "more",
            "skills",
            "verified",
            "rating is",
            "payment",
            "save job",
            "job feedback",
            "proposals:",
            'about "',
        ]
        for item in title_rows:
            value = item.strip()
            value_lower = value.lower()
            if (
                value
                and not any(
                    value_lower == item or value_lower.startswith(item) for item in blacklist
                )
                and not any(keyword in value_lower for keyword in time_keywords)
                and len(value) < 150
            ):
                title = value
                break

    job_tags = _find_skills(rows, description_index)

    return {
        "posted_date": calculate_posted_datetime(posted_date_text),
        "job_title": title,
        "job_description": description,
        "job_proposals": proposals,
        "job_tags": json.dumps(clean_skills(job_tags)),
        "job_id": generate_job_id(title, job_url, description),
    }
