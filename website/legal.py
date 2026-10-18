"""Prose for the public website's Privacy Policy and Terms of Service.

Held here rather than in the templates so that the wording has exactly one
source, and so that reviewers can read both documents without reading HTML.

These documents describe what the implementation actually does. Where a
decision has not yet been confirmed by the business, the document says so
explicitly rather than asserting a term that was never agreed:

* The service is offered under terms agreed individually with each school, so
  these terms cover the use of *this website* and evaluation of the system.
* Eden Creative Systems acts as the processor of student data on the school's
  instructions; the school remains the data controller.

Both documents should be reviewed by the business (and, if desired, by a
lawyer) before launch. See ``LEGAL_REVIEW_NOTICE``.
"""

# A single, honest statement of the status of these documents. Rendered at the
# top of both legal pages. Remove it only once the business has formally
# approved the wording.
LEGAL_REVIEW_NOTICE = (
    "This document describes how the service currently operates. Service terms "
    "for each school are agreed in writing with that school. Please contact "
    "Eden Creative Systems if anything here needs to be clarified for your "
    "school's circumstances."
)

LAST_UPDATED = "October 2026"

PRIVACY_INTRO = (
    "This policy explains how Eden Creative Systems (\"Edensys\", \"we\") "
    "handles personal information in two separate situations: information you "
    "send us through this website, and information held inside the boarding "
    "management system that a school uses."
)

PRIVACY_SECTIONS = (
    {
        "heading": "Information you send through this website",
        "body": (
            "If you submit the demonstration request form or contact us "
            "directly, we receive the details you provide: your name, your "
            "role, your school's name, your region, your phone number, your "
            "email address, an optional estimate of your boarding population, "
            "an optional preferred contact method, and any message you write. "
            "We also record a one-way cryptographic hash of the network "
            "address the request came from, together with your browser's "
            "user-agent string, solely to detect and limit automated abuse. "
            "The raw network address is not stored."
        ),
    },
    {
        "heading": "Why we use that information",
        "body": (
            "We use it to respond to your enquiry, to arrange and deliver a "
            "demonstration, and to keep a record of who we have spoken to. On "
            "the form we ask for your agreement to be contacted; that "
            "agreement is the basis on which we contact you. We do not sell "
            "enquiry details, and we do not use them for advertising."
        ),
    },
    {
        "heading": "How long enquiry information is kept",
        "body": (
            "Enquiries are retained for as long as they are useful for "
            "following up with your school and for our business records. If "
            "you would like an enquiry you submitted to be deleted, contact us "
            "using the details on the contact page and we will remove it, "
            "unless we are required to keep it."
        ),
    },
    {
        "heading": "Student information inside the system",
        "body": (
            "When a school uses the boarding management system, the school "
            "remains responsible for the student records it holds. Eden "
            "Creative Systems stores and processes those records on the "
            "school's instructions and for no other purpose. We do not use "
            "student information for our own marketing, and we do not disclose "
            "it to anyone else except as needed to operate the service or where "
            "the law requires it."
        ),
    },
    {
        "heading": "Who can see student information",
        "body": (
            "Access is limited by the school's own staff permissions. A "
            "housemaster is restricted to their own house; senior staff and "
            "administrators see more. Within Eden Creative Systems, access to a "
            "school's environment is limited to the personnel who support that "
            "school, and technical access is granted for a specific reason, is "
            "time-limited, and is recorded."
        ),
    },
    {
        "heading": "Separating one school from another",
        "body": (
            "Each school is provisioned into its own isolated database schema. "
            "A request is served only against the school whose verified domain "
            "it arrived on, and an unrecognised hostname is refused rather than "
            "resolved to any school's data."
        ),
    },
    {
        "heading": "Text messages to guardians",
        "body": (
            "Where a school enables guardian notifications, the guardian's name "
            "and phone number held in the school's records are used to send "
            "text messages about that student's boarding events. Message "
            "content is limited to the boarding event itself. Message delivery "
            "is handled by a communications provider, and delivery records are "
            "kept so usage can be reviewed and problems investigated."
        ),
    },
    {
        "heading": "Cookies and sessions",
        "body": (
            "This public website sets no tracking or advertising cookies and "
            "runs no third-party analytics. When staff sign in to the school "
            "application, a session cookie is used to keep them signed in; that "
            "session expires after 30 minutes of inactivity and when the "
            "browser is closed. A separate cookie protects forms against "
            "cross-site request forgery."
        ),
    },
    {
        "heading": "Where information is stored",
        "body": (
            "The application, its database and uploaded files are hosted on "
            "managed cloud infrastructure selected by Eden Creative Systems. "
            "Data is transmitted over encrypted connections, and production "
            "traffic is served over HTTPS only."
        ),
    },
    {
        "heading": "Your choices",
        "body": (
            "You may ask us what information we hold about you, ask for it to "
            "be corrected, or ask for it to be deleted. If your question "
            "concerns a student record held by a school, the school is the "
            "right point of contact because the school controls that record; we "
            "will assist the school if it asks us to."
        ),
    },
    {
        "heading": "Changes to this policy",
        "body": (
            "If this policy changes, the updated version will be published on "
            "this page with a new date."
        ),
    },
    {
        "heading": "Contacting us about privacy",
        "body": (
            "Privacy questions can be sent to the email address on the contact "
            "page, or raised by phone or WhatsApp using the published number."
        ),
    },
)

