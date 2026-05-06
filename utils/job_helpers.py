import hashlib
import json
from datetime import datetime, timedelta
import re
from urllib.parse import unquote


def extract_job_id_from_url(url):
    """
    Extract the unique Upwork job cipher (e.g., 01...) from the URL.
    Strips the leading '~'.
    """
    if not url:
        return None
    # Upwork URLs usually end with _~01...
    match = re.search(r'_~([a-f0-9]+)', url)
    if match:
        return match.group(1)
    return None


def extract_title_from_url(url):
    """
    Extract and clean the job title slug from the URL.
    """
    if not url:
        return None
    try:
        # Example: https://www.upwork.com/jobs/Software-Ai-Developer_~01...
        path = url.split('/jobs/')[-1]
        slug = path.split('_~')[0]
        # Replace hyphens with spaces and unquote URL encoding
        title = unquote(slug.replace('-', ' '))
        return title
    except Exception:
        return None


def generate_job_id(job_title, job_url=None, job_description=""):
    """
    Generate a unique job ID. Prefers the Upwork cipher from the URL.
    """
    # 1. Try extracting from URL
    cipher = extract_job_id_from_url(job_url)
    if cipher:
        return cipher

    # 2. Fallback: Hash title and description for more uniqueness than just title
    content = f"{job_title.lower()}|{job_description[:100].lower()}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def calculate_posted_datetime(timestamp):
    """
    Calculate the datetime when a job was posted based on the given timestamp.
    """
    now = datetime.now()
    t_lower = timestamp.lower()
    try:
        if 'just now' in t_lower:
            posted_datetime = now
        elif 'yesterday' in t_lower:
            posted_datetime = now - timedelta(days=1)
        elif 'hour' in t_lower:
            match = re.findall(r'\d+', timestamp)
            hours_ago = int(match[0]) if match else 0
            posted_datetime = now - timedelta(hours=hours_ago)
        elif 'day' in t_lower:
            match = re.findall(r'\d+', timestamp)
            days_ago = int(match[0]) if match else 0
            posted_datetime = now - timedelta(days=days_ago)
        elif 'last week' in t_lower:
            posted_datetime = now - timedelta(weeks=1)
        elif 'week' in t_lower:
            match = re.findall(r'\d+', timestamp)
            weeks_ago = int(match[0]) if match else 1
            posted_datetime = now - timedelta(weeks=weeks_ago)
        elif 'minute' in t_lower:
            match = re.findall(r'\d+', timestamp)
            minutes_ago = int(match[0]) if match else 0
            posted_datetime = now - timedelta(minutes=minutes_ago)
        else:
            posted_datetime = now
    except Exception:
        posted_datetime = now
    return posted_datetime


def clean_job_proposals(job_proposals_text):
    if not job_proposals_text:
        return ''
    if 'freelancers' in job_proposals_text:
        job_proposals = job_proposals_text.replace('Proposals: ', '').split(' Nu')[0]
    elif ' ago' in job_proposals_text:
        job_proposals = ''
    else:
        job_proposals = job_proposals_text.replace(
            'Proposals: ', '').replace('Load More Jobs', '').replace('Featured', '')
    return job_proposals.strip()


def clean_skills(skills):
    cleaned = []
    exclude_list = [
        'more', 'Next skills. Update list', 'Skip skills', 
        '  Payment verified', '  Payment unverified', 'Skills', 
        'Verified', 'Payment verified', 'Payment unverified'
    ]
    for s in skills:
        s_clean = s.strip()
        if not s_clean:
            continue
        if s_clean in exclude_list:
            continue
        if 'Rating is' in s_clean or '$' in s_clean:
            continue
        cleaned.append(s_clean)
    return cleaned


def parse_job_details(r, job_url=None):
    """
    Parse job details from a given row of data using heuristics and URL hints.

    Parameters:
    - r (list): A list containing job details as lines of text.
    - job_url (str): Optional URL of the job for better identification.

    Returns:
    - dict: A dictionary containing parsed job details.
    """
    if not r:
        return {
            'posted_date': datetime.now(),
            'job_title': '',
            'job_description': '',
            'job_proposals': '',
            'job_tags': '[]',
            'job_id': generate_job_id('', job_url)
        }

    # 1. Posted Date Heuristic
    posted_date_str = r[0]
    time_keywords = ['ago', 'yesterday', 'week', 'day', 'hour', 'minute', 'just now']
    for item in r:
        if any(kw in item.lower() for kw in time_keywords):
            posted_date_str = item
            break
            
    # 2. Job Proposals Heuristic
    job_proposals_text = ''
    for item in r:
        if 'Proposals:' in item:
            job_proposals_text = item
            break
            
    # 3. Job Description Heuristic
    # Description is the longest continuous text block
    job_description = max(r, key=len) if r else ""

    # 4. Job Title Heuristic
    job_title = ''
    url_title_hint = extract_title_from_url(job_url)

    # Strategy A: Use URL Hint (Strongest)
    if url_title_hint:
        # Look for a string in r that contains a significant part of the URL hint
        # Or matches exactly (case insensitive)
        hint_words = set(url_title_hint.lower().split())
        for item in r:
            it_clean = item.strip()
            if not it_clean or len(it_clean) > 150: continue
            it_words = set(it_clean.lower().split())
            # If the item's words are a superset or very similar to hint words
            if hint_words.issubset(it_words) or it_words.issubset(hint_words):
                job_title = it_clean
                break

    # Strategy B: Use 'Job feedback' pattern
    if not job_title:
        for item in r:
            if f"Job feedback {item}" in r:
                job_title = item
                break
            
    # Strategy C: Fallback - find first valid candidate
    if not job_title:
        blacklist = ['•', 'more', 'skills', 'verified', 'rating is', 'payment', 'save job', 'job feedback', 'proposals:', 'posted', 'about "']
        for item in r:
            it = item.strip()
            it_lower = it.lower()
            if (it and 
                not any(it_lower == b or it_lower.startswith(b) for b in blacklist) and 
                not any(kw in it_lower for kw in time_keywords) and 
                len(it) < 150):
                job_title = it
                break

    # 5. Job Tags Heuristic
    job_tags_list = []
    try:
        skills_idx = -1
        for i, item in enumerate(r):
            if item.strip() == 'Skills':
                skills_idx = i
                break
        
        if skills_idx != -1:
            end_idx = len(r)
            for i in range(skills_idx + 1, len(r)):
                if any(marker in r[i] for marker in ['Verified', 'Payment', 'Rating', '$', 'United States']):
                    end_idx = i
                    break
            job_tags_list = r[skills_idx + 1 : end_idx]
    except Exception:
        pass

    return {
        'posted_date': calculate_posted_datetime(posted_date_str),
        'job_title': job_title,
        'job_description': job_description,
        'job_proposals': clean_job_proposals(job_proposals_text),
        'job_tags': json.dumps(clean_skills(job_tags_list)),
        'job_id': generate_job_id(job_title, job_url, job_description)
    }
