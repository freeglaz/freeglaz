# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Offline demo: a fake Z9 client with curated canned data.

Enabling demo mode (from the "Try demo" button) swaps ``app.state.z9`` for a
``MockZ9Client``. It duck-types the subset of ``Z9Client`` the web app calls so
the **Print screen works without a printer**: a loaded demo paper on a 24" roll,
ten healthy ink levels, a live status stream, geometry preview, and a simulated
print (paired with ``FREEGLAZ_MOCK_PRINT`` so nothing is sent).

Lives in ``webapp/`` (never in ``lib/z9_client/``): a pure additive demo layer
over the real interface — the engine is untouched.

Screens that need real device operations (paper CRUD, profiling, measurements,
the job queue) degrade gracefully: canned-empty rather than functional.
"""
from __future__ import annotations

from lib.z9_client.exceptions import Z9Error

# ── Curated demo state ────────────────────────────────────────────────────
_DEMO_PAPER_ID = "demo-glossy-roll-24"
_DEMO_PAPER_NAME = "Demo Glossy Photo — roll 24\""
_DEMO_ROLL_WIDTH_MM = 610.0  # 24 inch

# 10 channels (the real Z9 set), all healthy → no ink alerts in the demo.
_DEMO_INKS: list[tuple[str, float]] = [
    ("photo-black", 90.0),
    ("matte-black", 72.0),
    ("gray", 58.0),
    ("cyan", 74.0),
    ("magenta", 78.0),
    ("yellow", 82.0),
    ("chromatic-red", 66.0),
    ("chromatic-green", 63.0),
    ("chromatic-blue", 69.0),
    ("post-treatment", 45.0),  # gloss enhancer
]

_DEMO_IDENTIFICATION = {
    "ModelName": "HP DesignJet Z9 24in",
    "ModelNumber": "Z9 24in",
    "SerialNumber": "DEMO-0000000",
    "FwReleaseName": "demo",
    "PrinterName": "freeglaz demo printer",
}


def _assembled_status() -> dict:
    """What ``DeviceOps.status()`` returns — consumed by ``/api/status`` and by
    ``build_loaded_paper`` (geometry)."""
    return {
        "identification": dict(_DEMO_IDENTIFICATION),
        "loaded_paper_id": _DEMO_PAPER_ID,
        "loaded_paper_name": _DEMO_PAPER_NAME,
        "loaded_paper_source": "ROLL",
        "loaded_paper_source_label": "Roll",
        "loaded_paper_width_mm": _DEMO_ROLL_WIDTH_MM,
        "loaded_paper_length_mm": None,  # a roll has no fixed length
        "ink_levels": {c: lvl for c, lvl in _DEMO_INKS},
        "ink_warnings": [],
        "global_status": "Ready",
    }


def _raw_ink_system() -> dict:
    """Shape consumed by the SSE subscriber's ``_extract_inks``."""
    groups = [
        {
            "Color": color,
            "InkSlotGroupInfo": {
                "InkSupplyGroupInfo": {
                    "LevelPercentage": level,
                    "State": "OK",
                    "UserReportedStatus": "OK",
                }
            },
        }
        for color, level in _DEMO_INKS
    ]
    return {"InkSlotGroupCollection": {"InkSlotGroup": groups}}


def _raw_device_status() -> dict:
    """Shape consumed by ``_extract_global_status`` / ``_extract_activity``."""
    return {
        "StatusOverview": {"MostRelevantStatus": "Ready"},
        "ActivitiesOverview": {"MostRelevantActivity": {"Name": "NoActivity"}},
    }


def _raw_loaded_media_info() -> dict:
    """Shape consumed by ``_extract_loaded_paper``."""
    return {
        "media_id": _DEMO_PAPER_ID,
        "source": "ROLL",
        "source_label": "Roll",
        "width_mm": _DEMO_ROLL_WIDTH_MM,
        "length_mm": None,
    }


# ── Fake sub-namespaces (duck-type the real ops) ─────────────────────────
class _MockDevice:
    def status(self) -> dict:
        return _assembled_status()

    def device_status(self) -> dict:
        return _raw_device_status()

    def ink_system(self) -> dict:
        return _raw_ink_system()

    def loaded_media_info(self) -> dict:
        return _raw_loaded_media_info()


class _MockPaper:
    def get(self, ref):
        if ref == _DEMO_PAPER_ID:
            return {
                "id": _DEMO_PAPER_ID,
                "name": _DEMO_PAPER_NAME,
                "category_id": "DEMO",
                "category_name": "Demo",
                "is_factory": False,
                "is_user_custom": True,
            }
        return None

    def get_by_name(self, name):  # noqa: ARG002
        return self.get(_DEMO_PAPER_ID)

    def capabilities(self, ref):  # noqa: ARG002
        return {
            "supports_gloss_enhancer": True,
            "supports_max_detail": True,
            "supports_profiling": True,
        }

    def details(self, ref):  # noqa: ARG002
        return self.get(_DEMO_PAPER_ID)

    # Everything that mutates or fetches an ICC is unavailable in the demo.
    def _unavailable(self, *_a, **_k):
        raise Z9Error("Not available in demo mode")

    export_icc = _unavailable
    import_icc = _unavailable
    create = _unavailable
    delete = _unavailable
    delete_profile = _unavailable
    restore_default_preset = _unavailable
    set_mechanical_properties = _unavailable
    get_raw_xml = _unavailable


class _MockJobs:
    queue_uuid = "demo-queue-0000"

    def get_jobs_snapshot(self):
        return []

    def find_new_reprint_job(self, *_a, **_k):
        return None

    def get_job_preview(self, *_a, **_k):
        return None

    def _noop(self, *_a, **_k):
        return None

    cancel_job = _noop
    remove_job = _noop
    reprint_job = _noop
    clear_all = _noop
    pause_queue = _noop
    resume_queue = _noop


class _MockEvents:
    def event_table(self):
        return {}


class _MockLogs:
    def get_events(self, *_a, **_k):
        return []


class _MockSoap:
    def get_profile(self, *_a, **_k):
        raise Z9Error("Not available in demo mode")


class _MockPrint:
    def send(self, *_a, **_k):
        # In demo mode FREEGLAZ_MOCK_PRINT is set → the mock worker handles the
        # job and never calls this. Guard anyway.
        raise Z9Error("Demo mode: printing is simulated, nothing is sent")


class MockZ9Client:
    """Duck-typed stand-in for ``Z9Client`` serving curated demo data."""

    def __init__(self) -> None:
        self.host = "demo"
        self.name = "freeglaz demo printer"
        self.parent = None
        self.device = _MockDevice()
        self.paper = _MockPaper()
        self.jobs = _MockJobs()
        self.events = _MockEvents()
        self.logs = _MockLogs()
        self.soap = _MockSoap()
        self.print = _MockPrint()

    def identification(self) -> dict:
        return dict(_DEMO_IDENTIFICATION)

    def close(self) -> None:
        pass
