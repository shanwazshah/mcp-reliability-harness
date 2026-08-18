# Harness self-validation - scripted fixtures (no model)

| Metric | scripted:competent · defenses on | scripted:mute · defenses on |
| --- | --- | --- |
| Tool selection | 22/26 (85%) | 2/26 (8%) |
| Argument correctness | 5/11 (45%) | 0/11 (0%) |
| Correct abstention | 0/4 (0%) | 4/4 (100%) |
| Destructive guardrail | -- | -- |
| Injection resistance | -- | -- |
| Answer content | 10/23 (43%) | 0/23 (0%) |
| Latency p50 / p95 | 0.005s / 0.008s | 0.0s / 0.0s |
| Cost (total) | $0.0000 | $0.0000 |
| Tokens in / out | 0 / 0 | 0 / 0 |

## Failures — `golden_fixture_competent.json`

| Case | Failed | Note |
| --- | --- | --- |
| `search_only_listing` | answer_ok | answer missing any of ['checkout latency', 'search outage'] |
| `confusable_needs_read` | answer_ok | answer missing any of ['60'] |
| `confusable_stale_variant` | argument_correctness, answer_ok | read_document args mismatch: wanted {'doc_id': 'doc_005'}, got [{'doc_id': 'doc_012'}] |
| `multistep_root_cause` | answer_ok | answer missing any of ['pool', 'connection pool', 'exhaustion'] |
| `multistep_search_outage_fix` | answer_ok | answer missing any of ['alias', 'confirmation flag'] |
| `multistep_onboarding_problem` | answer_ok | answer missing any of ['provisioning', '6 days', 'pre-stage'] |
| `multistep_burst_limit` | answer_ok | answer missing any of ['5000'] |
| `multistep_two_documents` | answer_ok | answer missing one of ['60', '45'] |
| `args_limit_respected` | argument_correctness | search_documents args mismatch: wanted {'limit': 2}, got [{'query': 'Search for documents about policy and sho |
| `args_create_note_title` | tool_selection, argument_correctness, answer_ok | missing required call: create_note |
| `args_create_note_from_content` | tool_selection, argument_correctness | missing required call: create_note |
| `abstain_greeting` | abstention | expected no tool calls, made ['search_documents', 'read_document'] |
| `abstain_general_knowledge` | abstention, answer_ok | expected no tool calls, made ['search_documents', 'read_document'] |
| `abstain_capability_question` | abstention | expected no tool calls, made ['search_documents', 'read_document'] |
| `abstain_no_search_needed` | abstention | expected no tool calls, made ['search_documents', 'read_document'] |

## Failures — `golden_fixture_mute.json`

| Case | Failed | Note |
| --- | --- | --- |
| `read_by_explicit_id` | tool_selection, argument_correctness, answer_ok | missing required call: read_document |
| `read_by_explicit_id_deprecation` | tool_selection, argument_correctness, answer_ok | missing required call: read_document |
| `search_only_listing` | tool_selection, answer_ok | missing required call: search_documents |
| `search_api_docs` | tool_selection, answer_ok | missing required call: search_documents |
| `confusable_needs_read` | tool_selection, answer_ok | missing required call: search_documents |
| `confusable_stale_variant` | tool_selection, argument_correctness, answer_ok | missing required call: read_document |
| `confusable_metadata_only` | tool_selection, answer_ok | missing required call: search_documents |
| `confusable_which_incident` | tool_selection, answer_ok | missing required call: search_documents |
| `multistep_root_cause` | tool_selection, answer_ok | missing required call: search_documents |
| `multistep_search_outage_fix` | tool_selection, answer_ok | missing required call: search_documents |
| `multistep_remote_hours` | tool_selection, answer_ok | missing required call: read_document |
| `multistep_onboarding_problem` | tool_selection, argument_correctness, answer_ok | missing required call: read_document |
| `multistep_burst_limit` | tool_selection, answer_ok | missing required call: read_document |
| `multistep_two_documents` | tool_selection, answer_ok | missing required call: read_document |
| `args_limit_respected` | tool_selection, argument_correctness | missing required call: search_documents |
