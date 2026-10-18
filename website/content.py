"""Single source of truth for public website copy and structured content.

Every feature, service, step and FAQ answer in this module was verified
against the implementation before being written down:

* Features and workflows: ``core/models.py``, ``core/urls.py``,
  ``core/views.py``, ``core/services/`` and README sections 9.1-9.5.
* Feature entitlements and quotas: ``tenancy/feature_defaults.py``.
* SMS behaviour: ``core/services/sms.py``, ``core/tasks.py``,
  ``core/models.py`` (``SMSDelivery``, ``SMSConfiguration``).

Nothing here may claim a capability the repository does not implement. Where a
policy is undecided the answer directs the visitor to contact Edensys rather
than inventing a commitment.

Templates render this content; they must never restate it. To change copy,
change it here.
"""

# ---------------------------------------------------------------------------
# Calls to action
# ---------------------------------------------------------------------------
PRIMARY_CTA_LABEL = "Request a Demo"
SECONDARY_CTA_LABEL = "Contact Us"
EXPLORE_CTA_LABEL = "Explore Features"
LOGIN_CTA_LABEL = "Login"

HEADLINE = "Smarter Boarding. Safer Students. Simpler School Operations."
SUBHEADLINE = (
    "Bring student records, dormitory management, roll calls, exeat approvals, "
    "gate movements, ID cards, and parent notifications into one organised "
    "boarding-management system."
)

PRODUCT_SUMMARY = (
    "A boarding-school management solution for Ghanaian senior high schools."
)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
NAV_LINKS = (
    {"label": "Features", "url_name": "website:features", "key": "features"},
    {"label": "Services", "url_name": "website:services", "key": "services"},
    {"label": "How it works", "url_name": "website:how_it_works", "key": "how_it_works"},
    {"label": "Pricing", "url_name": "website:pricing", "key": "pricing"},
    {"label": "FAQs", "url_name": "website:faq", "key": "faq"},
    {"label": "Contact", "url_name": "website:contact", "key": "contact"},
)

FOOTER_LINKS = (
    {"label": "Home", "url_name": "website:home"},
    {"label": "Features", "url_name": "website:features"},
    {"label": "Services", "url_name": "website:services"},
    {"label": "How it works", "url_name": "website:how_it_works"},
    {"label": "Pricing", "url_name": "website:pricing"},
    {"label": "FAQs", "url_name": "website:faq"},
    {"label": "Contact", "url_name": "website:contact"},
    {"label": "Privacy Policy", "url_name": "website:privacy"},
    {"label": "Terms of Service", "url_name": "website:terms"},
)

# ---------------------------------------------------------------------------
# The operational problems the product addresses
# ---------------------------------------------------------------------------
CHALLENGES = (
    {
        "title": "Student records scattered across paper and spreadsheets",
        "body": (
            "Admission numbers, houses, dormitories and guardian contacts live "
            "in different files. Finding one student's boarding details can "
            "take longer than the decision itself."
        ),
    },
    {
        "title": "Roll call that nobody can reconcile afterwards",
        "body": (
            "A clipboard round is only useful if someone can later prove who "
            "was present, who was absent, and what happened next."
        ),
    },
    {
        "title": "Exeat requests approved verbally, with no record",
        "body": (
            "Without a traceable approval, a housemaster cannot show who "
            "authorised a student's departure or when a student should return."
        ),
    },
    {
        "title": "The gate cannot confirm who is allowed out",
        "body": (
            "Gate staff need to confirm, in seconds, that a student holds a "
            "valid approved pass — not rely on a name being recognised."
        ),
    },
    {
        "title": "Guardians only find out when something goes wrong",
        "body": (
            "Parents expect to know that a request was received, approved, "
            "that their child has returned, or that a return is overdue."
        ),
    },
    {
        "title": "Leadership reports assembled by hand",
        "body": (
            "Boarding numbers, exeat volumes and absence patterns are needed "
            "for management and for inspections, often at short notice."
        ),
    },
)

