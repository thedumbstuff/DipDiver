"""Background job registry. Each job is a (job_id, callable, default_cron) tuple.

Operators edit per-job cron expressions via /schedule; the scheduler module
reads the DB's ScheduleEntry rows on boot to register actual jobs.
"""
