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

"""
Z9Client: main class to drive the HP DesignJet Z9.

Usage:
    from lib.z9_client import Z9Client

    # From environment variables (Z9_HOST, Z9_ADMIN_PWD)
    client = Z9Client.from_env()

    # Or explicitly
    client = Z9Client(host="192.168.1.50", admin_pwd="secret")

    # Machine identification
    info = client.identification()
    print(info["ModelName"])

    # Paper list
    papers = client.paper.list()
    for p in papers:
        print(f"{p['id']} : {p['name']}")

    # Global machine status
    status = client.status()
"""

import os
from .rest import PIWSClient
from .soap import SOAPClient
from . import parsers
from .exceptions import (
    Z9Error, Z9ConnectionError, Z9PaperError, Z9RESTError,
    Z9CalibrationError, Z9CalibrationTimeout,
)
from .jobqueue import JobQueueOps
from .printing import PrintOps


class Z9Client:
    """
    Main client for the HP DesignJet Z9.

    Composed of:
      - .rest        : raw REST PIWS access
      - .soap        : raw SOAP access (port 8085 + 8086)
      - .events      : reading LEDM endpoints /EventMgmt/* (native events)
      - .paper       : paper operations (list, create, calibrate, scan)
      - .device      : machine status, inks, alerts
      - .print       : print pipeline (TIFF/PDF -> PDF/X-4 -> PRN -> 9100)
      - .jobs        : queue management (list, cancel, pause, etc.)
    """

    def __init__(self, host, admin_pwd=None, timeout=10):
        """
        :param host: IP/hostname of the Z9 (e.g. "192.168.1.50")
        :param admin_pwd: Admin password for protected endpoints
                          (do not write in cleartext, use Z9_ADMIN_PWD env var)
        :param timeout: Default network timeout in seconds
        """
        self.host = host
        self.admin_pwd = admin_pwd
        self.timeout = timeout

        # Low level (directly reusable)
        self.rest = PIWSClient(host=host, admin_pwd=admin_pwd, timeout=timeout)
        self.soap = SOAPClient(host=host, timeout=timeout * 3)  # SOAP longer
        # Lazy import: avoids a cycle if events.py ever wants to import client.py
        from .events import LEDMEventReader
        self.events = LEDMEventReader(host=host, admin_pwd=admin_pwd, timeout=timeout)

        from .logs import ProductLogsReader
        self.logs = ProductLogsReader(host=host, timeout=timeout)

        # High level (business modules)
        self.paper = PaperOps(self)
        self.device = DeviceOps(self)
        self.print = PrintOps(self)
        self.jobs = JobQueueOps(self)

    @classmethod
    def from_env(cls):
        """
        Builds a Z9Client from environment variables.

        Variables read:
          - Z9_HOST       : IP or hostname (e.g. 192.168.1.50)
          - Z9_ADMIN_PWD  : admin password (optional)
          - Z9_TIMEOUT    : timeout in seconds (optional, default 10)

        Also loads a .env file at the root if python-dotenv is available.
        """
        # Attempt to use python-dotenv if installed
        try:
            from dotenv import load_dotenv
            # Look for .env at the project root (3 levels above this file)
            here = os.path.dirname(os.path.abspath(__file__))
            for upward in range(4):
                env_path = os.path.join(here, *[".."] * upward, ".env")
                env_path = os.path.normpath(env_path)
                if os.path.exists(env_path):
                    load_dotenv(env_path)
                    break
        except ImportError:
            pass  # dotenv not installed, we just read the system env

        # ACTIVE printer from the store.json registry (written by the web/desktop
        # onboarding). Read once — used as the fallback for BOTH host and admin_pwd.
        from . import cache
        active = cache.active_printer()

        host = os.getenv("Z9_HOST")
        if not host:
            # IP config fallback: ACTIVE printer from the store.json registry.
            # Global order: --host (CLI, handled before dispatch) > Z9_HOST (.env,
            # dev/override) > printers active (store.json) > not configured (we raise,
            # caught gracefully by the webapp lifespan -> z9=None, and by the CLI
            # -> config message + exit 2). Z9_HOST priority = dev unchanged.
            if active and active.get("ip"):
                host = active["ip"]
        if not host:
            raise Z9Error(
                "No printer configured. Set Z9_HOST (environment/.env) "
                "or register an active printer in store.json (IP configuration).\n"
                "Example: export Z9_HOST=192.168.1.50"
            )
        # Admin password (protected endpoints: admin settings + job queue). Same
        # priority as host: Z9_ADMIN_PWD env (.env/dev) > active printer in the
        # store (web/desktop onboarding). Lets a webapp-only user reach the job
        # queue without a .env.
        admin_pwd = os.getenv("Z9_ADMIN_PWD") or (active or {}).get("admin_pwd")
        timeout = int(os.getenv("Z9_TIMEOUT", "10"))
        return cls(host=host, admin_pwd=admin_pwd, timeout=timeout)

    def __repr__(self):
        auth_state = "auth" if self.admin_pwd else "no-auth"
        return f"<Z9Client host={self.host!r} {auth_state}>"

    # --- Convenience methods ---

    def ping(self):
        """Checks that the Z9 responds. Returns True/False."""
        return self.rest.ping()

    def identification(self):
        """Returns a dict with identification info (Model, S/N, etc.)."""
        xml = self.rest.get("/Identification.xml")
        data = parsers.parse_xml(xml)
        return data.get("Identification", {}).get("Fields", {})

    def status(self):
        """
        Returns a dict with a machine overview.
        Combines several REST endpoints for an overall view.
        """
        return self.device.status()

    def close(self) -> None:
        """Cleanly closes the HTTP keep-alive sessions (REST + SOAP).

        Called on FastAPI backend shutdown (cf. ``main.py lifespan``).
        Prevents zombie sessions from holding state (cookies,
        TLS cache, stale queue UUID) that pollutes the context if another
        freeglaz process restarts quickly on the same port.

        Idempotent: silently OK if already closed.
        """
        for client in (self.rest, self.soap):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — best-effort
                    pass