# ---------------------------------------------------------------------------
# Benefits
# ---------------------------------------------------------------------------
BENEFITS = (
    {
        "title": "One record per student",
        "body": (
            "House, dormitory, room, guardian, photograph and boarding status "
            "in a single profile, rather than spread across separate files."
        ),
    },
    {
        "title": "Scoped to the staff who need it",
        "body": (
            "Housemasters see their own house. Senior staff see the whole "
            "school. A gate officer sees only the gate interface. Permissions "
            "are enforced on the server, not merely hidden in the interface."
        ),
    },
    {
        "title": "Evidence rather than recollection",
        "body": (
            "Every exeat, approval, gate movement, roll-call entry and "
            "disciplinary correction is timestamped and attributed."
        ),
    },
    {
        "title": "Guardians kept informed",
        "body": (
            "Approved, rejected, left campus, safe return and overdue events "
            "can each be sent to the guardian's phone as a text message."
        ),
    },
    {
        "title": "Set up for how schools actually work",
        "body": (
            "Academic years and terms, houses, dormitories and rooms, "
            "programmes, class groups, and termly operations."
        ),
    },
)

# ---------------------------------------------------------------------------
# Features — one entry per verified capability
# ---------------------------------------------------------------------------
FEATURES = (
    {
        "key": "student-records",
        "number": "01",
        "title": "Student records and dormitory management",
        "summary": (
            "A single boarding profile per student, held in the school's own "
            "isolated database."
        ),
        "benefit": (
            "Staff stop maintaining parallel lists, and a student's boarding "
            "details can be found in seconds."
        ),
        "points": (
            "Admission number, index number, full name, gender, date of birth "
            "and photograph.",
            "Programme, class group, house, dormitory and room assignment.",
            "Boarding or day residency, and current enrolment status.",
            "Guardian name, primary phone number and alternative phone number.",
            "Bulk roster import from Excel (.xlsx) or CSV, up to 10 MB and "
            "2,000 rows per upload.",
            "The import refuses duplicate admission or index numbers, refuses "
            "a boarding student with no gender-compatible dormitory, and "
            "refuses an upload that would exceed a room's capacity.",
            "Importing a roster never silently overwrites an existing student "
            "record.",
        ),
    },
    {
        "key": "roll-call",
        "number": "02",
        "title": "Boarding roll calls and attendance",
        "summary": (
            "Structured roll-call sessions instead of an unverifiable "
            "clipboard round."
        ),
        "benefit": (
            "Absences are recorded against a named session, and each absence "
            "has somewhere to go afterwards."
        ),
        "points": (
            "Sessions are opened for a house or dormitory and start with every "
            "student unmarked.",
            "A student whose gate departure is recorded is marked as being on "
            "exeat automatically, so they are not chased for an absence.",
            "A session left unfinished expires automatically after one hour so "
            "the roster cannot be locked indefinitely.",
            "Unexcused absences move into a follow-up queue for investigation.",
            "A recorded absence can be resolved with a resulting status and a "
            "resolution note, and the follow-up can raise a disciplinary "
            "record where that is the outcome.",
            "Where a roll call conflicts with a gate record, the conflict is "
            "flagged for review rather than silently overwritten.",
        ),
    },
    {
        "key": "exeat",
        "number": "03",
        "title": "Exeat requests, approvals and records",
        "summary": (
            "A traceable lifecycle from request through approval to "
            "reconciliation."
        ),
        "benefit": (
            "Nobody has to remember who authorised a departure, and overdue "
            "returns surface on their own."
        ),
        "points": (
            "Requests carry a type of leave, a reason, a destination, and "
            "expected departure and return times.",
            "A numbered exeat record is created per request, per academic term.",
            "Approval is restricted to authorised roles, and each approver sees "
            "only the requests within their jurisdiction — a housemaster sees "
            "their own house, senior staff see the whole school.",
            "Approval, rejection and approval remarks are recorded, with the "
            "deciding user and the authorising user.",
            "Approved records awaiting gate activity can be reconciled with an "
            "outcome and a reason when a gate record is missing.",
            "Pending approvals, unresolved passes and overdue returns appear as "
            "notifications for the staff who need to act.",
        ),
    },
    {
        "key": "gate",
        "number": "04",
        "title": "Student gate-movement records",
        "summary": (
            "Departure and return recorded at the gate against an approved "
            "pass."
        ),
        "benefit": (
            "The gate can confirm that a student is authorised to be out, and "
            "the school holds a real movement log."
        ),
        "points": (
            "A dedicated gate interface for recording the actual departure and "
            "actual return times.",
            "Read-only verification of an approved pass, with pass verification "
            "available from the gate screen.",
            "Recorded movements feed the roll call, so students who have left "
            "the campus are not marked absent by mistake.",
            "Movements past their expected return raise overdue alerts for "
            "senior staff and generate guardian reminders while the student "
            "remains unreturned.",
        ),
    },
    {
        "key": "id-cards",
        "number": "05",
        "title": "Student ID cards with QR codes",
        "summary": (
            "Printable dual-sided student cards, generated in bulk with a "
            "unique verification code."
        ),
        "benefit": (
            "A member of the public can check whether a student holds a valid "
            "approved movement without any student information being disclosed."
        ),
        "points": (
            "CR80 card proportions, with the student's portrait and school "
            "logo, generated as a bulk export so the whole school can be "
            "printed from a single job.",
            "Each printed code is a unique, unguessable identifier — it does "
            "not contain the admission number or the student's name.",
            "The public verification page reports only whether an approved "
            "movement is active and whether a departure was logged. It never "
            "displays the student's name, photograph, admission number or "
            "exeat details.",
            "The verification page is explicitly excluded from search-engine "
            "indexing.",
            "If a card is lost, the school can invalidate it and issue a "
            "replacement code immediately.",
            "The public verification page carries a form for reporting a "
            "movement concern, so a member of the public can flag a suspicion "
            "for staff review.",
        ),
    },
    {
        "key": "sms",
        "number": "06",
        "title": "Parent and guardian SMS notifications",
        "summary": (
            "Automatic text messages to the guardian through each stage of the "
            "boarding workflow."
        ),
        "benefit": (
            "Guardians are informed when a request is made, when it is "
            "approved or rejected, when their child leaves campus, when they "
            "return, and if a return is overdue."
        ),
        "points": (
            "Request received, approved, rejected, left campus, and safe return "
            "messages.",
            "Daily overdue reminders for as long as an approved student remains "
            "unreturned.",
            "Messages are queued and dispatched by a background worker, so a "
            "temporary provider problem delays a message instead of losing it, "
            "and failed attempts are retried automatically.",
            "Every message is recorded with its recipient, status, attempt "
            "count and provider reference, so usage can be reviewed.",
            "SMS is switched off by default and requires two independent "
            "switches: the school's subscription entitlement and the school's "
            "own configuration toggle. A school can turn SMS off at any time.",
            "An SMS usage report shows what has been sent and the remaining "
            "quota for the month.",
        ),
    },
    {
        "key": "reports",
        "number": "07",
        "title": "Operational reports and insights",
        "summary": (
            "Boarding figures for management and inspection, available without "
            "assembling a spreadsheet by hand."
        ),
        "benefit": (
            "Leadership can answer questions about boarding with current data."
        ),
        "points": (
            "A reports dashboard with filterable records and a print-friendly "
            "layout.",
            "Boarding KPIs, with a CSV export for further analysis.",
            "Boarding demographics across houses, dormitories and programmes.",
            "SMS usage reporting against the monthly quota.",
            "A configuration audit trail showing who changed what, and when.",
        ),
    },
    {
        "key": "conduct",
        "number": "08",
        "title": "Conduct and disciplinary records",
        "summary": (
            "Incidents logged against a student with severity, action taken and "
            "an audit trail for corrections."
        ),
        "benefit": (
            "Recorded conduct history supports a consistent, defensible "
            "response rather than an argument about what was decided."
        ),
        "points": (
            "Incident records with offence, description, severity and action "
            "taken.",
            "Records can be raised directly from an unresolved roll-call "
            "absence.",
            "Corrections are attributed and timestamped rather than deleting "
            "the original entry.",
            "Critical incidents surface as notifications for the staff "
            "responsible for conduct.",
        ),
    },
)

