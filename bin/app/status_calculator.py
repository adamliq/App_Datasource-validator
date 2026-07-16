"""
Status rollup rules, exactly as locked in HANDOFF.md.

Data source status: PASS / FAIL / ERROR / RUNNING / QUEUED / NOT RUN /
DISABLED / CANCELLED, with a STALE overlay on a PASS that is older than
the configurable threshold.

Platform status: green only if enabled-child-count > 0 and every enabled
child is a fresh PASS; amber if all enabled children pass but >=1 is
stale or >=1 is NOT RUN/CANCELLED; red if any enabled child is FAIL or
ERROR; running if any enabled child is RUNNING or QUEUED; grey if the
platform itself is disabled or has zero enabled children (a platform is
never green on zero enabled children). Disabled children are excluded
before any of these rules are evaluated.

Colour is never the only signal - every returned status carries a text
label, an icon name, and an accessible description string; the UI layer
(Phase 4) is responsible for actually rendering all three.
"""
from dataclasses import dataclass

from app.models.base import parse_iso, utcnow_iso
from app.models.datasource import (
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    STATUS_QUEUED,
    STATUS_RUNNING,
)

DISABLED = "DISABLED"

DATASOURCE_STATUS_META = {
    STATUS_PASS: {
        "label": "Pass",
        "icon": "check-circle",
        "description": "The last run of this data source's search returned at least one event.",
    },
    STATUS_FAIL: {
        "label": "Fail",
        "icon": "x-circle",
        "description": "The last run of this data source's search completed but returned zero events.",
    },
    STATUS_ERROR: {
        "label": "Error",
        "icon": "alert-triangle",
        "description": "The last run of this data source's search could not complete.",
    },
    STATUS_RUNNING: {
        "label": "Running",
        "icon": "spinner",
        "description": "This data source's validation search is currently executing.",
    },
    STATUS_QUEUED: {
        "label": "Queued",
        "icon": "clock",
        "description": "Waiting for the single validation worker to dispatch this search.",
    },
    STATUS_NOT_RUN: {
        "label": "Not Run",
        "icon": "minus-circle",
        "description": "This data source has not been validated yet.",
    },
    STATUS_CANCELLED: {
        "label": "Cancelled",
        "icon": "slash-circle",
        "description": "The validation run was cancelled before this data source completed.",
    },
    DISABLED: {
        "label": "Disabled",
        "icon": "circle-off",
        "description": "This data source is disabled and is excluded from its platform's status.",
    },
}

STALE_LABEL_SUFFIX = " (Stale)"
STALE_ICON = "check-circle-warning"
STALE_DESCRIPTION = (
    "The last known result was a pass, but it is older than the "
    "configured staleness threshold - it may no longer reflect reality."
)

RUNNING_OR_QUEUED = {STATUS_RUNNING, STATUS_QUEUED}
FAIL_OR_ERROR = {STATUS_FAIL, STATUS_ERROR}
NOT_RUN_OR_CANCELLED = {STATUS_NOT_RUN, STATUS_CANCELLED}

PLATFORM_GREEN = "green"
PLATFORM_AMBER = "amber"
PLATFORM_RED = "red"
PLATFORM_RUNNING = "running"
PLATFORM_GREY = "grey"

PLATFORM_STATUS_META = {
    PLATFORM_GREEN: {
        "label": "Healthy",
        "icon": "check-circle",
        "description": "Every enabled data source under this platform is a fresh pass.",
    },
    PLATFORM_AMBER: {
        "label": "Attention",
        "icon": "alert-circle",
        "description": "Every enabled data source is passing, but at least one result is stale, not yet run, or was cancelled.",
    },
    PLATFORM_RED: {
        "label": "Failing",
        "icon": "x-circle",
        "description": "At least one enabled data source under this platform is failing or errored.",
    },
    PLATFORM_RUNNING: {
        "label": "Validating",
        "icon": "spinner",
        "description": "A validation run is currently in progress for this platform.",
    },
    PLATFORM_GREY: {
        "label": "Inactive",
        "icon": "circle-off",
        "description": "This platform is disabled or has no enabled data sources.",
    },
}


