"""
solutions/base_solution.py

"Solution Applications (Plug-ins Built on the Platform)" from the
diagram — the abstract contract every business solution implements.

A solution owns the domain logic (e.g. PPEAssociator for industrial
safety) plus how raw tracked detections become violation events. It
does NOT own inference, tracking, alerting, or storage — those stay
in platform_core and are handed to the solution, not owned by it.
"""

from abc import ABC, abstractmethod


class BaseSolution(ABC):
    #: must be overridden — used as the DataManager "solution" column
    #: and to select the right model_config/rules.json at startup
    name: str = None

    #: Whether this solution needs person-tracking (ByteTracker) before
    #: evaluate() runs. PPE needs it (associate helmet/vest to a tracked
    #: person box). Driver monitoring does not — it has no person/driver
    #: class at all, just a single fixed cabin camera and direct
    #: per-class presence detection, so tracking would be wasted work.
    #: Default True preserves existing PPE behavior unchanged.
    requires_tracking: bool = True

    #: Single source of truth for EVERYTHING solution-specific the
    #: frontend and annotation drawing need -- branding, violation-type
    #: colors/icons/labels, upload-page copy. Read as a CLASS attribute
    #: (SolutionClass.manifest), no instantiation needed, since api/main.py
    #: serves it via GET /api/solutions/manifest without constructing the
    #: solution (which has side effects like loading rules.json).
    #: Replaces what used to be 4+ duplicated JS config objects
    #: (SOLUTION_LABELS, SOLUTION_META, IN_APP_BRANDING, SOLUTION_FILTERS,
    #: SOLUTION_UPLOAD_META) plus a second duplicate copy of violation-type
    #: colors in theme.js and a THIRD copy in core/annotation.py (BGR for
    #: burned-in video annotations). Shape:
    #:   {
    #:     "icon": "🛡️", "name": "...", "description": "...",
    #:     "sidebar_brand_html": "<span ...>...", "model_badge": "...",
    #:     "doc_title": "...",
    #:     "violation_types": {
    #:       "<violation_type_key>": {
    #:         "label": "...", "short_label": "..." (for video overlay),
    #:         "icon": "...", "tone": "danger"|"warn"|"gold"|"success",
    #:       }, ...
    #:     },
    #:     "upload": {
    #:       "badge": "...", "description": "...", "init_icon": "...",
    #:       "init_title": "...", "init_subtitle": "...",
    #:       "init_tags": [...], "inference_step_title": "...",
    #:       "inference_step_desc": "...", "capabilities": [...],
    #:     },
    #:   }
    manifest: dict = {}

    @abstractmethod
    def evaluate(self, tracked_detections: list) -> list:
        """
        tracked_detections: list of tracker output, e.g.
            [{"track_id": 1, "bbox": [...], "class": "person", ...}, ...]
            plus whatever raw class detections (helmet/vest/etc) the
            tracker pairs alongside them per your existing tracker.py
            output shape.

        Returns: list of confirmed violation events, e.g.
            [{"person_id": 1, "violation_type": "no_helmet", ...}, ...]

        Implementations should internally call their own association
        logic (e.g. PPEAssociator) + apply the solution's rules.json
        for what counts as a reportable event.
        """
        raise NotImplementedError

    def get_stats(self, smoothed: dict) -> dict:
        """Turns a solution's smoothed-status dict into the summary
        counters shown in the UI (TOTAL PERSONS / TOTAL VIOLATIONS).
        Default assumes one dict entry == one tracked person, which is
        correct for tracking-based solutions like PPE (len(smoothed) is
        genuinely the number of people in frame). Solutions that report
        multiple independent status channels per single subject (e.g.
        driver_monitoring's driver_eyes/driver_phone/driver_cigarette/
        driver_seatbelt) must override this, since len(smoothed) there
        would count channels, not people."""
        persons = len(smoothed)
        violations = sum(1 for s in smoothed.values() if s.get("violation"))
        return {"persons": persons, "violations": violations}

    @abstractmethod
    def get_class_names(self) -> list:
        """Ordered list of model output class names, e.g.
        ['helmet', 'person', 'vest'] — used by the inference backend
        to map class indices to names, and by the frontend to render
        correct labels/colors via rules.json."""
        raise NotImplementedError