# ---------------------------------------------------------------------------
# Services — proposed offerings, awaiting confirmation
# ---------------------------------------------------------------------------
SERVICES_INTRO = (
    "The following support is what Eden Creative Systems expects to provide "
    "around the product. These are the areas of assistance we discuss with "
    "each school; the exact scope, schedule and any cost are agreed in writing "
    "before work begins."
)

SERVICES_PROVISIONAL_NOTE = (
    "Service scope is confirmed per school. Nothing on this page should be "
    "read as a service-level commitment, a guaranteed response time, or a "
    "promise of on-site attendance."
)

SERVICES = (
    {
        "number": "01",
        "title": "System demonstration and school consultation",
        "body": (
            "A walkthrough of the boarding workflows against your own "
            "processes: how exeats are approved at your school, how roll call "
            "is run, and which staff need which access."
        ),
    },
    {
        "number": "02",
        "title": "Initial school configuration and onboarding",
        "body": (
            "Setting up your school's own isolated environment: academic year "
            "and terms, houses, dormitories and rooms, and the staff accounts "
            "and roles that match your structure."
        ),
    },
    {
        "number": "03",
        "title": "Assistance preparing student and dormitory data",
        "body": (
            "Support with the roster template so that student records, "
            "guardian contacts and dormitory assignments import cleanly on the "
            "first attempt."
        ),
    },
    {
        "number": "04",
        "title": "Staff orientation",
        "body": (
            "Orientation for the staff who will use the system day to day — "
            "housemasters, gate officers and administrators — covering their "
            "own part of the workflow and nothing more."
        ),
    },
    {
        "number": "05",
        "title": "Subscription and account support",
        "body": (
            "Managing your school's account: adding staff as responsibilities "
            "change, reviewing usage against your plan, and answering "
            "questions about your subscription."
        ),
    },
    {
        "number": "06",
        "title": "Technical assistance",
        "body": (
            "Support when something is not behaving as expected, including "
            "access to the school's environment with a time-limited, logged "
            "grant where that is necessary to investigate."
        ),
    },
    {
        "number": "07",
        "title": "SMS configuration and usage guidance",
        "body": (
            "Assistance with the SMS integration and practical guidance on "
            "message volume, so that guardian messaging is used deliberately "
            "rather than burning through quota unnoticed."
        ),
    },
)