class PaperOps:
    """Business operations on papers (custom medium)."""

    def __init__(self, client):
        self._client = client

    def list(self):
        """
        Lists all papers (factory + custom) known by the Z9.

        Returns a list of dicts with:
          - id, name, category_id, donor_id, revision
          - is_factory, is_custom, is_visible, is_protected
          - properties (dict of any Properties)

        The donor_id for custom papers allows finding the donor HP
        paper (e.g. "4110" = "Fine Art Pearl Paper (less ink)").
        """
        xml = self._client.rest.get("/Paper/List")
        papers = parsers.parse_paper_list(xml)
        # Add a derived is_custom flag for convenience
        # We use is_user_custom (UUID 32 chars) rather than the plain
        # negation of is_factory, because some HP papers integrated
        # late have IsFactory=false in the XML while they are
        # actually HP papers (and not user creations).
        for p in papers:
            p["is_custom"] = p.get("is_user_custom", False)
        return papers

    def get(self, paper_id):
        """
        Retrieves a specific paper by its ID.

        :param paper_id: MediumId hex 32 chars (custom) or numeric ID (factory)
        :return: paper dict, or None if not found
        """
        for p in self.list():
            if p["id"] == paper_id:
                return p
        return None

    def get_by_name(self, name, exact=False):
        """
        Searches for a paper by its name (en_US).

        Matching:
          - If `exact=True`: strict case-insensitive match
          - Otherwise, two passes:
            1. Substring match first (old behavior, higher priority)
            2. If nothing, multi-word match: all words of the query present
               in the name (any order)

        Examples:
          - "Hahnemühle" -> matches "Hahnemühle Photo Rag 308g" (substring)
          - "Hahnemühle Baryta" -> matches "Hahnemühle Fine Art Baryta Satin"
            (multi-word, since "Hahnemühle" + "Baryta" both present)
          - "Canson Photo" -> matches the Canson Photolustre (direct substring)

        :param name: Name to search (case-insensitive)
        :param exact: If True, exact match only
        :return: list of matching papers (may be empty)
        """
        name_lower = name.lower().strip()
        if not name_lower:
            return []
        all_papers = self.list()

        # Exact match
        if exact:
            return [p for p in all_papers
                    if (p.get("name") or "").lower() == name_lower]

        # 1. Substring match priority (preserves the old semantics)
        substring_matches = [
            p for p in all_papers
            if name_lower in (p.get("name") or "").lower()
        ]
        if substring_matches:
            return substring_matches

        # 2. Otherwise, multi-word match: all words of the query must
        #    appear in the paper name (in any order).
        #    Allows "Hahnemühle Baryta" -> "Hahnemühle Fine Art Baryta Satin"
        query_words = name_lower.split()
        if len(query_words) < 2:
            return []  # a single word and no substring -> nothing more to do
        return [
            p for p in all_papers
            if all(w in (p.get("name") or "").lower() for w in query_words)
        ]

    def calibration_status(self):
        """
        Returns the CLC state of all papers from /Calibrations.json.

        Returns a dict {MediaID: {clc_status, overall_status, timestamp}}.
        """
        text = self._client.rest.get("/Calibrations.json")
        data = parsers.parse_json(text)
        result = {}
        medias = data.get("Calibrations", {}).get("MediaDependentCollection", {}).get("Media", [])
        for m in medias:
            mid = m.get("MediaID")
            if not mid:
                continue
            overall = m.get("UserReportedStatus", "Unknown")
            clc_status = None
            clc_ts = None
            for cal in m.get("Calibration", []):
                if cal.get("Type") == "CLC":
                    clc_status = cal.get("Status")
                    clc_ts = cal.get("TimeStamp")
                    break
            result[mid] = {
                "clc_status": clc_status,
                "overall_status": overall,
                "timestamp": clc_ts,
            }
        return result

    # --- SOAP read-only operations ---

    def soap_version(self):
        """
        Retrieves the version of the SOAP PaperManagement service (port 8085).
        Useful as a "SOAP ping" to check that the service responds.

        :return: str (e.g. "PM_SERVICE_VERSION_1.0")
        """
        return self._client.soap.get_service_version()

    def list_version(self):
        """
        Retrieves the current version of the paper list (SOAP).

        This version changes on each paper create/delete/modify.
        Useful to detect if the list has changed since a last check.

        :return: str (e.g. "MEDIUM_LIST_VERSION_137")
        """
        return self._client.soap.get_medium_list_version()

    def get_raw_xml(self, language="en_US"):
        """
        Retrieves the full paper list in SOAP format (verbose).

        Unlike `list()` which goes through REST (summary), this method
        returns the internal XML block of each paper (StarWheelPos, PenToRib,
        DryTime, TimeToReady, etc.) — useful for debug and exploration.

        :param language: language of the Localizations
        :return: raw XML str
        """
        return self._client.soap.get_medium_list(language=language)

    def list_full(self, language="en_US"):
        """
        Lists the papers with ALL details (via SOAP getMediumList).

        Much richer than `list()` (REST Paper/List):
          - Calibration date + obsolete
          - All ProfilingTickets (UUID, date, GE, ColorSpace)
          - Capabilities (cutter, GE, max detail, profiling, etc.)
          - Settings (StarWheel*, PenToRib, DryTime, etc.)
          - Details (grammage, inks used, donor)

        :param language: language of the Localizations
        :return: list of detailed dicts (cf. parsers.parse_soap_medium_list)
        """
        xml = self._client.soap.get_medium_list(language=language)
        return parsers.parse_soap_medium_list(xml)

    def details(self, paper_id, language="en_US"):
        """
        Retrieves ALL details of one particular paper.

        :param paper_id: MediumId (hex 32 or short numeric)
        :return: detailed paper dict, or None if not found
        """
        for p in self.list_full(language=language):
            if p["id"] == paper_id:
                return p
        return None

    def capabilities(self, paper_id, language="en_US"):
        """
        Retrieves the capabilities of a paper in a normalized format.

        Lightweight wrapper around `details()` that exposes the capabilities
        as Python booleans (None if information unavailable). Useful
        for the print pipeline and job parameter validation.

        The Z9 firmware stores capabilities as strings "1"/"0".
        This defensive method tests several possible field names to be
        robust to variations depending on firmware version.

        :param paper_id: MediumId of the paper
        :return: dict {
            'supports_gloss_enhancer': bool|None,
            'supports_max_detail': bool|None,
            'supports_cutter': bool|None,
            'supports_profiling': bool|None,
            'raw': dict,   # the raw capabilities dict, for debug
        } or None if paper not found
        """
        d = self.details(paper_id, language=language)
        if d is None:
            return None

        caps_raw = d.get("capabilities") or {}

        def _bool_or_none(*keys):
            """Looks up several key names, returns True/False/None."""
            for k in keys:
                v = caps_raw.get(k)
                if v is None:
                    continue
                if isinstance(v, bool):
                    return v
                if isinstance(v, str):
                    return v.strip() == "1"
                if isinstance(v, int):
                    return v == 1
            return None

        return {
            "supports_gloss_enhancer": _bool_or_none(
                "GlossEnhancerSupported",
                "SupportsGlossEnhancer",
                "GlossEnhancer",
            ),
            "supports_max_detail": _bool_or_none(
                "MaxDetailSupported",
                "MaximumDetailSupported",
                "SupportsMaxDetail",
                "MaxDetail",
            ),
            "supports_cutter": _bool_or_none(
                "CutterSupported",
                "SupportsCutter",
            ),
            "supports_profiling": _bool_or_none(
                "ProfilingSupported",
                "SupportsProfiling",
            ),
            "raw": caps_raw,
        }

    # --- SOAP write operations ---

    def create(self, name, donor, language="en_US"):
        """
        Creates a custom paper by cloning an existing paper (donor).

        :param name: Name of the new paper (will be in the Localization)
        :param donor: Can be:
                      - A MediumId (hex 32 chars OR short numeric 4 chars)
                      - A partial name of an existing paper (search)
        :param language: Language code of the Localization (en_US, fr_FR, ...)
        :return: dict with:
                   - "id" : new MediumId generated by the Z9 (hex 32 chars)
                   - "name" : name given
                   - "donor_id" : id of the donor used
                   - "donor_name" : name of the donor used
                   - "outcome" : "OK" on success
        :raises Z9PaperError: if donor not found or ambiguous
        :raises Z9SOAPFault: if the firmware refuses creation
        """
        # 1. Resolve the donor to a valid paper
        donor_paper = self._resolve_paper(donor)

        # 2. SOAP call newCustomMedium
        from .soap import NS_PM, NS_EO
        # IMPORTANT: we must use the full prefix "PaperManagement:"
        # NOT the short prefix "PM:" because the firmware wants an exact
        # namespace in the body.
        body_xml = (
            f'<PaperManagement:NEW-CUSTOM-MEDIUM-REQUEST '
            f'donorId="{donor_paper["id"]}">'
            f'<PaperManagement:Localizations language="{language}">'
            f'<EngineOperations:Name>{_xml_escape(name)}</EngineOperations:Name>'
            f'</PaperManagement:Localizations>'
            f'</PaperManagement:NEW-CUSTOM-MEDIUM-REQUEST>'
        )
        xml_response = self._client.soap.call_paper_mgmt(
            action="newCustomMedium",
            body_xml=body_xml,
            namespaces={
                "PaperManagement": NS_PM,
                "EngineOperations": NS_EO,
            },
        )

        # 3. Parse the response to retrieve the new MediumId
        from .soap import find_body_element, _strip_namespace
        elem = find_body_element(xml_response, expected_local_name="NEW-CUSTOM-MEDIUM-RESPONSE")
        if elem is None:
            raise Z9Error("Empty response for newCustomMedium")
        outcome = elem.get("outcome", "?")
        if outcome != "OK":
            raise Z9Error(f"newCustomMedium failed: outcome={outcome}")

        # The new MediumId is in the response XML, either as an attribute or
        # in <Medium><MediumId>...</MediumId></Medium>
        new_id = None
        for sub in elem.iter():
            tag = _strip_namespace(sub.tag)
            if tag == "MediumId":
                text = (sub.text or "").strip()
                if text:
                    new_id = text
                    break

        return {
            "id": new_id,
            "name": name,
            "donor_id": donor_paper["id"],
            "donor_name": donor_paper.get("name", "?"),
            "outcome": outcome,
        }

    def delete(self, ref):
        """
        Deletes a custom paper from the Z9.

        Uses SOAP `deleteCustomMedium` on port 8085 (PaperManagement).
        The REST `Paper/Delete` documented for other Z9 firmwares does not
        exist on this firmware — all actions go through SOAP.

        :param ref: MediumId hex 32, short numeric MediumId, or partial name
        :return: dict with:
                   - "id" : MediumId of the deleted paper
                   - "name" : name of the deleted paper
                   - "outcome" : "OK" on success
        :raises Z9PaperError: if paper not found / ambiguous / factory protected
        :raises Z9SOAPFault: if the firmware refuses deletion

        Safety note: we REFUSE to delete a factory paper
        (factory papers are protected firmware-side, but we double-check
        client-side to avoid making the useless request) and the paper
        currently loaded on the roll.
        """
        # 1. Resolve the reference to a concrete paper
        paper = self._resolve_paper(ref)

        # 2. Guard: refuse factory papers
        if paper.get("is_factory", False):
            raise Z9PaperError(
                f"Refusing to delete a factory paper: '{paper['name']}' "
                f"(id={paper['id']}). HP factory papers are protected."
            )

        # 3. Guard: refuse to delete the currently loaded paper
        try:
            loaded_id = self._client.device.loaded_media_id()
            if loaded_id and loaded_id == paper["id"]:
                raise Z9PaperError(
                    f"Refusing to delete '{paper['name']}': it is the paper "
                    f"currently loaded on the roll."
                )
        except Z9PaperError:
            raise
        except Exception:
            # If we can't determine the loaded paper, we continue.
            # The firmware will refuse itself if necessary.
            pass

        # 4. SOAP call deleteCustomMedium (port 8085)
        # IMPORTANT: the MediumId must be passed as a child element, NOT as an
        # attribute. If we put mediumId="..." as an attribute, the firmware replies
        # outcome="OK" but deletes nothing (internal parser does not extract
        # the ID when it is an attribute).
        from .soap import NS_PM, find_body_element
        body_xml = (
            f'<PaperManagement:DELETE-CUSTOM-MEDIUM-REQUEST>'
            f'<PaperManagement:MediumId>{paper["id"]}</PaperManagement:MediumId>'
            f'</PaperManagement:DELETE-CUSTOM-MEDIUM-REQUEST>'
        )
        xml_response = self._client.soap.call_paper_mgmt(
            action="deleteCustomMedium",
            body_xml=body_xml,
            namespaces={"PaperManagement": NS_PM},
        )

        # 5. Parse the response to check outcome="OK"
        elem = find_body_element(xml_response, expected_local_name="DELETE-CUSTOM-MEDIUM-RESPONSE")
        if elem is None:
            raise Z9Error("Empty response for deleteCustomMedium")
        outcome = elem.get("outcome", "?")
        if outcome != "OK":
            raise Z9PaperError(f"deleteCustomMedium failed: outcome={outcome}")

        # 6. Post-deletion check: the firmware can reply OK
        # without having actually deleted (seen on this firmware when the MediumId
        # is passed as an attribute). We check that the version has changed OR that
        # the paper no longer appears in the list.
        try:
            still_exists = self._client.paper.get(paper["id"]) is not None
            if still_exists:
                raise Z9PaperError(
                    f"The firmware replied outcome=OK but the paper "
                    f"'{paper['name']}' ({paper['id']}) is still present. "
                    f"Suspected firmware bug."
                )
        except Z9PaperError:
            raise
        except Exception:
            # If the check fails for another reason, we log but do not
            # block (the firmware may have deleted anyway)
            pass

        return {
            "id": paper["id"],
            "name": paper.get("name", "?"),
            "outcome": outcome,
        }

    def _resolve_paper(self, ref):
        """
        Resolves a flexible reference to an existing paper.

        :param ref: MediumId (hex 32 or short numeric) OR partial name
        :return: dict of the found paper
        :raises Z9PaperError: if not found or ambiguous (no user interaction)
        """
        ref = (ref or "").strip()
        if not ref:
            raise Z9PaperError("Empty paper reference")

        papers = self.list()

        # Case 1: MediumId hex 32 chars
        if len(ref) == 32 and all(c in "0123456789ABCDEFabcdef" for c in ref):
            ref_upper = ref.upper()
            for p in papers:
                if p["id"].upper() == ref_upper:
                    return p
            raise Z9PaperError(f"MediumId hex '{ref}' not found")

        # Case 2: short numeric MediumId (factory)
        if ref.isdigit():
            for p in papers:
                if p["id"] == ref:
                    return p
            raise Z9PaperError(f"Numeric MediumId '{ref}' not found")

        # Case 3: partial name (case-insensitive)
        # 2-pass strategy: first direct substring, then multi-word
        # (all words present in any order).
        ref_lower = ref.lower()
        matches = [
            p for p in papers
            if ref_lower in (p.get("name") or "").lower()
        ]
        if not matches:
            # Multi-word fallback: "Hahnemühle Baryta" -> "Hahnemühle Fine Art Baryta Satin"
            query_words = ref_lower.split()
            if len(query_words) >= 2:
                matches = [
                    p for p in papers
                    if all(w in (p.get("name") or "").lower() for w in query_words)
                ]
        if not matches:
            raise Z9PaperError(f"No paper matches '{ref}'")
        if len(matches) > 1:
            names = ", ".join(f"'{m['name']}' ({m['id']})" for m in matches[:5])
            raise Z9PaperError(
                f"Ambiguous reference '{ref}' matches {len(matches)} papers: "
                f"{names}{'...' if len(matches) > 5 else ''}"
            )
        return matches[0]

    def calibrate(self, ref, on_progress=None, poll_interval=10,
                  timeout=1200, user="freeglaz"):
        """
        Launches a CLC (Color Linearization Calibration) on a paper
        and polls until the end (blocking).

        Full workflow (duration ~5-10 min depending on paper and phase):
          1. newCalibration -> OperationId
          2. Polling getStatus every `poll_interval` seconds
          3. End detection (DONE or job purged)
          4. Post-action check via paper.details()

        :param ref: MediumId (hex 32 or short numeric) OR partial name
        :param on_progress: optional callback (dict) called at each poll
                            dict received: {
                                "operation_id": str,
                                "elapsed": float (seconds),
                                "percent": int (-1 if N/A),
                                "process": str ("PRINTING", "DRYING", ...),
                                "outcome": str,
                            }
        :param poll_interval: seconds between polls (10s default, cadence
                              suited to the calibration workflow)
        :param timeout: max seconds before abandoning (default 20 min)
        :param user: username displayed Z9-side

        :return: dict with:
                   - "operation_id" : str
                   - "elapsed" : float (seconds)
                   - "final_state" : str (last observed process)
                   - "calibration_date" : str (date stored in the Z9 database)
                   - "calibration_valid" : bool (False if OBSOLETE after the op)

        :raises Z9PaperError: if paper not found or ambiguous
        :raises Z9CalibrationError: if calibration refused or failed
        :raises Z9CalibrationTimeout: if timeout exceeded
        """
        import time as _time

        # 1. Resolve the reference
        paper = self._resolve_paper(ref)
        medium_id = paper["id"]

        # 2. Launch newCalibration
        try:
            launch = self._client.soap.new_calibration(
                medium_id=medium_id,
                user=user,
            )
        except Z9Error as e:
            raise Z9CalibrationError(f"newCalibration failed: {e}")

        operation_id = launch["operation_id"]
        outcome = launch["outcome"]

        if operation_id == "-1" or outcome != "OK":
            # Immediate firmware refusal. Structured data for the CLI.
            from . import outcomes
            raise Z9CalibrationError(
                message=f"Calibration refused: {outcome}",
                code=outcome,
                description=outcomes.describe(outcome),
                context={
                    "paper_name": paper.get("name"),
                    "paper_id": medium_id,
                },
            )

        # 3. Polling (factored out)
        try:
            polling_result = self._poll_workflow(
                operation_id=operation_id,
                workflow_name="Calibration",
                on_progress=on_progress,
                poll_interval=poll_interval,
                timeout=timeout,
                error_context={
                    "paper_name": paper.get("name"),
                    "paper_id": medium_id,
                },
                error_class=Z9CalibrationError,
                timeout_class=Z9CalibrationTimeout,
            )
        except KeyboardInterrupt:
            raise

        final_state = polling_result["final_state"]
        elapsed = polling_result["elapsed"]

        # 4. Check via paper.details() that the calibration was indeed updated
        details = self.details(medium_id)
        if not details:
            raise Z9CalibrationError(
                f"Paper {medium_id} not found after calibration. "
                f"Inconsistent state."
            )

        cal_info = details.get("calibration") or {}
        cal_date = cal_info.get("date")
        cal_obsolete = cal_info.get("obsolete", False)

        # If the date was updated, it is a success
        import datetime as _dt
        today_str = _dt.date.today().isoformat()
        success = (cal_date == today_str) and not cal_obsolete

        if not success:
            raise Z9CalibrationError(
                f"Calibration was not recorded correctly. "
                f"Stored date: {cal_date}, obsolete={cal_obsolete}, "
                f"final_state={final_state}"
            )

        return {
            "operation_id": operation_id,
            "elapsed": elapsed,
            "final_state": final_state,
            "calibration_date": cal_date,
            "calibration_valid": not cal_obsolete,
            "paper_name": paper.get("name"),
        }

    def _poll_workflow(self, operation_id, workflow_name, on_progress,
                       poll_interval, timeout, error_context,
                       error_class, timeout_class):
        """
        Generic polling of an asynchronous SOAP workflow (calibration, profile).

        Strategy:
          - Poll getStatus every `poll_interval` seconds
          - Detect terminal states: DONE, UNKNOWN-OPERATION (purged), FAILED
          - Call on_progress at each poll if provided
          - Raise a structured exception on error

        :param operation_id: OperationId of the running workflow
        :param workflow_name: "Calibration" or "Profile" — for the messages
        :param on_progress: dict-status callback or None
        :param poll_interval: seconds between polls
        :param timeout: max seconds
        :param error_context: context dict for the exceptions
        :param error_class: exception class for business error
        :param timeout_class: exception class for timeout
        :return: dict {"final_state": str, "elapsed": float}
        """
        import time as _time
        start_time = _time.time()
        final_state = None

        while True:
            elapsed = _time.time() - start_time
            if elapsed > timeout:
                raise timeout_class(
                    message=f"Timeout after {timeout}s on Operation {operation_id}",
                    code="TIMEOUT",
                    description=f"The {workflow_name} workflow exceeded "
                                f"{timeout}s. It is probably still running on the Z9.",
                    context=error_context,
                )

            try:
                status = self._client.soap.get_status(operation_id)
            except Z9Error:
                # SOAP error during polling = potentially OK
                # (the Z9 may have purged the operation after success)
                final_state = "purged"
                break

            process = status["process"]
            outcome = status["outcome"]

            # User callback
            if on_progress:
                on_progress({
                    "operation_id": operation_id,
                    "elapsed": elapsed,
                    "percent": status["percent"],
                    "process": process,
                    "outcome": outcome,
                })

            # Terminal cases
            if process == "DONE":
                final_state = "DONE"
                break
            if outcome == "UNKNOWN-OPERATION":
                # Job purged after termination (success or failure)
                # -> we will check the paper state afterwards
                final_state = "purged"
                break
            if process == "FAILED" or outcome not in ("OK", "UNKNOWN-OPERATION"):
                from . import outcomes
                raise error_class(
                    message=f"{workflow_name} failed in phase {process}",
                    code=outcome,
                    description=outcomes.describe(outcome),
                    context={**error_context, "phase": process},
                )

            _time.sleep(poll_interval)

        return {
            "final_state": final_state,
            "elapsed": _time.time() - start_time,
        }

    def profile(self, ref, gloss_enhancer="FULLPAGE", quality="BEST",
                max_detail="OFF", color_space="RGB",
                profile_name=None, workflow_kind="PRINT_AND_SCAN",
                on_progress=None, poll_interval=10, timeout=1500,
                user="freeglaz"):
        """
        Launches a full ICC profiling workflow (PRINT_AND_SCAN by default).

        Full workflow (duration ~10 min depending on paper and quality):
          1. newProfile -> OperationId
          2. Polling getStatus every `poll_interval` seconds
          3. The Z9 prints the 464-patch chart, dries, scans, computes
          4. Post-action check: new ProfilingTicket in the database

        :param ref: MediumId (hex 32 or short numeric) OR partial name
        :param gloss_enhancer: "FULLPAGE" (HP default) | "OFF"
        :param quality: "BEST" (HP default) | "NORMAL" | "FAST"
        :param max_detail: "OFF" (HP default) | "ON"
        :param color_space: "RGB" (HP default) | "GRAYSCALE"
        :param profile_name: name of the ICC profile. If None, auto-generated in
                             HP format: "HP_<paper_no_spaces>_<GEON|GEOFF>, GE <ON|OFF>"
        :param workflow_kind: "PRINT_AND_SCAN" (default) | "PRINT_ONLY" | "SCAN_ONLY"
        :param on_progress: callback (dict) called at each poll
        :param poll_interval: seconds between polls (10s default)
        :param timeout: max seconds (1500s = 25 min default)
        :param user: username displayed Z9-side

        :return: dict with:
                   - "operation_id" : str
                   - "elapsed" : float (seconds)
                   - "final_state" : str
                   - "profile_name" : str (name used)
                   - "profile_uuid" : str|None (UUID of the new profile)
                   - "profile_icc_name" : str|None (internal name of the ICC)
                   - "paper_name" : str

        :raises Z9PaperError: paper not found
        :raises Z9CalibrationError: workflow refused or failed
        :raises Z9CalibrationTimeout: timeout exceeded
        """
        # 1. Resolve the reference
        paper = self._resolve_paper(ref)
        medium_id = paper["id"]

        # 2. Auto-generate the profile_name if not provided (faithful HP format)
        if profile_name is None:
            paper_name = paper.get("name", "Unknown")
            # HP format: remove spaces from the name
            paper_no_spaces = paper_name.replace(" ", "")
            # GE suffix depending on the mode
            if gloss_enhancer == "FULLPAGE":
                profile_name = f"HP_{paper_no_spaces}_GEON, GE ON"
            else:
                profile_name = f"HP_{paper_no_spaces}_GEOFF, GE OFF"

        # 3. Launch newProfile
        try:
            launch = self._client.soap.new_profile(
                medium_id=medium_id,
                profile_name=profile_name,
                quality=quality,
                gloss_enhancer=gloss_enhancer,
                max_detail=max_detail,
                color_space=color_space,
                workflow_kind=workflow_kind,
                user=user,
            )
        except Z9Error as e:
            raise Z9CalibrationError(f"newProfile failed: {e}")

        operation_id = launch["operation_id"]
        outcome = launch["outcome"]

        if operation_id == "-1" or outcome != "OK":
            # Immediate firmware refusal
            from . import outcomes
            raise Z9CalibrationError(
                message=f"Profiling refused: {outcome}",
                code=outcome,
                description=outcomes.describe(outcome),
                context={
                    "paper_name": paper.get("name"),
                    "paper_id": medium_id,
                    "profile_name": profile_name,
                    "workflow_kind": workflow_kind,
                },
            )

        # 4. Polling (factored out with calibrate)
        polling_result = self._poll_workflow(
            operation_id=operation_id,
            workflow_name="Profilage",
            on_progress=on_progress,
            poll_interval=poll_interval,
            timeout=timeout,
            error_context={
                "paper_name": paper.get("name"),
                "paper_id": medium_id,
                "profile_name": profile_name,
            },
            error_class=Z9CalibrationError,
            timeout_class=Z9CalibrationTimeout,
        )

        final_state = polling_result["final_state"]
        elapsed = polling_result["elapsed"]

        # 5. Check that a new ProfilingTicket exists with today's date
        details = self.details(medium_id)
        if not details:
            raise Z9CalibrationError(
                f"Paper {medium_id} not found after profiling. "
                f"Inconsistent state."
            )

        # Find the profile matching our Key (GE + ColorSpace) with
        # the most recent date
        import datetime as _dt
        today_str = _dt.date.today().isoformat()
        ge_token = gloss_enhancer  # "FULLPAGE" or "OFF"

        # The ColorSpace is stored as "PRINTER_RGB" firmware-side (vs "RGB" in input)
        cs_token = "PRINTER_RGB" if color_space == "RGB" else color_space

        new_profile = None
        for prof in details.get("profiles", []):
            # We look for the profile that matches our Key
            if (prof.get("gloss_enhancer") == ge_token
                    and prof.get("color_space") == cs_token
                    and prof.get("custom")):
                if prof.get("date") == today_str:
                    new_profile = prof
                    break

        if not new_profile:
            raise Z9CalibrationError(
                f"Profiling completed but no new custom profile found "
                f"for Key(GE={ge_token}, ColorSpace={cs_token}) "
                f"with date {today_str}. final_state={final_state}"
            )

        return {
            "operation_id": operation_id,
            "elapsed": elapsed,
            "final_state": final_state,
            "profile_name": profile_name,
            "profile_uuid": new_profile.get("uuid"),
            "profile_icc_name": new_profile.get("icc_name"),
            "profile_date": new_profile.get("date"),
            "gloss_enhancer": gloss_enhancer,
            "color_space": color_space,
            "paper_name": paper.get("name"),
            "paper_id": medium_id,
        }

    def print_only(self, ref, **kwargs):
        """Wrapper over profile() with workflow_kind='PRINT_ONLY'."""
        kwargs["workflow_kind"] = "PRINT_ONLY"
        return self.profile(ref, **kwargs)

    def scan_only(self, ref, **kwargs):
        """Wrapper over profile() with workflow_kind='SCAN_ONLY'."""
        kwargs["workflow_kind"] = "SCAN_ONLY"
        return self.profile(ref, **kwargs)

    def set_mechanical_properties(
        self, medium_id, pen_to_rib="LOW", dry_time_factor=100,
        star_wheels="INTERMEDIATE", cutter=1,
    ):
        """Modifies the 4 mechanical properties of a custom paper (bit-perfect HP DJ Utility)."""
        sw = "".join(
            f"<EO:StarWheelPos{p}>{star_wheels}</EO:StarWheelPos{p}>"
            for p in ["LowerRoll", "LowerRoll1", "LowerRoll2",
                       "HigherRoll", "HigherRoll1", "HigherRoll2"]
        )
        body = (
            f'<PM:SET-MEDIUM-PROPERTIES-REQUEST>'
            f'<PM:MediumId>{medium_id}</PM:MediumId>'
            f'<PM:Properties GlossEnhancerVolume="100" InkVolume="100">'
            f'{sw}'
            f'<EO:PenToRib>{pen_to_rib}</EO:PenToRib>'
            f'<EO:DryTimeFactor>{dry_time_factor}</EO:DryTimeFactor>'
            f'<EO:Cutter>{cutter}</EO:Cutter>'
            f'</PM:Properties>'
            f'</PM:SET-MEDIUM-PROPERTIES-REQUEST>'
        )
        xml = self._client.soap.call_paper_mgmt("setMediumProperties", body)
        if 'outcome="OK"' not in xml:
            raise Z9PaperError(f"setMediumProperties failed: {xml[:300]}")
        return True

    @staticmethod
    def build_cutter_only_body(medium_id, enabled):
        """Builds the `setMediumProperties` body that sets ONLY the horizontal
        cutter (`EO:Cutter`), WITHOUT any other mechanical setting.

        Relies on the firmware **MERGE** behavior (empirically validated
        on 26/05 on 2 papers, including the Canson Photolustre RC): a `Properties`
        containing only one field does not reset the others. So we send
        ONLY `<EO:Cutter>` ->

          - never `PenToRib` (suspect of the firmware crash `BABYSIT_PROCESS_LOST`
            of 26/05 — drives a head-height motor);
          - never `StarWheelPos*`/`DryTimeFactor` nor the `InkVolume`/
            `GlossEnhancerVolume` attributes (which `set_mechanical_properties` forces to 100);
          - never `ManualAdvanceAdjustment` (durable ADVANCE setting -> banding;
            NOT to be confused with the cutter despite the XSD adjacency).

        `enabled=False` -> `Cutter=0` (horizontal cutter OFF). Pure (no network)
        -> usable in dry-run. `yCutter` is intentionally absent (vertical
        cutter = Z9+ 44in only, not applicable on the 24in).
        """
        cutter = 1 if enabled else 0
        return (
            f'<PM:SET-MEDIUM-PROPERTIES-REQUEST>'
            f'<PM:MediumId>{medium_id}</PM:MediumId>'
            f'<PM:Properties>'
            f'<EO:Cutter>{cutter}</EO:Cutter>'
            f'</PM:Properties>'
            f'</PM:SET-MEDIUM-PROPERTIES-REQUEST>'
        )

    def set_cutter(self, medium_id, enabled):
        """Enables/disables the horizontal cutter of a custom paper — cutter-only.

        HARDWARE ACT. Touches NO other mechanical setting (cf.
        `build_cutter_only_body`: firmware MERGE). Known side effect (Trace
        26/05 §3.4): any successful `setMediumProperties` increments `Version` and
        sets the paper CLC to `obsolete="1"` (the physical CLC is NOT
        modified; the flag is based on `MediaChecksum`, not on the values). The
        caller decides whether it then restores `set_cutter(medium_id, True)`.

        :param enabled: True = cutter ON (HP default); False = OFF (no-cut).
        :return: True if outcome="OK".
        """
        body = self.build_cutter_only_body(medium_id, enabled)
        xml = self._client.soap.call_paper_mgmt("setMediumProperties", body)
        if 'outcome="OK"' not in xml:
            raise Z9PaperError(f"setMediumProperties(Cutter) failed: {xml[:300]}")
        return True

    def restore_default_preset(self, medium_id):
        """Restores the Variable properties of a custom paper to the donor values."""
        body = (
            f'<PM:SET-DEFAULT-PAPER-PRESET-REQUEST>'
            f'<PM:MediumId>{medium_id}</PM:MediumId>'
            f'</PM:SET-DEFAULT-PAPER-PRESET-REQUEST>'
        )
        xml = self._client.soap.call_paper_mgmt("setDefaultPaperPresets", body)
        if 'outcome="OK"' not in xml:
            raise Z9PaperError(f"setDefaultPaperPreset failed: {xml[:300]}")
        return True

    # Mapping of high-level parameters -> REST PIWS tokens (legacy, to avoid)
    # NOTE: these tokens are actually INCONSISTENT with the Z9 firmware — the REST
    # PIWS Paper/GetResource ignores selector-gloss-enhancer and always falls back
    # on the "default" profile. Kept for possible future compat.
    _PIWS_QUALITY_MAP = {
        "BEST":   "quality-best",
        "NORMAL": "quality-normal",
        "FAST":   "quality-fast",
    }

    _PIWS_GLOSS_MAP = {
        "FULLPAGE":   "gloss-enhancer-full-page",
        "OFF":        "gloss-enhancer-off",
        "INKEDAREA":  "gloss-enhancer-inked-area",
    }

    def export_icc(self, ref, output_path,
                   gloss_enhancer="FULLPAGE", quality="BEST",
                   color_space="RGB", _pre_resolved=None):
        """
        Exports an ICC profile (factory or custom) to a local file.

        Uses SOAP getProfile (port 8085) — this is the ONLY method that
        correctly returns custom profiles. The REST PIWS
        Paper/GetResource is limited to factory profiles (it ignores
        the GlossEnhancer selector and always falls back on GEOFF).

        :param ref: MediumId or partial name of the paper
        :param output_path: local path to save the .icc
        :param gloss_enhancer: "FULLPAGE" | "OFF" — must match an existing profile
        :param quality: "BEST" | "NORMAL" | "FAST" — used for the file name
                        (SOAP getProfile does not accept this parameter)
        :param color_space: "RGB" | "GRAYSCALE" — translated to PRINTER_* SOAP-side
        :param _pre_resolved: optional — already-resolved paper dict (with
                              at least ``id`` and ``name``). If provided, we
                              short-circuit ``_resolve_paper(ref)`` (which
                              does a costly REST ``list()``). Useful for
                              looped calls (mirror sync).

        :return: dict with:
                   - "output_path"  : str — path of the saved file
                   - "size_bytes"   : int — file size
                   - "paper_name"   : str
                   - "paper_id"     : str
                   - "md5"          : str — MD5 of the ICC file (for verification)

        :raises Z9PaperError: paper not found
        :raises Z9CalibrationError: if no profile for these parameters
        """
        import os
        import hashlib

        # 1. Resolve the reference (or short-circuit if already resolved)
        paper = _pre_resolved if _pre_resolved is not None else self._resolve_paper(ref)
        medium_id = paper["id"]

        # 2. Map ColorSpace input -> SOAP token (PRINTER_<X>)
        if color_space == "RGB":
            soap_cs = "PRINTER_RGB"
        elif color_space == "GRAYSCALE":
            soap_cs = "PRINTER_GRAYSCALE"
        else:
            raise Z9PaperError(f"Invalid ColorSpace: '{color_space}'")

        if gloss_enhancer not in ("FULLPAGE", "OFF"):
            raise Z9PaperError(f"Invalid GlossEnhancer: '{gloss_enhancer}'")

        # 3. SOAP call getProfile
        try:
            result = self._client.soap.get_profile(
                medium_id=medium_id,
                gloss_enhancer=gloss_enhancer,
                color_space=soap_cs,
            )
        except Z9Error as e:
            raise Z9CalibrationError(f"getProfile SOAP failed: {e}")

        outcome = result.get("outcome")
        icc_bytes = result.get("icc_bytes")

        if outcome != "OK" or not icc_bytes:
            from . import outcomes
            raise Z9CalibrationError(
                message=f"ICC export failed: outcome={outcome}",
                code=outcome or "?",
                description=outcomes.describe(outcome) if outcome else
                    "No ICC profile returned for these parameters.",
                context={
                    "paper_name": paper.get("name"),
                    "paper_id": medium_id,
                    "gloss_enhancer": gloss_enhancer,
                    "color_space": soap_cs,
                },
            )

        # 4. Save locally
        output_path = os.path.abspath(os.path.expanduser(output_path))
        with open(output_path, "wb") as f:
            f.write(icc_bytes)

        # 5. MD5 for traceability
        md5 = hashlib.md5(icc_bytes).hexdigest()

        return {
            "output_path": output_path,
            "size_bytes": len(icc_bytes),
            "md5": md5,
            "paper_name": paper.get("name"),
            "paper_id": medium_id,
            "gloss_enhancer": gloss_enhancer,
            "color_space": color_space,
        }

    def import_icc(self, ref, icc_path, icc_name=None,
                   gloss_enhancer="FULLPAGE", quality="BEST",
                   maximum_detail="OFF", color_space="RGB",
                   auto_backup=True):
        """
        Imports an external ICC profile to the Z9 via SOAP setProfile.

        Importing an ICC goes **EXCLUSIVELY through the SOAP port 8085** (setProfile),
        and not through REST PIWS Paper/SetResource.

        Endpoint : POST http://192.168.1.50:8085/MManApi/Query
        Action   : "http://www.bpo.hp.com/PaperManagement/setProfile"

        Z9 response: 200 OK with <SET-PROFILE-RESPONSE outcome="OK"/>

        :param ref: MediumId or partial name of the paper
        :param icc_path: local path to the ICC file to import
        :param icc_name: name to associate with the profile in the Z9 (visible in paper show).
                         If None, derived from the file name.
        :param gloss_enhancer: "FULLPAGE" | "OFF" — Key for which to install the profile
        :param quality: "BEST" | "NORMAL" | "FAST"
        :param maximum_detail: "OFF" | "ON"
        :param color_space: "RGB" | "GRAYSCALE" -> mapped to PRINTER_RGB / PRINTER_GRAYSCALE

        :return: dict with:
                   - "paper_name", "paper_id"
                   - "icc_path", "icc_size_bytes", "icc_md5"
                   - "icc_name"     : name used for ProfilingTicket
                   - "ticket_date"  : timestamp sent
                   - "outcome"      : firmware code (OK or other)
                   - "had_existing" : bool
                   - "existing_uuid": str or None

        :raises Z9PaperError: paper not found or invalid parameters
        :raises Z9CalibrationError: if the Z9 returns outcome != OK
        :raises FileNotFoundError: ICC source not found
        """
        import os
        import hashlib
        from datetime import datetime

        # 1. Check the ICC file
        icc_path = os.path.abspath(os.path.expanduser(icc_path))
        if not os.path.isfile(icc_path):
            raise FileNotFoundError(f"ICC file not found: {icc_path}")

        with open(icc_path, "rb") as f:
            icc_bytes = f.read()

        if len(icc_bytes) < 128 or icc_bytes[36:40] != b"acsp":
            raise Z9PaperError(
                f"File not valid as ICC: {icc_path} "
                f"('acsp' signature missing at offset 36)"
            )

        icc_md5 = hashlib.md5(icc_bytes).hexdigest()

        # 2. Resolve the paper reference
        paper = self._resolve_paper(ref)
        medium_id = paper["id"]

        # 3. Validate the parameters (the SOAP values, not REST!)
        if quality not in ("BEST", "NORMAL", "FAST"):
            raise Z9PaperError(f"Invalid Quality: '{quality}'")
        if gloss_enhancer not in ("FULLPAGE", "OFF"):
            raise Z9PaperError(f"Invalid GlossEnhancer: '{gloss_enhancer}'")
        if maximum_detail not in ("ON", "OFF"):
            raise Z9PaperError(f"Invalid MaximumDetail: '{maximum_detail}'")
        if color_space == "RGB":
            soap_cs = "PRINTER_RGB"
        elif color_space == "GRAYSCALE":
            soap_cs = "PRINTER_GRAYSCALE"
        else:
            raise Z9PaperError(f"Invalid ColorSpace: '{color_space}'")

        # 4. Derive the profile name if not provided
        if not icc_name:
            base = os.path.splitext(os.path.basename(icc_path))[0]
            icc_name = base[:63]  # ICC V2 desc limit (textDescriptionTag; Argyll = V2 only)

        # 5. Detect if a profile already exists for this Key
        had_existing = False
        existing_uuid = None
        existing_prof_full = None
        try:
            details = self.details(medium_id)
            for prof in details.get("profiles") or []:
                if (prof.get("gloss_enhancer") == gloss_enhancer
                        and prof.get("color_space") == soap_cs):
                    had_existing = True
                    existing_uuid = prof.get("uuid")
                    existing_prof_full = prof
                    break
        except Z9Error:
            pass  # non-blocking

        # 6. Prepare the ticket_date in HP format
        ticket_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 6.5. Refuse if importing an ICC file marked freeglaz-factory
        #      (donor factory profile extracted by `paper donor-export`)
        from . import cache
        if cache.is_freeglaz_factory_filename(icc_path):
            raise Z9PaperError(
                f"Import refused: the source ICC file carries the "
                f"freeglaz factory marker (`{cache.FACTORY_MARKER}`).\n"
                f"  File: {icc_path}\n"
                f"  This file is a factory donor profile extracted by "
                f"`paper donor-export`. Importing it into a slot would "
                f"turn it into a fake custom (attribute custom='1' "
                f"while the content is factory).\n"
                f"  To expose the factory profile in a slot, use "
                f"instead:\n"
                f"    freeglaz paper restore-icc {paper.get('name', '?')!r} "
                f"--gloss-enhancer {gloss_enhancer}"
            )

        # 6.6. Automatic backup of the existing profile (reading the slot via getProfile)
        backup_path_str = None
        backup_md5 = None
        backup_skipped_reason = None
        if auto_backup and had_existing:
            try:
                getprof = self._client.soap.get_profile(
                    medium_id=medium_id,
                    gloss_enhancer=gloss_enhancer,
                    color_space=soap_cs,
                )
                if (getprof.get("outcome") == "OK"
                        and getprof.get("icc_bytes")):
                    # Unified per-serial backup service (via store.get_serial):
                    # backups/<serial>/<mediaid>/<ge_state>/<ISO>.icc. No
                    # sidecar — the original name lives in the desc tag of the .icc.
                    from . import icc_backups, store as _store
                    serial = _store.get_serial(self._client)
                    ge_state = icc_backups.ge_state_from_gloss(gloss_enhancer)
                    b_path = icc_backups.new_backup_path(serial, medium_id, ge_state)
                    b_path.write_bytes(getprof["icc_bytes"])
                    icc_backups.rotate(serial, medium_id, ge_state)
                    backup_path_str = str(b_path)
                    backup_md5 = hashlib.md5(getprof["icc_bytes"]).hexdigest()
                else:
                    backup_skipped_reason = f"getProfile outcome={getprof.get('outcome')}"
            except Exception as e:
                backup_skipped_reason = f"{type(e).__name__}: {e}"

        # 7. SOAP call setProfile (dedicated method that reproduces EXACTLY
        #    the SOAP format expected by the service)
        try:
            result_soap = self._client.soap.set_profile(
                medium_id=medium_id,
                icc_bytes=icc_bytes,
                icc_name=icc_name,
                ticket_date=ticket_date,
                quality=quality,
                gloss_enhancer=gloss_enhancer,
                maximum_detail=maximum_detail,
                color_space=soap_cs,
            )
        except Z9Error as e:
            raise Z9CalibrationError(f"setProfile SOAP failed: {e}")

        outcome = result_soap.get("outcome", "?")

        if outcome != "OK":
            from . import outcomes
            raise Z9CalibrationError(
                message=f"ICC import failed: outcome={outcome}",
                code=outcome,
                description=outcomes.describe(outcome) if outcome else
                    "The Z9 refused the profile without an explicit error message.",
                context={
                    "paper_name": paper.get("name"),
                    "paper_id": medium_id,
                    "icc_path": icc_path,
                    "icc_size": len(icc_bytes),
                    "icc_md5": icc_md5,
                    "gloss_enhancer": gloss_enhancer,
                    "color_space": soap_cs,
                },
            )

        return {
            "paper_name": paper.get("name"),
            "paper_id": medium_id,
            "icc_path": icc_path,
            "icc_size_bytes": len(icc_bytes),
            "icc_md5": icc_md5,
            "icc_name": icc_name,
            "ticket_date": ticket_date,
            "outcome": outcome,
            "had_existing": had_existing,
            "existing_uuid": existing_uuid,
            "gloss_enhancer": gloss_enhancer,
            "color_space": color_space,
            "backup_path": backup_path_str,
            "backup_md5": backup_md5,
            "backup_skipped_reason": backup_skipped_reason,
        }

    def delete_profile(self, ref,
                       gloss_enhancer="FULLPAGE",
                       color_space="RGB"):
        """
        Deletes an ICC profile from a slot of a custom paper.

        Uses SOAP `deleteProfile` on port 8085 (PaperManagement).

        Endpoint : POST http://<host>:8085/MManApi/Query
        Action   : "http://www.bpo.hp.com/PaperManagement/deleteProfile"

        Firmware effect: the (GE, ColorSpace) slot of the custom paper is emptied.
        If an R&D donor factory profile exists in the firmware for this paper
        (typical case of custom papers derived from an HP donor), it is
        automatically re-exposed to replace the deleted custom profile.

        This is the native mechanism of the "Restore Factory Profile" operation
        exposed by the HP utilities (cf. method `restore_icc` which is a
        semantic alias of it).

        :param ref: MediumId or partial name of the custom paper
        :param gloss_enhancer: "FULLPAGE" | "OFF" — slot to delete
        :param color_space: "RGB" | "GRAYSCALE" -> mapped to PRINTER_RGB / PRINTER_GRAYSCALE

        :return: dict with:
                   - "paper_name", "paper_id"
                   - "gloss_enhancer", "color_space"
                   - "outcome" : firmware code (OK or other)
                   - "deleted_uuid" : UUID of the deleted profile (None if not found)
                   - "deleted_icc_name" : name of the deleted profile (None if not found)

        :raises Z9PaperError: paper not found, factory protected, or invalid parameters
        :raises Z9Error: if the firmware returns outcome != OK
        """
        # 1. Resolve the reference
        paper = self._resolve_paper(ref)
        medium_id = paper["id"]

        # 2. Guard: refuse factory papers
        if paper.get("is_factory", False):
            raise Z9PaperError(
                f"Refusing to delete a profile on a factory paper: "
                f"'{paper['name']}' (id={medium_id}). The profiles of HP "
                f"factory papers are protected. This command only applies "
                f"to custom papers."
            )

        # 3. Validate the parameters
        if gloss_enhancer not in ("FULLPAGE", "OFF"):
            raise Z9PaperError(f"Invalid GlossEnhancer: '{gloss_enhancer}'")
        if color_space == "RGB":
            soap_cs = "PRINTER_RGB"
        elif color_space == "GRAYSCALE":
            soap_cs = "PRINTER_GRAYSCALE"
        else:
            raise Z9PaperError(f"Invalid ColorSpace: '{color_space}'")

        # 4. Pre-read to retrieve the info of the deleted profile (traceability)
        deleted_uuid = None
        deleted_icc_name = None
        try:
            details = self.details(medium_id)
            for prof in (details or {}).get("profiles") or []:
                if (prof.get("gloss_enhancer") == gloss_enhancer
                        and prof.get("color_space") == soap_cs):
                    deleted_uuid = prof.get("uuid")
                    deleted_icc_name = prof.get("icc_name")
                    break
        except Z9Error:
            pass  # non-blocking

        # 5. SOAP call deleteProfile
        from .soap import NS_PM, NS_EO, find_body_element
        body_xml = (
            f'<PaperManagement:DELETE-PROFILE-REQUEST>'
            f'<PaperManagement:MediumId>{medium_id}</PaperManagement:MediumId>'
            f'<PaperManagement:Key>'
            f'<EngineOperations:GlossEnhancer>{gloss_enhancer}</EngineOperations:GlossEnhancer>'
            f'<EngineOperations:ColorSpace>{soap_cs}</EngineOperations:ColorSpace>'
            f'</PaperManagement:Key>'
            f'</PaperManagement:DELETE-PROFILE-REQUEST>'
        )
        xml_response = self._client.soap.call_paper_mgmt(
            action="deleteProfile",
            body_xml=body_xml,
            namespaces={
                "PaperManagement": NS_PM,
                "EngineOperations": NS_EO,
            },
        )

        # 6. Parse the response
        elem = find_body_element(
            xml_response,
            expected_local_name="DELETE-PROFILE-RESPONSE",
        )
        if elem is None:
            raise Z9Error("Empty response for deleteProfile")
        outcome = elem.get("outcome", "?")
        if outcome != "OK":
            from . import outcomes
            raise Z9Error(
                f"deleteProfile failed: outcome={outcome}"
                + (f" — {outcomes.describe(outcome)}" if outcome else "")
            )

        return {
            "paper_name": paper.get("name"),
            "paper_id": medium_id,
            "gloss_enhancer": gloss_enhancer,
            "color_space": color_space,
            "outcome": outcome,
            "deleted_uuid": deleted_uuid,
            "deleted_icc_name": deleted_icc_name,
        }

    def restore_icc(self, ref,
                    gloss_enhancer="FULLPAGE",
                    color_space="RGB"):
        """
        Restores the factory profile of a slot of a custom paper.

        Semantic alias of `delete_profile`. Reproduces the
        "Restore Factory Profile" operation of the HP utilities.

        Mechanism:
          1. Deletes the custom profile currently in the slot (deleteProfile)
          2. The firmware automatically re-exposes the R&D donor factory profile
             of the paper that was stored underneath (never erased).

        :param ref: MediumId or partial name of the custom paper
        :param gloss_enhancer: "FULLPAGE" | "OFF" — slot to restore
        :param color_space: "RGB" | "GRAYSCALE"

        :return: dict identical to `delete_profile`

        :raises Z9PaperError: paper not found, factory, or invalid parameters
        :raises Z9Error: if the firmware returns outcome != OK

        Example:
            client.paper.restore_icc("Canson Baryta Photographique",
                                     gloss_enhancer="OFF")
        """
        return self.delete_profile(
            ref=ref,
            gloss_enhancer=gloss_enhancer,
            color_space=color_space,
        )

    def donor_export(self, ref, color_space="RGB", on_step=None):
        """
        Extracts the R&D donor factory profile of a custom paper and caches it.

        Strategy (option γ):
        1. Examine the profiles in both GE slots
        2. If AT LEAST ONE slot has a pure factory profile (custom="0"):
              -> export from that slot, without touching the rest
        3. If BOTH slots contain user custom profiles:
              -> error (the user must decide which slot to sacrifice
                via restore_icc before calling donor_export)

        The exported profile is saved in the local cache
        (~/Documents/freeglaz/donors/<slug>.icc) with its metadata.

        :param ref: MediumId or partial name of the custom paper
        :param color_space: "RGB" | "GRAYSCALE"
        :param on_step: optional callback to trace the process.
                       Signature: on_step(step_num, total, label, **details)

        :return: dict with:
                   - "paper_name", "paper_id"
                   - "donor_id", "donor_name"
                   - "gloss_enhancer_extracted_from" : "FULLPAGE" or "OFF"
                   - "color_space"
                   - "icc_path"   : path of the file in the cache
                   - "icc_size_bytes", "icc_md5"
                   - "skipped"    : True if the cache already existed and is valid

        :raises Z9PaperError: paper not found, factory, or no factory slot available
        :raises Z9Error: if the SOAP export fails
        """
        from . import cache

        def _step(n, total, label, **details):
            if on_step:
                on_step(n, total, label, **details)

        # 1. Resolve the paper
        paper = self._resolve_paper(ref)
        medium_id = paper["id"]
        paper_name = paper.get("name", "?")

        if paper.get("is_factory", False):
            raise Z9PaperError(
                f"Paper {paper_name!r} is factory (HP), not custom. "
                f"donor-export only applies to custom papers."
            )

        # 2. Read the existing profiles
        _step(1, 3, "lecture-profils", paper_name=paper_name)
        details = self.details(medium_id)
        if details is None:
            raise Z9Error(f"Unable to read details of paper {medium_id}")

        profiles = details.get("profiles") or []
        if color_space == "RGB":
            soap_cs = "PRINTER_RGB"
        elif color_space == "GRAYSCALE":
            soap_cs = "PRINTER_GRAYSCALE"
        else:
            raise Z9PaperError(f"Invalid ColorSpace: '{color_space}'")

        # Filter by target ColorSpace
        slot_factory = None
        slot_custom = None
        for prof in profiles:
            if prof.get("color_space") != soap_cs:
                continue
            if prof.get("custom"):
                slot_custom = prof
            else:
                if slot_factory is None:
                    slot_factory = prof

        # 3. Slot choice strategy
        if slot_factory is None:
            # No factory slot available
            available = [
                f"GE={p.get('gloss_enhancer')} (custom user)"
                for p in profiles
                if p.get("color_space") == soap_cs and p.get("custom")
            ]
            raise Z9PaperError(
                f"No factory slot available for this paper (ColorSpace={soap_cs}).\n"
                f"  Existing slots: {available or '(none)'}\n"
                f"  To recover the R&D donor, first run:\n"
                f"    freeglaz paper restore-icc {paper_name!r} --gloss-enhancer <GE>"
            )

        gloss_enhancer_src = slot_factory.get("gloss_enhancer")

        _step(2, 3, "selection-slot",
              slot_chosen=gloss_enhancer_src,
              factory_uuid=slot_factory.get("uuid"),
              factory_icc_name=slot_factory.get("icc_name"),
              factory_date=slot_factory.get("date"),
              has_custom_other_slot=slot_custom is not None,
              custom_uuid=slot_custom.get("uuid") if slot_custom else None,
              custom_icc_name=slot_custom.get("icc_name") if slot_custom else None)

        # 4. Check cache: if UUID identical, no need to re-extract
        from . import store as _store
        serial = _store.get_serial(self._client)
        cached_entry = cache.get_donor_entry(paper_name, serial)
        if cached_entry and cached_entry.get("z9_uuid") == slot_factory.get("uuid"):
            # Cache up to date, just check the file integrity
            try:
                path, data, meta = cache.load_donor(
                    paper_name, serial, verify_md5=True,
                )
                _step(3, 3, "cache-hit",
                      cache_path=str(path),
                      size_bytes=len(data),
                      md5=meta.get("md5"))
                return {
                    "paper_name": paper_name,
                    "paper_id": medium_id,
                    "donor_id": details.get("donor_id"),
                    "donor_name": None,  # resolved CLI-side if needed
                    "gloss_enhancer_extracted_from": gloss_enhancer_src,
                    "color_space": color_space,
                    "icc_path": str(path),
                    "icc_size_bytes": len(data),
                    "icc_md5": meta.get("md5"),
                    "skipped": True,
                }
            except (FileNotFoundError, ValueError):
                # cache corrupted, we re-extract
                pass

        # 5. Extraction via SOAP getProfile (without writing an intermediate file)
        _step(3, 3, "extraction",
              gloss_enhancer=gloss_enhancer_src,
              color_space=soap_cs)

        try:
            result = self._client.soap.get_profile(
                medium_id=medium_id,
                gloss_enhancer=gloss_enhancer_src,
                color_space=soap_cs,
            )
        except Z9Error as e:
            raise Z9CalibrationError(f"getProfile SOAP failed: {e}")

        outcome = result.get("outcome")
        icc_bytes = result.get("icc_bytes")
        if outcome != "OK" or not icc_bytes:
            raise Z9CalibrationError(
                message=f"Donor export failed: outcome={outcome}",
                code=outcome or "?",
                context={
                    "paper_name": paper_name,
                    "paper_id": medium_id,
                    "gloss_enhancer": gloss_enhancer_src,
                    "color_space": soap_cs,
                },
            )

        # 6. Save in the mirror
        path, entry = cache.save_donor(
            icc_bytes=icc_bytes,
            paper_id=medium_id,
            paper_name=paper_name,
            serial=serial,
            donor_id=details.get("donor_id"),
            donor_name=None,  # resolved CLI-side if needed
            gloss_enhancer_extracted_from=gloss_enhancer_src,
            color_space=soap_cs,
            z9_uuid=slot_factory.get("uuid"),
            z9_icc_name=slot_factory.get("icc_name"),
            z9_date=slot_factory.get("date"),
            extracted_from_host=self._client.host,
        )

        return {
            "paper_name": paper_name,
            "paper_id": medium_id,
            "donor_id": details.get("donor_id"),
            "donor_name": None,
            "gloss_enhancer_extracted_from": gloss_enhancer_src,
            "color_space": color_space,
            "icc_path": str(path),
            "icc_size_bytes": entry.size_bytes,
            "icc_md5": entry.md5,
            "skipped": False,
        }