TERMS_INTRO = (
    "These terms cover your use of this website and the evaluation of the SHS "
    "Boarding & Dorm Management System. They are not the service agreement for "
    "a school: a school that goes live with the system does so under terms "
    "agreed in writing with Eden Creative Systems."
)

TERMS_SECTIONS = (
    {
        "heading": "Who we are",
        "body": (
            "This website is operated by Eden Creative Systems (also referred "
            "to as Edensys), which develops and owns the SHS Boarding & Dorm "
            "Management System."
        ),
    },
    {
        "heading": "Using this website",
        "body": (
            "You may read and share the information on this website for the "
            "purpose of evaluating the system for your school. You may not copy "
            "the site's content, branding or materials into your own "
            "publication, or present them as your own, without written "
            "permission."
        ),
    },
    {
        "heading": "Enquiries are not orders",
        "body": (
            "Submitting the demonstration request form, calling, or messaging "
            "us creates an enquiry only. It does not create a contract, reserve "
            "capacity, or oblige either party to proceed. Any arrangement to "
            "use the system is made separately and in writing."
        ),
    },
    {
        "heading": "Pricing and quotations",
        "body": (
            "Any figure we give you is specific to your school, the size of "
            "your boarding house and the features you need, and it remains "
            "valid for the period stated in the quotation. Where price lists "
            "are published on this site, they form part of the quotation "
            "process rather than a standing offer."
        ),
    },
    {
        "heading": "What we provide, and what we do not promise",
        "body": (
            "We provide the boarding management system together with the "
            "configuration, orientation and support agreed with your school. We "
            "do not publish an availability guarantee or a formal response-time "
            "commitment, and nothing on this website should be read as one. "
            "Specific service levels, if a school requires them, are agreed in "
            "writing for that school."
        ),
    },
    {
        "heading": "Your school's responsibilities",
        "body": (
            "Your school is responsible for the accuracy of the information it "
            "enters, for deciding which members of staff may access the system, "
            "for keeping staff accounts secure, and for complying with the "
            "obligations that apply to it as the controller of its students' "
            "information."
        ),
    },
    {
        "heading": "Availability and maintenance",
        "body": (
            "The service depends on managed cloud infrastructure and a "
            "communications provider for text messages. Interruptions to those "
            "services, and planned maintenance, can affect availability. Where "
            "a message cannot be delivered immediately it is queued and retried "
            "rather than discarded, but delivery cannot be guaranteed."
        ),
    },
    {
        "heading": "Intellectual property",
        "body": (
            "The system, its source code, its design and the Eden Creative "
            "Systems name and mark remain the property of Eden Creative "
            "Systems. A school's own records remain the school's."
        ),
    },
    {
        "heading": "Ending an evaluation",
        "body": (
            "Either party may end an evaluation at any time. If a school "
            "proceeds with the system and later stops using it, the school can "
            "request an export of its records before the account is closed."
        ),
    },
    {
        "heading": "Our liability",
        "body": (
            "This website is provided as-is for information purposes. To the "
            "extent permitted by law, Eden Creative Systems is not liable for "
            "loss arising from reliance on the general information published "
            "here. Liability in respect of a school's use of the system is "
            "governed by the agreement made with that school."
        ),
    },
    {
        "heading": "Governing law",
        "body": (
            "These terms are governed by the laws of the Republic of Ghana, and "
            "the courts of Ghana have jurisdiction over any dispute arising "
            "from them."
        ),
    },
    {
        "heading": "Changes to these terms",
        "body": (
            "We may update these terms; the version published on this page at "
            "the time of your visit applies to your use of the website."
        ),
    },
)

# Statements that deliberately avoid inventing commitments. Rendered so the
# business can see exactly what is being withheld and why.
TERMS_WITHHELD = (
    "No service-level agreement, uptime figure or guaranteed response time is published.",
    "No formal security or privacy certification is claimed.",
    "No refund, cancellation or notice period is published for school subscriptions.",
    "No data-retention period for closed school accounts is published.",
)