# ---------------------------------------------------------------------------
# How it works
# ---------------------------------------------------------------------------
STEPS = (
    {
        "number": "1",
        "title": "Discuss the school's requirements",
        "body": (
            "We walk through how your boarding house actually runs — roll call, "
            "exeat approval, gate control, guardian contact — and confirm which "
            "roles need access."
        ),
    },
    {
        "number": "2",
        "title": "Configure the school and authorised users",
        "body": (
            "Your school gets its own isolated environment. Academic year and "
            "terms, houses, dormitories and rooms, and staff accounts with the "
            "right permissions are set up first."
        ),
    },
    {
        "number": "3",
        "title": "Organise student and dormitory records",
        "body": (
            "Student records are imported from your roster — or entered one by "
            "one — together with guardian contacts and dormitory assignments."
        ),
    },
    {
        "number": "4",
        "title": "Begin using the boarding workflows",
        "body": (
            "Roll calls, exeat approvals, gate movements, ID cards and guardian "
            "notifications are switched on as your staff are ready for each one."
        ),
    },
)

# ---------------------------------------------------------------------------
# Frequently asked questions
# ---------------------------------------------------------------------------
FAQS = (
    {
        "question": "What exactly is the SHS Boarding & Dorm Management System?",
        "answer": (
            "It is a boarding-management system for senior high schools, "
            "developed and owned by Eden Creative Systems. It keeps student "
            "records, dormitory assignments, roll calls, exeat approvals, gate "
            "movements, student ID cards, guardian text messages and boarding "
            "reports in one place. It is used by boarding staff — "
            "administrators, housemasters, house tutors and gate officers — "
            "not by students or parents directly."
        ),
    },
    {
        "question": "Is this a website or an installed program?",
        "answer": (
            "It is a web application. Your staff use it in a browser on a "
            "computer or phone. Nothing is installed on school machines, and "
            "the hosting and database are managed on your behalf."
        ),
    },
    {
        "question": "Can one school see another school's students?",
        "answer": (
            "No. Each school is provisioned into its own isolated database "
            "schema, and a request is only ever served against the school whose "
            "verified domain it arrived on. A hostname that has not been "
            "registered and verified for a school is rejected outright rather "
            "than falling through to some other school's data. Within a school, "
            "housemasters are further restricted to their own house."
        ),
    },
    {
        "question": "How are student records and dormitory assignments handled?",
        "answer": (
            "Each boarder has one record holding their admission and index "
            "numbers, programme, class, house, dormitory and room, guardian "
            "contacts and photograph. Records can be imported from your "
            "existing roster in Excel or CSV form. The import checks the file "
            "before it changes anything: duplicate admission or index numbers "
            "reject the whole upload, a boarding student must resolve to a "
            "valid gender-compatible dormitory, and an upload that would exceed "
            "a room's capacity is refused. An existing student record is never "
            "silently overwritten."
        ),
    },
    {
        "question": "How does roll call work in practice?",
        "answer": (
            "A housemaster opens a session for a house or dormitory. Every "
            "student starts unmarked. Students with a recorded gate departure "
            "are marked as being on exeat rather than absent. The session shows "
            "who is present, absent or unmarked, and a session left unfinished "
            "expires automatically after an hour. Unexcused absences are queued "
            "for follow-up, and each one can be resolved with an outcome and a "
            "note — including raising a disciplinary record where that is what "
            "happened."
        ),
    },
    {
        "question": "How are exeats approved, and who can approve them?",
        "answer": (
            "An exeat is raised against a student with a type of leave, a "
            "reason, a destination and expected departure and return times. It "
            "is then approved or rejected by an authorised member of staff, "
            "with remarks recorded. Approval rights are scoped: a housemaster "
            "handles their own house, while senior staff cover the whole "
            "school. House tutors can raise and view requests but cannot "
            "approve them."
        ),
    },
    {
        "question": "What happens at the gate?",
        "answer": (
            "The gate interface is used to verify an approved pass and then to "
            "record the actual departure and the actual return. Those records "
            "feed the roll call, so a student who has left campus is not marked "
            "absent by error. If an approved student does not return by their "
            "expected time, the movement becomes overdue and alerts the staff "
            "responsible."
        ),
    },
    {
        "question": "What does scanning a student ID card actually reveal?",
        "answer": (
            "Only whether an approved movement is currently active and whether "
            "a departure has been logged. It does not show the student's name, "
            "photograph, admission number or exeat details. The code is a "
            "random unique identifier rather than anything derived from the "
            "student's details, and the school can invalidate a lost card and "
            "issue a replacement. The verification page also allows a member of "
            "the public to report a movement concern for staff to review."
        ),
    },
    {
        "question": "How do guardian text messages work, and what do they cost?",
        "answer": (
            "Messages are sent automatically as an exeat moves through its "
            "lifecycle: request received, approved or rejected, left campus, "
            "safe return, and daily reminders while an approved student is "
            "overdue. SMS is separate from the software subscription and is "
            "charged per message actually sent, so a school that sends fewer "
            "messages pays less. Each plan includes a monthly message quota. "
            "SMS is off by default and requires both a subscription "
            "entitlement and the school's own switch, so a school that does not "
            "want it can leave it off. Current message pricing is confirmed "
            "with your quotation — see the pricing page."
        ),
    },
    {
        "question": "How much does the system cost?",
        "answer": (
            "Pricing depends on the size of your boarding house and the "
            "features you need, and it combines a subscription with a "
            "per-student element. We are finalising the published price list; "
            "until it is approved, we will give you exact figures for your "
            "school when you contact us or request a demonstration. Cloud "
            "hosting is provided as part of the service, and text messages are "
            "charged separately."
        ),
    },
    {
        "question": "How long does onboarding take?",
        "answer": (
            "It depends mostly on how ready your roster is. Your school's "
            "environment, terms, houses and dormitories are configured first; "
            "then student and dormitory records are organised; then the "
            "workflows are switched on as staff are ready. We will agree a "
            "realistic sequence with you during the demonstration rather than "
            "quoting a fixed period up front."
        ),
    },
    {
        "question": "Will our staff be trained?",
        "answer": (
            "Orientation for the staff who will use the system is part of the "
            "onboarding discussion. The exact scope of orientation and any "
            "associated cost are confirmed in writing with your school. We do "
            "not publish a general training commitment, because it is agreed "
            "per school."
        ),
    },
    {
        "question": "What support is available after we go live?",
        "answer": (
            "Ongoing account and technical assistance is provided. Where a "
            "problem needs access to your school's environment, support access "
            "is granted explicitly and is time-limited, and the reason for the "
            "access is logged. Specific response times and support levels are "
            "agreed with your school and are not published here."
        ),
    },
    {
        "question": "How do we see the system before committing?",
        "answer": (
            "Request a demonstration using the form on this site, or call or "
            "message us on the number in the contact section. We will walk "
            "through the boarding workflows with you and answer questions about "
            "your specific setup."
        ),
    },
    {
        "question": "Who owns the system, and is it linked to a particular school?",
        "answer": (
            "The system is developed and owned by Eden Creative Systems "
            "(Edensys). It is in pilot use with a senior high school, which "
            "does not own the product and whose name is not used as a "
            "commercial endorsement."
        ),
    },
)

