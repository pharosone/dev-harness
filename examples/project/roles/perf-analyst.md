The app runs as one API process and one worker on a small VM with a single PostgreSQL.

- Hot paths: `GET /api/lists/{id}/tasks` (every page view), the CSV import in
  `app/importer.py`, and the reminder loop in `app/worker.py` (once a minute over all due tasks).
- Profile Python with `python3 -m cProfile -s cumtime`; paste `EXPLAIN ANALYZE` for every new
  or changed query against `make seed-large`.
- The UI bundle budget is the output of `npm run build` in `web/`: report its size change.
