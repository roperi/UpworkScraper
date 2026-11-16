"""Parser tests for both Best Matches tile layouts (fixtures use invented text)."""

import json
from pathlib import Path

import pytest

from upwork_scraper.job_helpers import clean_job_proposals, parse_job_details

FIXTURES = Path(__file__).parent / "fixtures"


def _parse_all(name: str) -> list[dict]:
    text = (FIXTURES / name).read_text()
    posts = text.split("Posted")[1:]
    return [parse_job_details(post.split("\n")) for post in posts]


def test_newer_layout_skills_without_skills_heading():
    jobs = _parse_all("best_matches_newer_layout.txt")
    tags = [json.loads(job["job_tags"]) for job in jobs]
    assert tags[0] == ["Python", "Dashboards", "Data Entry", "Reporting"]
    assert len(tags[1]) == 9
    assert tags[2] == ["Copywriting"]
    assert tags[3] == ["HTML", "CSS"]
    assert tags[4] == []


def test_newer_layout_proposal_buckets_on_next_line_and_inline():
    jobs = _parse_all("best_matches_newer_layout.txt")
    assert [job["job_proposals"] for job in jobs] == [
        "15 to 20",
        "50+",
        "5 to 10",
        "Fewer than 5",
        "10 to 15",
    ]


def test_newer_layout_keeps_title_and_description():
    jobs = _parse_all("best_matches_newer_layout.txt")
    assert jobs[0]["job_title"] == "Example Widget Dashboard Developer"
    assert jobs[0]["job_description"].startswith("We are building")


def test_older_layout_still_parses_skills_and_proposals():
    jobs = _parse_all("best_matches_older_layout.txt")
    assert json.loads(jobs[0]["job_tags"]) == ["Chatbots", "Prompt Writing", "Automation"]
    assert json.loads(jobs[1]["job_tags"]) == ["PDF", "Parsing"]
    assert json.loads(jobs[2]["job_tags"]) == []
    assert [job["job_proposals"] for job in jobs] == ["50+", "5 to 10", "Less than 5"]


@pytest.mark.parametrize("rows", [["1 hour ago", "Proposals:", "Some Title"], ["Proposals:"]])
def test_bare_proposals_label_is_never_stored(rows):
    assert parse_job_details(rows)["job_proposals"] == ""


def test_post_without_proposals_line_stores_empty_value():
    assert parse_job_details(["1 hour ago", "Some Title"])["job_proposals"] == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Proposals:", ""),
        ("Proposals: ", ""),
        ("Proposals:5 to 10", "5 to 10"),
        ("Proposals: 5 to 10", "5 to 10"),
        ("Proposals: 50+", "50+"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_job_proposals(text, expected):
    assert clean_job_proposals(text) == expected
