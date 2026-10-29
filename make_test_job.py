import json
from datetime import datetime, timedelta, timezone
middle = "Our store sells custom apparel and home goods through print-on-demand. " * 22
now = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
job = {
    "id": "test-dual-001",
    "title": "Shopify Virtual Assistant for Product Listings and Customer Support (POD)",
    "description": (
        "We run a Shopify print-on-demand store and need daily help keeping the store running smoothly. "
        + middle +
        "Requirements: careful product management, clear English communication, and attention to detail. "
        "Please include: tell us briefly about your experience and share any stores you have worked on. "
        "Screening questions: 1. Describe your recent experience with similar projects. "
        "2. Please list any certifications related to this project. "
        "To apply, please start your proposal with POD so we know you read this post."
    ),
    "description_snippet": "",
    "skills": ["Shopify", "Printify", "Customer Support", "Product Listing"],
    "job_type": "hourly",
    "budget": 25,
    "client": {"country": "United States", "rating": 4.9, "verification_status": "VERIFIED", "total_posted_jobs": 10, "total_spent": "$5,000"},
    "proposals_tier": "fewer than 5",
    "published_date": now,
    "url": "https://www.upwork.com/jobs/test-dual-001",
}
with open("test_job.json", "w", encoding="utf-8") as f:
    json.dump([job], f, ensure_ascii=False, indent=2)
print("OK", now)
