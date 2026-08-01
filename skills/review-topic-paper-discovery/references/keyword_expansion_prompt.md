# Keyword Expansion Prompt

Given a review topic contract and user-provided queries, generate a concise,
domain-independent query plan for literature discovery.

Rules:

- Keep the user's original keywords unless clearly irrelevant.
- Add a compact core query and queries for distinct coverage dimensions,
  populations or materials, mechanisms or outcomes, methods or evidence, and
  scope constraints that are actually declared in the contract.
- Do not infer a domain-specific family merely because one generic token
  overlaps it.
- Do not create too many broad generic queries.
- Prefer self-contained provider queries over prose phrases. Do not append the
  entire manuscript title to every query.
- Classify each keyword as one of:
  - `core_topic`
  - `coverage`
  - `population_or_material`
  - `mechanism_or_outcome`
  - `method_or_evidence`
  - `scope`
  - `document_scope`
- If a query does not fit cleanly, classify it as `coverage`.
- Mark source as `user`, `agent`, or both.

Expected output shape:

```json
{
  "user_topic": "...",
  "user_keywords": ["..."],
  "agent_keywords": [
    {"keyword": "...", "category": "...", "reason": "..."}
  ],
  "merged_keywords": [
    {"keyword": "...", "category": "...", "source": ["user", "agent"], "keep": true}
  ]
}
```