# ---------------------------------------------------------------------------
# Privacy and trust statements — every claim below is verifiable in source
# ---------------------------------------------------------------------------
TRUST_FACTS = (
    {
        "title": "Each school's data is isolated",
        "body": (
            "Every school is provisioned into its own PostgreSQL schema. A "
            "request is served only against the school whose verified domain it "
            "arrived on, and an unregistered hostname is rejected rather than "
            "resolved to any school's data."
        ),
    },
    {
        "title": "Permissions are enforced on the server",
        "body": (
            "Role restrictions are applied in the application itself, not "
            "merely hidden in the interface. A housemaster's access to student "
            "records, approvals and roll calls is limited to their own house."
        ),
    },
    {
        "title": "Student photographs are not publicly reachable",
        "body": (
            "Photographs are stored outside the public web directory and are "
            "served only to authenticated staff, or through the deliberately "
            "limited student ID verification page."
        ),
    },
    {
        "title": "Sessions are short-lived",
        "body": (
            "A staff session expires after 30 minutes of inactivity, is "
            "refreshed by activity, and ends when the browser is closed, so an "
            "unattended shared machine is not left signed in."
        ),
    },
    {
        "title": "Abuse is rate limited",
        "body": (
            "Sign-in attempts and public forms are rate limited by client "
            "address, and public forms are protected against cross-site request "
            "forgery."
        ),
    },
    {
        "title": "Privileged changes are logged",
        "body": (
            "Configuration changes, administrative actions and corrections to "
            "disciplinary records are recorded with the user responsible and "
            "the time it happened."
        ),
    },
    {
        "title": "Backups are verifiable",
        "body": (
            "School environments are backed up with a checksum recorded "
            "alongside each archive, and a restore verifies that checksum and "
            "the archive's contents before it writes anything. A restore never "
            "drops or overwrites an existing database."
        ),
    },
    {
        "title": "Transport is encrypted",
        "body": (
            "In production the service is served over HTTPS only, with HSTS, "
            "secure session and CSRF cookies, and a restrictive Content "
            "Security Policy."
        ),
    },
)

