# What was improved

## Core fixes
- `chart_hint` now accepts structured objects, not just strings.
- parameter names are normalized to avoid `:hospital_id` vs `hospital_id` mismatch.
- SQL generation no longer depends entirely on the LLM for common admin queries.
- read-only SQL guardrails were kept and strengthened.
- matching support added for medicines, doctors, and treatments.
- date parsing expanded for weekdays and common relative ranges.

## Newly supported query families
- booking counts
- booking lists by status/date
- revenue totals
- doctor with most appointments
- top N treatments by revenue
- patient registrations
- busiest time slot / day-part
- unpaid billing entries
- prescription counts
- medicine stock lookup
- doctor schedule lookup

## Notes
- This version is a hybrid system: deterministic first, LLM fallback second.
- That makes it more accurate, faster, and easier to debug for hospital admin use.
