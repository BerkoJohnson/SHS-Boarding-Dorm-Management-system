# SHS Boarding & Dorm Management System — user manual

This is the plain-language operating guide for the pilot. It complements the
technical `README.md`. It does **not** replace school policy or the approved
decisions in `DECISIONS.md`.

## 1. Before you start

- Use your **named** account. Never share a login or use another person's
  account.
- Check that the correct academic term and house are selected before saving.
- If something looks wrong, record the facts and raise a dispute or notify the
  Senior Housemaster. Do not edit a closed record directly.
- SMS is disabled during the pilot. An action that shows “SMS queued” is not a
  guarantee that a guardian received a message.

## 2. Common tasks

### 2.1 Approve or reject an exeat

1. Open **Exeats → Pending approvals**.
2. Check the student, destination, dates, reason, and guardian details.
3. Approve only if the request is in your jurisdiction.
4. A rejection must include a clear reason so the requester can correct it.
5. Do not approve your own child's or relative's pass. Escalate it.

A medical/infirmary lead may approve only **Medical** passes. A Housemaster
initiates but does not authorize a general **Emergency** movement; the
Headmaster/ADMIN or Senior Housemaster authorizes it and records the reason.
The authorizer must not complete their own 24-hour review.

### 2.2 Change an exeat time

Use **Reschedule** on the exeat. Record the reason and the new departure and
return times. Rescheduling is blocked after the gate has recorded departure.
Do not change times by editing the database or Django admin.

### 2.3 Record a gate departure or return

1. Verify the pass at **Gate → Verify pass**.
2. Confirm the student's identity and the pass status.
3. Use **Log Departure** or **Log Return** once. Do not repeat the action if the
   page is slow; refresh and check the movement record first.
4. A late return creates a late-return incident. Record the return even when it
   is late; do not leave the pass open to avoid the incident.

Gate-duty enforcement is opt-in. If it is enabled and you are not on the current
roster, ask the Senior Housemaster or an administrator to cover the gate.

### 2.4 Complete a roll call

1. Open **Roll call** and select the correct house/session.
2. Mark every student: Present, Absent, On Exeat, Infirmary, or Activity.
3. Verify the entry for every student, then complete the session.
4. A completed session is locked. If a correction is needed, use the
   **Attendance corrections** workflow.

### 2.5 Correct a closed roll call

1. Open the completed session and choose **Request correction** on the entry.
2. Record the new status, a factual reason, and evidence (register, tutor note,
   or other document reference).
3. Present ↔ Absent and conduct-linked corrections require a second approver.
4. The requester cannot approve their own correction. A Senior Housemaster or
   administrator decides it.
5. Never use the normal roll-call screen or Django admin to change a completed
   session.

### 2.6 Handle a late return

1. Check **Operations → Late returns**.
2. At the first late return, contact the guardian and Housemaster.
3. At two hours late, the system escalates to the Senior Housemaster and creates
   a written incident.
4. At six hours late, the system escalates to the Headmaster.
5. The Headmaster decides any further escalation. Record the decision and keep
   the incident open until it is reviewed.
6. Do not put medical details in an SMS or in a broadly visible note.

### 2.7 Open and decide a dispute

1. Open **Disputes → New dispute**.
2. Select the student, category, and the exact record in dispute.
3. State what happened and what outcome is requested.
4. A different Senior Housemaster or administrator reviews the case. The person
   who raised it cannot decide it.
5. Record the decision and link any correction through the approved workflow.
   Do not change the disputed record first and close the case afterwards.

### 2.8 Export a report

1. Open **Reports & Stats** and select the report and period.
2. Check the scope shown on screen (house or school-wide).
3. Use **CSV**, **XLSX**, or **PDF**. Every export is logged with your name,
   report, filters, format, and row count.
4. Treat exports as sensitive student records. Store them only in the approved
   school location and delete local copies under the school's document-handling
   rules. Do not send them to personal email or WhatsApp.

## 3. Capacity exceptions

Dormitory capacity is blocked by default. Only the Headmaster/ADMIN or Senior
Housemaster may approve a written, time-bound exception, for no more than seven
days at a time. Record the reason and review the exception weekly. Overcapacity
must not become permanent.

## 4. What to do when something goes wrong

| Problem | Action |
|---|---|
| Wrong pass or wrong student | Stop. Do not record a movement. Notify the gate lead and raise a dispute. |
| Duplicate roll-call session | Do not delete it. Notify the Senior Housemaster; preserve both records for review. |
| Attendance changed after closure | Use the correction workflow with reason and evidence. |
| Late return not visible | Check the exeat and movement record, then notify the Senior Housemaster. Do not create a second pass. |
| Suspected account compromise | Stop using the account and notify the Headmaster and Eden Sys. Do not change shared credentials. |
| Data loss or bad restore | Follow D7/D8: obtain written authorization before any restore. |

## 5. Privacy and access

- Access student information only for your role and house.
- Do not export or forward a whole register unless the school has approved the
  purpose.
- Medical details are restricted. Do not copy them into general notes, SMS, or
  dispute summaries.
- The school owns the data. Eden Sys supports the system as processor under D8.

## 6. Training and support

Use the role quick starts in this folder. Record training attendance and open
questions in `TRAINING_AND_HANDOVER.md`. Report defects through the school's
approved support channel; never test fixes against live student data.