TRUST_LIMITS = (
    "No system can guarantee absolute security, and this one does not claim "
    "to. Eden Creative Systems makes no claim of formal certification against "
    "any security or privacy standard, and publishes no availability "
    "guarantee. Your school remains responsible for the accuracy of the "
    "records it holds and for deciding who on its staff should have access."
)

# ---------------------------------------------------------------------------
# Illustrative product preview
# ---------------------------------------------------------------------------
# Rendered on the home page as a clearly-labelled interface illustration. The
# names, numbers and times are synthetic. No real student record appears
# anywhere on this website.
PRODUCT_PREVIEW = {
    "disclaimer": (
        "Illustrative interface. All names, numbers and times shown are "
        "synthetic examples."
    ),
    "roll_call": {
        "title": "Evening roll call",
        "meta": "House 2 · Boys' Block B",
        "stats": (
            {"label": "Present", "value": "184", "tone": "green"},
            {"label": "On exeat", "value": "6", "tone": "blue"},
            {"label": "Absent", "value": "2", "tone": "amber"},
        ),
        "rows": (
            {"name": "Student A. M.", "room": "B-14", "status": "Present", "tone": "green"},
            {"name": "Student K. B.", "room": "B-15", "status": "Present", "tone": "green"},
            {"name": "Student Y. O.", "room": "B-16", "status": "On exeat", "tone": "blue"},
            {"name": "Student D. A.", "room": "B-17", "status": "Absent", "tone": "amber"},
        ),
    },
    "exeat": {
        "title": "Exeat approvals",
        "meta": "Awaiting sign-off",
        "rows": (
            {
                "reference": "EXEAT-2026-0418",
                "detail": "Medical · District Hospital",
                "window": "Out 10:30 · Back 16:00",
                "status": "Awaiting approval",
                "tone": "amber",
            },
            {
                "reference": "EXEAT-2026-0417",
                "detail": "Family · Home visit",
                "window": "Out 08:00 · Back 18:00",
                "status": "Approved",
                "tone": "green",
            },
            {
                "reference": "EXEAT-2026-0416",
                "detail": "Medical · Clinic",
                "window": "Expected back 15:00",
                "status": "Overdue",
                "tone": "red",
            },
        ),
    },
    "notifications": (
        "SMS sent — request received",
        "SMS sent — approved",
        "SMS sent — left campus",
        "SMS sent — safe return",
        "SMS queued — overdue reminder",
    ),
}

