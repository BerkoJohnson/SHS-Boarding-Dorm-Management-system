"""Central pricing configuration for the public website.

Rules this module exists to enforce
-----------------------------------
1. **Pricing is unpublished until explicitly approved.** Nothing renders a
   figure unless ``settings.PRICING_PUBLIC_ENABLED`` is true *and* an approved
   price list exists below. Default behaviour is "Contact us for pricing".
2. **One source of truth.** Templates never contain a price. They render what
   this module returns.
3. **Internal commercial data never appears publicly.** Per-school contract
   terms, hosting cost bases and margins, and SMS cost bases and margins live
   in ``tenancy.TenantSubscription`` and are *not* read here. See
   ``website.tests.test_pricing`` for the guard tests that enforce this.

Why no figures yet
------------------
The implemented commercial model is termly, and its rates live in
``tenancy/management/commands/tenant_subscription.py`` (``PLAN_PRESETS``):

    Starter       GH₵500  per term + GH₵0.50 per student per month
    Standard      GH₵1,000 per term + GH₵0.65 per student per month
    Premium       GH₵2,000 per term + GH₵0.80 per student per month
    Enterprise    GH₵3,000 per term + GH₵0.60 per student per month
    Pilot         GH₵2,500 flat per term (pilot agreement only)

A separate annual model (GH₵1,200 / GH₵2,400 / GH₵6,000 plus GH₵1.50 / 2.00 /
2.50 per student per month) was discussed but is not implemented, and
``BillingCycle`` supports only ``termly`` and ``monthly``. The two cannot both
be published, so neither is published.

Approving pricing
-----------------
1. Decide the approved model and confirm it with the finance owner.
2. Populate ``APPROVED_PRICE_LIST`` and ``BILLING_EXPLANATION`` below.
3. Set ``PRICING_PUBLIC_ENABLED=true`` in the environment.
4. Run ``python manage.py test website`` and confirm the guard tests still
   pass.

Until step 3 happens the pricing page shows "Contact us for pricing" with the
Request a Demo call to action, and no figure is rendered anywhere on the site.
"""
from __future__ import annotations

from django.conf import settings

# Publish gate. The single switch that decides whether any figure is shown.
# This is intentionally the *only* thing that turns pricing on: an approved
# price list is necessary but not sufficient.
PRICING_STATUS_AWAITING_APPROVAL = "awaiting_approval"

# --- Approved price list ----------------------------------------------------
# Populate only after the commercial model is signed off. Expected shape:
#
#   APPROVED_PRICE_LIST = (
#       {
#           "name": "Standard",
#           "summary": "Recommended for most boarding houses.",
#           "base_fee": "GH₵1,000 per term",
#           "per_student": "GH₵0.65 per student per month",
#           "limits": "Up to 1,500 students",
#           "includes": ("Roll call", "Exeats and gate", "ID cards", "SMS"),
#           "excludes": ("Cloud hosting", "SMS message charges"),
#       },
#   )
APPROVED_PRICE_LIST: tuple[dict, ...] = ()

# Shown beneath the price list once published, to explain the calculation, what
# is included, and what is billed separately.
BILLING_EXPLANATION = ""

PUBLIC_DISCLAIMER = (
    "Pricing depends on the size of your boarding house and the features you "
    "need, so figures are confirmed with your school directly."
)

CONTACT_FOR_PRICING_HEADLINE = "Contact us for pricing"

CONTACT_FOR_PRICING_BODY = (
    "We are finalising the published price list. Tell us the size of your "
    "boarding house and the workflows you need, and we will give you exact "
    "figures for your school — including what hosting and text messages cost on "
    "top of the subscription."
)

# Facts that hold regardless of whether figures are published. Each is verified
# in the codebase; none of them discloses an amount.
COMMERCIAL_STRUCTURE = (
    {
        "title": "A subscription, plus a per-student element",
        "body": (
            "Each school's charge combines a periodic subscription fee with an "
            "amount that reflects the number of students on the system."
        ),
    },
    {
        "title": "Cloud hosting is provided",
        "body": (
            "The system runs on managed cloud infrastructure, so your school "
            "does not buy, host or maintain servers. How hosting appears on "
            "your account depends on the agreed arrangement."
        ),
    },
    {
        "title": "Text messages are separate, and charged per message sent",
        "body": (
            "Guardian SMS is not bundled into the subscription. It is charged "
            "on the messages actually sent, and each plan carries a monthly "
            "message quota. A school that sends fewer messages pays less."
        ),
    },
    {
        "title": "There is a pilot arrangement",
        "body": (
            "The system is in pilot use with a senior high school under a "
            "separate, individually agreed arrangement. Pilot terms are not "
            "offered publicly."
        ),
    },
    {
        "title": "No payment is taken through this website",
        "body": (
            "This website collects enquiries only. It does not process "
            "payments and holds no card details."
        ),
    },
    {
        "title": "SMS capability can be switched off",
        "body": (
            "If a school prefers not to use text messaging, it can leave the "
            "SMS feature switched off and incur no message charges."
        ),
    },
)

BILLING_QUESTIONS = (
    "How is the student count measured — all enrolled students, or boarders only?",
    "At what point in the term is the student count taken?",
    "Is the subscription billed per academic term, annually, or monthly?",
    "Is the cloud hosting charge itemised separately or absorbed into the subscription?",
    "How many text messages does a plan include each month, and what happens when that is exceeded?",
)

# Stated explicitly so nobody assumes these are bundled. Each is verified: SMS
# is charged per message actually sent, infrastructure appears as its own line
# item on the tenant invoice, and there is no installable client.
NOT_INCLUDED_IN_SUBSCRIPTION = (
    "Guardian text messages, which are charged on the messages actually sent "
    "and are additional to your plan's monthly message quota.",
    "Cloud infrastructure, where your school's arrangement bills it as a "
    "separate line item instead of including it in the subscription.",
    "Any hardware, network or data costs at the school.",
    "Custom development or bespoke integrations, which are outside the "
    "standard system.",
    "Workstation software. There is nothing to licence or install — the system "
    "runs in a browser.",
)


def pricing_is_published() -> bool:
    """Return ``True`` only when pricing has been approved for publication."""
    if not getattr(settings, "PRICING_PUBLIC_ENABLED", False):
        return False
    return bool(APPROVED_PRICE_LIST)


def get_pricing_context() -> dict:
    """Build the context the pricing page renders.

    The template decides what to display from ``published``; it never holds a
    figure of its own.
    """
    published = pricing_is_published()
    return {
        "published": published,
        "status": "published" if published else PRICING_STATUS_AWAITING_APPROVAL,
        "plans": list(APPROVED_PRICE_LIST) if published else [],
        "billing_explanation": BILLING_EXPLANATION if published else "",
        "disclaimer": PUBLIC_DISCLAIMER,
        "headline": CONTACT_FOR_PRICING_HEADLINE,
        "body": CONTACT_FOR_PRICING_BODY,
        "structure": COMMERCIAL_STRUCTURE,
        "billing_questions": BILLING_QUESTIONS,
        "not_included": NOT_INCLUDED_IN_SUBSCRIPTION,
    }