@dataclass
class DisplayStatus:
    status: str
    stale: bool
    label: str
    icon: str
    description: str


def compute_datasource_display_status(datasource, now=None, default_stale_threshold_sec=None):
    """
    datasource: an app.models.datasource.DataSource (or anything with the
    same enabled / current_status / last_result_time / stale_threshold_sec
    attributes).
    now: a timezone-aware datetime to evaluate staleness against; defaults
    to the current UTC time. Injectable so tests are deterministic.
    default_stale_threshold_sec: fallback threshold (seconds) used when
    the data source has no per-object override.
    """
    if not datasource.enabled:
        meta = DATASOURCE_STATUS_META[DISABLED]
        return DisplayStatus(status=DISABLED, stale=False, **meta)

    raw_status = datasource.current_status or STATUS_NOT_RUN
    meta = DATASOURCE_STATUS_META.get(raw_status, DATASOURCE_STATUS_META[STATUS_NOT_RUN])
    label, icon, description = meta["label"], meta["icon"], meta["description"]

    stale = False
    if raw_status == STATUS_PASS:
        threshold = datasource.stale_threshold_sec
        if threshold is None:
            threshold = default_stale_threshold_sec
        if threshold is not None and datasource.last_result_time:
            evaluated_at = now or parse_iso(utcnow_iso())
            last_pass = parse_iso(datasource.last_result_time)
            if last_pass is not None and (evaluated_at - last_pass).total_seconds() > threshold:
                stale = True

    if stale:
        label = f"{meta['label']}{STALE_LABEL_SUFFIX}"
        icon = STALE_ICON
        description = STALE_DESCRIPTION

    return DisplayStatus(status=raw_status, stale=stale, label=label, icon=icon, description=description)


def compute_platform_status(platform, child_display_statuses):
    """
    platform: an app.models.platform.Platform (or anything with .enabled).
    child_display_statuses: the DisplayStatus for every data source under
    this platform (from compute_datasource_display_status), including
    disabled ones - they are filtered out here so callers don't have to
    pre-filter, matching "disabled children never affect platform status".
    """
    if not platform.enabled:
        return DisplayStatus(status=PLATFORM_GREY, stale=False, **PLATFORM_STATUS_META[PLATFORM_GREY])

    enabled_children = [c for c in child_display_statuses if c.status != DISABLED]
    if not enabled_children:
        # Never green on zero enabled children, even if the platform itself is enabled.
        return DisplayStatus(status=PLATFORM_GREY, stale=False, **PLATFORM_STATUS_META[PLATFORM_GREY])

    if any(c.status in RUNNING_OR_QUEUED for c in enabled_children):
        return DisplayStatus(status=PLATFORM_RUNNING, stale=False, **PLATFORM_STATUS_META[PLATFORM_RUNNING])

    if any(c.status in FAIL_OR_ERROR for c in enabled_children):
        return DisplayStatus(status=PLATFORM_RED, stale=False, **PLATFORM_STATUS_META[PLATFORM_RED])

    all_pass = all(c.status == STATUS_PASS for c in enabled_children)
    any_stale = any(c.stale for c in enabled_children)
    any_not_run_or_cancelled = any(c.status in NOT_RUN_OR_CANCELLED for c in enabled_children)

    if all_pass and not any_stale:
        return DisplayStatus(status=PLATFORM_GREEN, stale=False, **PLATFORM_STATUS_META[PLATFORM_GREEN])

    if (all_pass and any_stale) or any_not_run_or_cancelled:
        return DisplayStatus(status=PLATFORM_AMBER, stale=any_stale, **PLATFORM_STATUS_META[PLATFORM_AMBER])

    # Every enumerated data source status is covered by the branches
    # above; fail toward the most visible non-green state rather than
    # silently defaulting to green if a future status is added here
    # without updating this function.
    return DisplayStatus(status=PLATFORM_AMBER, stale=any_stale, **PLATFORM_STATUS_META[PLATFORM_AMBER])
