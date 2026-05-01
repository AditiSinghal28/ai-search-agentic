# Learning loop from real query logs

This system should not write raw user queries directly into `intent_catalog.py`. The catalog should stay curated.

Use this loop instead:

1. **Collect logs automatically**
   - `/query` writes each query, normalized query, intent, SQL, answer, status, error, and latency into `ai_search_query_logs`.
   - `/feedback` writes thumbs-up/down or corrected intent/answer into `ai_search_query_feedback`.

2. **Find weak areas**
   Run:
   ```bash
   python scripts/analyze_logs.py --hospital-id 1 --status error --limit 500
   ```
   Also review low-confidence successful queries by checking `semantic_score` in the logs.

3. **Cluster and review**
   The analyzer groups similar queries and prints suggested examples. Do not blindly add every raw query. Pick clean representative examples.

4. **Update the catalog**
   Add approved examples to `app/services/intent_catalog.py`. Add a new intent only when many real queries need a new business action.

5. **Add evaluation tests**
   Add the same representative query to `eval/intent_eval_set.json` with the expected intent.

6. **Run the evaluator**
   ```bash
   python scripts/evaluate_intents.py --file eval/intent_eval_set.json --hospital-id 1
   ```

7. **Deploy only if accuracy improves**
   This prevents one new example from breaking older query behavior.

Recommended weekly process during development: export/review failed logs, add 5-20 curated examples, run evaluation, then deploy.
