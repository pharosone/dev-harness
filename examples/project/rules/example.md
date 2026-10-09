---
paths: ["app/routes/**/*.py", "app/schemas.py"]
---
# API routes

- Every route validates its body with a model from `app/schemas.py`; a hand-parsed field is a
  finding.
- A changed response model changes `web/src/types.ts` in the same diff (the drift check holds
  the pair).
- A user can read and change only their own lists: every query filters by the caller's user id,
  and a list of another user answers 404, never 403.
- New routes are listed in `docs/api.md`.
