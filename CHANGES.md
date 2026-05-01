# Intelligent Upgrade Implementation

Implemented missing production features:

- DB-backed AI query logging in `ai_search_query_logs`.
- DB-backed user/admin feedback in `ai_search_query_feedback`.
- Laravel migration for both AI backend tables.
- `/feedback` endpoint to save thumbs up/down and corrections.
- `/logs` endpoint to inspect recent query logs.
- OpenAI-compatible LLM provider support without requiring a key by default.
- Stronger entity-aware intents:
  - `revenue_by_treatment`
  - `revenue_by_doctor`
  - `bookings_by_doctor`
  - `booking_list_by_doctor`
  - `prescriptions_by_doctor`
  - `patients_by_treatment`
- More complete intent catalog examples.
- Automatic logging of semantic score/method/matched example.
- Follow-up suggestions in query responses for a more chat-like UX.
- `scripts/analyze_logs.py` to review logs and suggest new intent catalog examples.

The Python service and Laravel app both use the same MySQL database. The included Laravel migration creates the AI tables in that shared DB.