def _xml_escape(text):
    """Escapes special XML characters for insertion into a SOAP body."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


class DeviceOps:
    """Machine state: inks, loaded paper, alerts, counters."""

    def __init__(self, client):
        self._client = client

    def media_system(self):
        """Returns the media system state (loaded paper, drawers, etc.)."""
        text = self._client.rest.get("/MediaSystem.json")
        return parsers.parse_json(text).get("MediaSystem", {})

    def ink_system(self):
        """Returns the ink state (10 entries including post-treatment GE)."""
        text = self._client.rest.get("/InkSystem.json")
        return parsers.parse_json(text).get("InkSystem", {})

    def device_status(self):
        """Machine overview status (alerts, activity)."""
        text = self._client.rest.get("/DeviceStatus.json")
        return parsers.parse_json(text).get("DeviceStatus", {})

    def loaded_media_id(self):
        """
        Returns the MediumId of the currently loaded paper.

        Searches in ALL input sources (Roll, Sheet/ManualFeed).
        Returns the first MediaTypeID found.

        :return: str MediumId or None if no paper loaded
        """
        info = self.loaded_media_info()
        return info["media_id"] if info else None

    def loaded_media_info(self):
        """
        Returns a dict describing the currently loaded paper:
          {
            "media_id": str           - MediumId hex or short
            "source": str             - "ROLL", "MANUAL_FEED", etc.
            "source_label": str       - "Roll", "ManualSheet" (UI)
            "drawer_id": int          - number of the drawer it is in
            "width_in": float|None    - width in inches if available
            "width_mm": float|None    - width in millimeters
            "length_in": float|None   - remaining length if available
            "length_mm": float|None
          }
        or None if no paper loaded.

        Searches in all sources: Roll, Sheet (manual feed), etc.
        Returns the first source with a loaded paper.
        """
        ms = self.media_system()
        try:
            drawers = ms.get("DrawerCollection", {}).get("Drawer", [])
            if not isinstance(drawers, list):
                drawers = [drawers]

            for drawer in drawers:
                drawer_id = drawer.get("Id")
                input_devices = drawer.get("InputDeviceCollection") or {}
                # We iterate over all keys (Roll, Sheet, etc.)
                for source_key, source in input_devices.items():
                    if not isinstance(source, dict):
                        continue
                    loaded_coll = source.get("LoadedMediaCollection")
                    if not loaded_coll:
                        continue
                    loaded = loaded_coll.get("LoadedMedia")
                    if not loaded or not loaded.get("MediaTypeID"):
                        continue

                    # Extract dimensions if available
                    width_in = None
                    width_mm = None
                    w = loaded.get("Width")
                    if isinstance(w, dict) and w.get("Value") is not None:
                        width_in = float(w["Value"])
                        width_mm = width_in * 25.4

                    length_in = None
                    length_mm = None
                    rl = loaded.get("RemainingLength")
                    if isinstance(rl, dict) and rl.get("Value") is not None:
                        length_in = float(rl["Value"])
                        length_mm = length_in * 25.4

                    return {
                        "media_id": loaded["MediaTypeID"],
                        "source": source.get("IdForPrinting", source_key.upper()),
                        "source_label": source.get("IdForUI", source_key),
                        "drawer_id": drawer_id,
                        "width_in": width_in,
                        "width_mm": width_mm,
                        "length_in": length_in,
                        "length_mm": length_mm,
                    }
        except (AttributeError, KeyError, TypeError):
            pass
        return None

    def status(self):
        """
        Global dashboard: assembles several sources into one simple dict.

        Returns:
          {
            "identification": {model, serial, partnumber, ...},
            "loaded_paper_id": str|None,
            "loaded_paper_name": str|None,
            "loaded_paper_source": str|None    - "ROLL", "MANUAL_FEED", ...
            "loaded_paper_source_label": str|None - "Roll", "ManualSheet"
            "loaded_paper_width_mm": float|None
            "loaded_paper_length_mm": float|None
            "ink_levels": {color: percent},
            "ink_warnings": [...],
            "global_status": "Ready"|"WithAlerts"|...,
          }
        """
        identification = self._client.identification()
        media_info = self.loaded_media_info()

        loaded_id = media_info["media_id"] if media_info else None
        loaded_name = None
        if loaded_id:
            paper = self._client.paper.get(loaded_id)
            loaded_name = paper["name"] if paper else None

        ink = self.ink_system()
        ink_levels = {}
        ink_warnings = []
        groups = ink.get("InkSlotGroupCollection", {}).get("InkSlotGroup", [])
        if not isinstance(groups, list):
            groups = [groups]
        for g in groups:
            color = g.get("Color", "?")
            info = g.get("InkSlotGroupInfo", {}).get("InkSupplyGroupInfo", {})
            pct = info.get("LevelPercentage")
            state = info.get("State")
            if pct is not None:
                ink_levels[color] = float(pct)
            if state in ("Warning", "Error"):
                ink_warnings.append({
                    "color": color,
                    "state": state,
                    "status": info.get("UserReportedStatus"),
                })

        device = self.device_status()
        global_status = device.get("StatusOverview", {}).get("MostRelevantStatus", "Unknown")

        return {
            "identification": identification,
            "loaded_paper_id": loaded_id,
            "loaded_paper_name": loaded_name,
            "loaded_paper_source": media_info["source"] if media_info else None,
            "loaded_paper_source_label": media_info["source_label"] if media_info else None,
            "loaded_paper_width_mm": media_info["width_mm"] if media_info else None,
            "loaded_paper_length_mm": media_info["length_mm"] if media_info else None,
            "ink_levels": ink_levels,
            "ink_warnings": ink_warnings,
            "global_status": global_status,
        }
