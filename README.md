# Medical Booking Agentic Search

Hybrid natural-language analytics/search service for the medical booking system.

## What this version improves
- deterministic handling for common medical admin questions
- safer SQL execution with read-only validation
- structured `chart_hint` support to avoid Pydantic errors
- stronger date parsing for today / this week / last month / weekdays / last N days
- fuzzy matching for doctors, treatments, and medicines
- LLM kept as fallback, not as the only brain

## Supported query families
- booking counts
- booking lists by status/date/doctor
- doctor leaderboards by appointments
- revenue totals and top treatments by revenue
- patient registrations
- busiest day / weekday / day part / time slot
- unpaid billing entries
- prescription counts
- medicine stock lookup
- schedule lookup

## Run
```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

## Sample body
```json
{
  "query": "Which doctor has the most appointments this week?",
  "hospital_id": 1,
  "today": "2026-04-23",
  "timezone": "Asia/Kolkata"
}
```