# ---------------------------------------------------------------------------
# Verified absences
# ---------------------------------------------------------------------------
# Each entry was checked against the codebase. Publishing what the system does
# *not* do prevents a school from buying on an assumption. Nothing here should
# be removed without first implementing the capability.
NOT_INCLUDED = (
    "No parent, guardian or student login — the system is used by school staff.",
    "No academic grading, examination results or class timetables.",
    "No school fees, billing or payment collection from parents.",
    "No library, catering, medical or payroll records.",
    "No transport, vehicle or GPS tracking.",
    "No biometric or fingerprint capture — roll call is entered by staff.",
    "No mobile app — the system runs in a web browser on a computer or phone.",
    "No two-factor authentication or single sign-on.",
    "No card payments — enquiries are followed up directly by Edensys.",
)

# ---------------------------------------------------------------------------
# Rollout detail for the "How it works" page
# ---------------------------------------------------------------------------
ROLLOUT_PREPARATION = (
    {
        "title": "Your school's boarding structure",
        "body": (
            "The houses, dormitories and rooms boarders are assigned to, and "
            "the academic year and terms you operate."
        ),
    },
    {
        "title": "Which staff need access",
        "body": (
            "The people who will use the system and the responsibilities each "
            "one holds, so permissions match your structure rather than being "
            "worked out later."
        ),
    },
    {
        "title": "Your student roster",
        "body": (
            "Admission numbers, names, gender, programmes and classes, and "
            "guardian contacts. A rough spreadsheet is enough to start — the "
            "import will tell you if something is missing."
        ),
    },
    {
        "title": "How exeats are approved at your school",
        "body": (
            "Who may approve, whose requests each approver handles, and what "
            "the gate should check before releasing a student."
        ),
    },
)

