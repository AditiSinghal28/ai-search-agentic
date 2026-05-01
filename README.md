# Medical Booking Agentic Search

Hybrid natural-language analytics/search service for the medical booking system.

## What this version improves
- Keeps your current deterministic SQL templates for common business questions.
- Adds automatic backend logging to `ai_search_query_logs` for every request, plan, SQL, result, answer, error, and latency.
- Adds a semantic intent layer over `intent_catalog.py`, so messy queries like `rebenue`, `revnu`, `income`, `earnings`, and poor grammar can still map to the correct intent.
- Upgrades fuzzy doctor/treatment/medicine matching into a searchable entity index with generated aliases and optional embeddings.
- Keeps the LLM as a fallback only; common queries use a typed plan plus deterministic SQL builder.
- Fixes tuple/list SQL parameters such as `IN :active_statuses` by using SQLAlchemy expanding bind parameters.

## Important architecture
```text
User query
  -> normalize + spelling/synonym cleanup
  -> semantic intent match against intent_catalog.py examples
  -> entity retrieval for treatment / medicine / doctor names
  -> typed PlannerOutput
  -> deterministic SQL builder for known business intents
  -> safe read-only SQL execution
  -> natural-language answer
  -> automatic log row update
```

## Logging table
The service can auto-create these tables if `AUTO_CREATE_AI_TABLES=true`, but for your Laravel project you should copy the included migration into the medical booking system and run `php artisan migrate`:

```text
medical-booking-system/database/migrations/2026_04_30_000001_create_ai_search_logs_tables.php
```

Tables created:

```sql
ai_search_query_logs
ai_search_query_feedback
```

It stores:
- incoming query
- normalized query
- interpreted intent
- structured plan JSON
- generated SQL
- SQL parameters
- result sample
- final answer
- chart hint
- success/error status
- error text
- latency in milliseconds

You should review this table later, group failed or low-quality queries, and then add clean examples to `app/services/intent_catalog.py`. Do not automatically copy raw user logs into the catalog, because logs can contain duplicates, bad grammar, wrong assumptions, or malicious text.

## Supported query families
- booking counts
- booking lists by status/date/doctor
- doctor leaderboards by appointments
- revenue totals
- revenue by treatment/category
- top treatments by revenue
- patient registrations
- busiest day part / time slot
- unpaid billing entries
- prescription counts
- medicine stock lookup
- schedule lookup
- doctor list
- all doctor schedules
- medicine list
- available medicines
- treatment list
- hospital-scoped patient list

## Run
```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

## Sample body
```json
{
  "query": "rebenue from derma this month",
  "hospital_id": 1,
  "today": "2026-04-23",
  "timezone": "Asia/Kolkata"
}
```


## Learning loop

1. Logs are stored automatically in `ai_search_query_logs`.
2. User feedback is stored in `ai_search_query_feedback`.
3. Review errors and weak examples:
   ```bash
   python scripts/analyze_logs.py --hospital-id 1 --status error --limit 500
   ```
4. Add clean approved examples to `app/services/intent_catalog.py`.
5. Add test cases to `eval/intent_eval_set.json`.
6. Run:
   ```bash
   python scripts/evaluate_intents.py --file eval/intent_eval_set.json --hospital-id 1
   ```

Do not auto-write raw logs into `intent_catalog.py`; keep that file curated.
