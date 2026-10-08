run tailwind watch
-- npx tailwindcss -i ./src/css/input.css -o ./src/css/output.css --watch                    

run development server
-- python .\manage.py runserver --settings=schoolsystem.settings.development

## Exeat SMS notifications

Configure `SMS_ENABLED`, `SMS_ONLINE_GH_API_KEY`, `SMS_SENDER_ID`, `SMS_TIME_ZONE`,
and `REDIS_URL` in `.env`. Use Redis 6 or newer as the Celery broker; the
legacy Redis 5.0.14 server currently installed on this Windows machine does not
support the `HELLO` handshake used by the Redis Python client. SMS dispatch is
disabled unless `SMS_ENABLED=true`. After migration, the `SMS Configuration`
record in Django Admin controls the live SMS switch and takes precedence over
the environment default. Disable it to stop new SMS queueing, queued-message
dispatch, and overdue reminders; re-enable it to resume processing.
The SMS outbox records each recipient's delivery state and retry history.

Run Redis, then start the application and its asynchronous workers in separate
terminals:

```powershell
.\.venv\Scripts\python.exe .\manage.py runserver --settings=schoolsystem.settings.development
.\.venv\Scripts\celery.exe -A schoolsystem worker --pool=solo --concurrency=1 --loglevel=INFO
.\.venv\Scripts\celery.exe -A schoolsystem beat --loglevel=INFO
```

Exeats generate SMS notifications for request submission, approval or rejection,
gate departure and return, and overdue status. Guardians and the student's
assigned Housemaster receive the notices. Overdue reminders are sent at the
expected return time and then once every 24 hours until a return is recorded.

## Roll-call attendance verification

New roll-call rosters begin with students marked **Unmarked**; an active gate
exeat is automatically recorded as **On Exeat**. A gate departure recorded
during an in-progress roll call also marks an unmarked matching entry On Exeat.
It never overwrites a staff-verified status: a discrepancy is shown on the
roster and must be reviewed before the session can be finalized. A gate return
does not automatically mark a student present.

Staff-marked absences enter the authorized follow-up queue and are not conduct
incidents by default. A reviewer can confirm an unexcused absence, record an
authorized reason, or correct a recording error; only confirmation creates a
conduct incident. Follow-up decisions and corrections retain an audit trail.
Attendance status changes do not require a free-text reason; roll call does
not collect diagnoses or punishment details.

Staff must assign each remaining student a status before finalizing the
session. Unmarked students remain in the roster and attendance-rate denominator
but do not count as present. The attendance report also shows the number of
unmarked entries in sessions still in progress.