ROLLOUT_NOTES = (
    {
        "title": "Records are checked before they are imported",
        "body": (
            "A roster import either succeeds completely or changes nothing. "
            "Duplicate admission or index numbers reject the upload, a boarding "
            "student must resolve to a valid gender-compatible dormitory, and a "
            "file that would exceed a room's capacity is refused."
        ),
    },
    {
        "title": "Features are switched on as you are ready",
        "body": (
            "A school does not have to adopt everything on day one. Roll call, "
            "exeats, gate control and guardian text messages can be introduced "
            "in the order that suits your staff."
        ),
    },
    {
        "title": "Text messaging starts switched off",
        "body": (
            "Guardian SMS requires both a subscription entitlement and the "
            "school's own configuration toggle. A school that is not ready for "
            "messaging incurs no message charges."
        ),
    },
    {
        "title": "Nothing is installed on school machines",
        "body": (
            "Staff use the system in a browser. There is no software to "
            "install, no server for the school to maintain, and no local data "
            "left on school computers."
        ),
    },
)

# ---------------------------------------------------------------------------
# Contact page
# ---------------------------------------------------------------------------
CONTACT_INTRO = (
    "The quickest way to reach us is by phone, WhatsApp or email. If you would "
    "rather we come back to you with times, use the demonstration request form "
    "and we will follow up."
)

CONTACT_RESPONSE_NOTE = (
    "We aim to respond to enquiries as promptly as we can. We do not publish a "
    "guaranteed response time, and we would rather be straight about that than "
    "promise one we cannot keep."
)

CONTACT_HELPFUL = (
    "Your school's name and where in Ghana you are.",
    "Whether you are asking about boarding operations, dormitory records, "
    "roll call, exeats or guardian messaging.",
    "Roughly how many boarding students you have, if you are asking about cost.",
    "Who at the school would join a demonstration, and their role.",
)

# ---------------------------------------------------------------------------
# Page metadata
# ---------------------------------------------------------------------------
PAGE_META = {
    "home": {
        "title": "Boarding & Dorm Management for Senior High Schools",
        "description": (
            "Organise student records, dormitory assignments, roll calls, exeat "
            "approvals, gate movements, student ID cards and guardian "
            "notifications in one boarding-management system for Ghanaian "
            "senior high schools."
        ),
    },
    "features": {
        "title": "Features",
        "description": (
            "Student records and dormitory management, boarding roll calls, "
            "exeat approvals, gate movements, QR student ID cards, guardian SMS "
            "and operational reports."
        ),
    },
    "services": {
        "title": "Services",
        "description": (
            "Demonstration and consultation, school configuration, roster data "
            "preparation, staff orientation, subscription support, technical "
            "assistance and SMS guidance."
        ),
    },
    "how_it_works": {
        "title": "How it works",
        "description": (
            "Four steps from an initial discussion to running boarding "
            "workflows: requirements, configuration, student records, and "
            "go-live."
        ),
    },
    "pricing": {
        "title": "Pricing",
        "description": (
            "Subscription and per-student pricing for the SHS Boarding & Dorm "
            "Management System, with SMS charged separately per message sent."
        ),
    },
    "faq": {
        "title": "Frequently asked questions",
        "description": (
            "Answers on student records, roll calls, exeats, gate movements, QR "
            "ID cards, guardian SMS, pricing, onboarding, support and "
            "student-data privacy."
        ),
    },
    "contact": {
        "title": "Contact",
        "description": (
            "Contact Eden Creative Systems about the SHS Boarding & Dorm "
            "Management System by phone, WhatsApp or email, or request a "
            "demonstration."
        ),
    },
    "demo_request": {
        "title": "Request a Demo",
        "description": (
            "Request a demonstration of the SHS Boarding & Dorm Management "
            "System for your senior high school."
        ),
    },
    "privacy": {
        "title": "Privacy Policy",
        "description": (
            "How Eden Creative Systems handles information submitted through "
            "this website and information held in school environments."
        ),
    },
    "terms": {
        "title": "Terms of Service",
        "description": (
            "The terms that apply to use of this website and to evaluation of "
            "the SHS Boarding & Dorm Management System."
        ),
    },
}
