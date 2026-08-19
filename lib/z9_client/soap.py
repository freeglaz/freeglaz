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
SOAP client for the HP DesignJet Z9.

Z9 SOAP architecture:
  - Port 8085 HTTP: SOAP PaperManagement v1.0
        URL: http://<host>:8085/MManApi/Query
        17 operations (3 are unimplemented WSDL stubs)
        Useful operations: newCustomMedium, getMediumList, getMediumListVersion,
                            getProfile, setProfile, ...

  - Port 8086 HTTP: SOAP multiplexed by XML namespace
        URL: http://<host>:8086/<any-path>
        Routing by xmlns: in the body
        Known services:
          - Calibration       (http://www.bpo.hp.com/Calibration)
              → newCalibration, newProfile, getStatus, enumMediaSources
          - RemoteManagement  (http://www.bpo.hp.com/RemoteManagement)
          - CalibrationsService (hidden, RemoteManagement/Service/CalibrationsService)
          - cds               (http://www.bpo.hp.com/cds)

CRITICAL NOTES (constraints of the Z9 SOAP dialect):
  1. CONNECTION: CLOSE MANDATORY
     The service systematically expects Connection: close. Python with
     requests keep-alive causes TCP hangs on some workflows
     (notably setProfile which does not return an HTTP response).
     So we force Connection: close on all calls.

  2. Port 8085 buggy on unknown namespaces
     The port 8085 SOAP parser has a memory allocation bug if it is
     sent an XML namespace it does not recognize. We must always use
     the correct namespace (http://www.bpo.hp.com/PaperManagement).

  3. setProfile: no HTTP response
     The setProfile operation (ICC injection) does NOT return an HTTP
     body. The Z9 closes the TCP connection cleanly (FIN) immediately
     after receiving it. This case must be handled explicitly.

  4. Polling at 10s
     newCalibration and newProfile are asynchronous. We poll getStatus
     every 10s with the OperationId received in the response.
"""

import logging
import os
import re
import socket
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    Z9ConnectionError,
    Z9SOAPFault,
    Z9ProtocolError,
)

logger = logging.getLogger(__name__)

# Raw SOAP tracing: set FREEGLAZ_SOAP_DEBUG=1 to log every SOAP request envelope
# and response body (INFO level → lands in freeglaz.log). OFF by default — the
# frequent status/paper SOAP calls would otherwise flood the log. Used to
# diagnose model-specific firmware behaviour (e.g. the Z9 Pro profiling failures)
# without a Wireshark capture. Never enabled in normal operation.
_SOAP_DEBUG = os.getenv("FREEGLAZ_SOAP_DEBUG") == "1"


# ─── Z9 SOAP service constants ───────────────────────────────────────

# Port 8085 — PaperManagement
PAPER_MGMT_PORT = 8085
PAPER_MGMT_PATH = "/MManApi/Query"
NS_PM = "http://www.bpo.hp.com/PaperManagement"
NS_EO = "http://www.bpo.hp.com/EngineOperations"

# Port 8086 — multiplexed, routing by the body's XML namespace
MULTIPLEX_PORT = 8086
MULTIPLEX_PATH = "/"
NS_CAL = "http://www.bpo.hp.com/Calibration"
NS_RM = "http://www.bpo.hp.com/RemoteManagement"
NS_CALSV = "http://www.bpo.hp.com/RemoteManagement/Service/CalibrationsService"

# Standard SOAP namespaces
NS_SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"


# ─── SOAP envelope templates ─────────────────────────────────────────

# Minimal envelope validated empirically (Pattern A)
# Works on 8085 AND 8086, sufficient for all the calls we make
SOAP_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>\
<SOAP-ENV:Envelope xmlns:SOAP-ENV="{soap_env}" {extra_ns}>\
<SOAP-ENV:Body>{body}</SOAP-ENV:Body>\
</SOAP-ENV:Envelope>"""


def build_envelope(body_xml, namespaces=None):
    """
    Build a minimal SOAP envelope.

    :param body_xml: The body XML (e.g. "<PM:GET-MEDIUM-LIST-REQUEST/>")
    :param namespaces: optional dict {prefix: uri} to declare in addition
    :return: complete XML str ready to send
    """
    extra_ns = ""
    if namespaces:
        extra_ns = " ".join(f'xmlns:{p}="{u}"' for p, u in namespaces.items())
    return SOAP_ENVELOPE.format(
        soap_env=NS_SOAP_ENV,
        extra_ns=extra_ns,
        body=body_xml,
    )


# ─── HTTP adapter with forced Connection: close ──────────────────────


class ConnectionCloseAdapter(HTTPAdapter):
    """
    HTTPAdapter that forces Connection: close, disables keep-alive
    and limits the TCP pool to 1 connection to avoid hangs.

    The HP firmware tends to mishandle keep-alive on some operations
    (notably setProfile), so we close every time.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, pool_connections=1, pool_maxsize=1, **kwargs)


def make_soap_session():
    """
    Create a requests.Session() configured to talk SOAP to the Z9:
      - Headers: Connection: close, Content-Type: text/xml
      - No keep-alive
      - Light retry on transient network errors (3 attempts)
    """
    session = requests.Session()
    adapter = ConnectionCloseAdapter(max_retries=Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
        allowed_methods=["POST"],
    ))
    session.mount("http://", adapter)
    # Global headers: Connection: close mandatory
    session.headers.update({
        "Connection": "close",
        "Content-Type": "text/xml; charset=utf-8",
        "User-Agent": "freeglaz/0.1",
    })
    return session


# ─── Parsing utilities ───────────────────────────────────────────────


def _strip_namespace(tag):
    """'{http://...}foo' → 'foo'"""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xml_escape_attr(text):
    """Escape a string for use in an XML attribute (between quotes)."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def find_soap_fault(xml_text):
    """
    Look for a SOAP-Fault in the response.
    Returns (faultcode, faultstring) or (None, None) if no fault.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None

    # Look for {NS_SOAP_ENV}Fault at any depth
    for elem in root.iter():
        if _strip_namespace(elem.tag) == "Fault":
            code = None
            msg = None
            for child in elem:
                name = _strip_namespace(child.tag)
                if name == "faultcode":
                    code = (child.text or "").strip()
                elif name == "faultstring":
                    msg = (child.text or "").strip()
            return code, msg
    return None, None


def find_body_element(xml_text, expected_local_name=None):
    """
    Get the first element inside SOAP-ENV:Body.
    If expected_local_name is provided, check that the name matches.

    :return: XML Element (ElementTree) or None if Body is empty
    :raises Z9ProtocolError: if parsing is impossible or element unexpected
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise Z9ProtocolError(f"Cannot parse SOAP response: {e}")

    # Body is at depth 1
    body = None
    for child in root:
        if _strip_namespace(child.tag) == "Body":
            body = child
            break
    if body is None:
        raise Z9ProtocolError("No SOAP-ENV:Body found in response")

    # First child of the body = the business response
    children = list(body)
    if not children:
        return None
    first = children[0]
    if expected_local_name:
        actual = _strip_namespace(first.tag)
        if actual != expected_local_name:
            raise Z9ProtocolError(
                f"Expected <{expected_local_name}> but got <{actual}>"
            )
    return first


# ─── Main SOAPClient ─────────────────────────────────────────────────


class SOAPClient:
    """
    SOAP client for the Z9. Encapsulates the quirks of ports 8085/8086.

    Low-level usage:
        soap = SOAPClient(host="192.168.1.50")
        xml = soap.call_paper_mgmt(
            action="getMediumListVersion",
            body="<PM:GET-MEDIUM-LIST-VERSION-REQUEST/>",
        )

    High-level usage: go through Z9Client.paper.* which calls SOAPClient.
    """

    def __init__(self, host, timeout=30):
        """
        :param host: IP/hostname of the Z9
        :param timeout: timeout in seconds (default 30s; for async
                        workflows we have a separate long timeout)
        """
        self.host = host
        self.timeout = timeout
        self._session = make_soap_session()

    def close(self):
        """Close the keep-alive HTTP session. Called at backend
        shutdown (robustness). Idempotent."""
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass

    def _post(self, port, path, soap_action, envelope):
        """
        SOAP POST with structured error handling.

        :param port: 8085 or 8086
        :param path: URL path (/MManApi/Query or /)
        :param soap_action: value of the SOAPAction header (full URI)
        :param envelope: XML of the complete envelope
        :return: response XML str
        :raises Z9ConnectionError: timeout, network, etc.
        :raises Z9SOAPFault: if the firmware returns a Fault
        :raises Z9ProtocolError: malformed response
        """
        url = f"http://{self.host}:{port}{path}"
        headers = {
            # The SOAPAction must be quoted per SOAP 1.1
            "SOAPAction": f'"{soap_action}"',
        }
        body = envelope.encode("utf-8")

        if _SOAP_DEBUG:
            logger.info("SOAP → %s SOAPAction=%s\n%s", url, soap_action, envelope)

        try:
            response = self._session.post(
                url,
                data=body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectTimeout:
            raise Z9ConnectionError(f"SOAP timeout connecting to {self.host}:{port}")
        except requests.exceptions.ConnectionError as e:
            raise Z9ConnectionError(f"SOAP cannot reach {self.host}:{port}: {e}")
        except requests.exceptions.RequestException as e:
            raise Z9ConnectionError(f"SOAP network error: {e}")

        if _SOAP_DEBUG:
            logger.info("SOAP ← HTTP %d from %s\n%s",
                        response.status_code, url, response.text)

        if response.status_code >= 400:
            raise Z9ProtocolError(
                f"SOAP HTTP {response.status_code} on {url}\n"
                f"Response body: {response.text[:500]}"
            )

        # The Z9 sends UTF-8 but not necessarily a charset header
        response.encoding = "utf-8"
        text = response.text

        # Check there is no SOAP-Fault
        fault_code, fault_msg = find_soap_fault(text)
        if fault_code or fault_msg:
            raise Z9SOAPFault(
                fault_string=fault_msg or "(no fault string)",
                fault_code=fault_code,
            )

        return text

    # ─── High-level helpers per port/service ─────────────────────────

    def call_paper_mgmt(self, action, body_xml, namespaces=None):
        """
        SOAP call to PaperManagement (port 8085).

        :param action: short operation name (e.g. "getMediumListVersion")
                       → SOAPAction will be "http://www.bpo.hp.com/PaperManagement/<action>"
        :param body_xml: Body content (e.g. "<PM:GET-MEDIUM-LIST-VERSION-REQUEST/>")
        :param namespaces: extra dict of namespaces to declare
        :return: XML str of the complete response
        """
        # Standard namespaces for PaperManagement
        ns = {"PM": NS_PM, "EO": NS_EO}
        if namespaces:
            ns.update(namespaces)
        envelope = build_envelope(body_xml, namespaces=ns)
        soap_action = f"{NS_PM}/{action}"
        return self._post(PAPER_MGMT_PORT, PAPER_MGMT_PATH, soap_action, envelope)

    def call_calibration(self, action, body_xml, namespaces=None):
        """
        SOAP call to Calibration (port 8086).

        :param action: short name (e.g. "newCalibration", "getStatus")
                       → SOAPAction will be "http://www.bpo.hp.com/Calibration/<action>"
        :param body_xml: Body content
        :param namespaces: extra dict of namespaces
        :return: response XML str
        """
        ns = {"CAL": NS_CAL}
        if namespaces:
            ns.update(namespaces)
        envelope = build_envelope(body_xml, namespaces=ns)
        soap_action = f"{NS_CAL}/{action}"
        return self._post(MULTIPLEX_PORT, MULTIPLEX_PATH, soap_action, envelope)

    # ─── Read-only business operations ───────────────────────────────

    def get_medium_list_version(self):
        """
        Fetch the current version of the paper list.
        Useful to detect whether the list has changed (creation/deletion).

        Note: we don't know exactly how the firmware names the tag
        contained in the response, so we walk through and take the first
        text leaf element.

        :return: dict {"version": str, "outcome": str, "raw_tag": str}
        """
        xml = self.call_paper_mgmt(
            action="getMediumListVersion",
            body_xml="<PM:GET-MEDIUM-LIST-VERSION-REQUEST/>",
        )
        elem = find_body_element(xml, expected_local_name="GET-MEDIUM-LIST-VERSION-RESPONSE")
        if elem is None:
            raise Z9ProtocolError("Empty response for getMediumListVersion")
        result = {"outcome": elem.get("outcome", "?"), "version": None, "raw_tag": None}
        # Look for the first child that has text (regardless of name)
        for child in elem:
            text = (child.text or "").strip()
            if text:
                result["raw_tag"] = _strip_namespace(child.tag)
                result["version"] = text
                break
        if result["version"] is None:
            # No text child: maybe the version is an attribute
            # of the response element itself
            for attr_name in ("version", "Version", "value"):
                if attr_name in elem.attrib:
                    result["version"] = elem.attrib[attr_name]
                    result["raw_tag"] = f"@{attr_name}"
                    break
        if result["version"] is None:
            raise Z9ProtocolError(
                f"No version value found in: {xml[:500]}"
            )
        return result

    def get_medium_list(self, language="en_US"):
        """
        Fetch the complete paper list via SOAP (SOAP equivalent of
        Paper/List REST).

        This operation is MORE verbose than REST: it returns the whole
        internal XML block of each paper (StarWheelPos, PenToRib, DryTime,
        TimeToReady, etc.) — useful for debug and exploration.

        :param language: language of the Localizations (en_US, fr_FR, etc.)
        :return: raw XML str of the complete response
        """
        body_xml = f'<PM:GET-MEDIUM-LIST-REQUEST language="{language}"/>'
        return self.call_paper_mgmt(action="getMediumList", body_xml=body_xml)

    def get_service_version(self):
        """
        Fetch the version of the PaperManagement service.
        Ultra-simple operation, ideal to test that SOAP works.

        The Z9 response contains <VersionMajor>X</VersionMajor>
        <VersionMinor>Y</VersionMinor> + an outcome="OK" attribute.

        :return: dict {"major": int, "minor": int, "version": "X.Y", "outcome": "OK"}
        """
        xml = self.call_paper_mgmt(
            action="getServiceVersion",
            body_xml="<PM:GET-SERVICE-VERSION-REQUEST/>",
        )
        elem = find_body_element(xml, expected_local_name="GET-SERVICE-VERSION-RESPONSE")
        if elem is None:
            raise Z9ProtocolError("Empty response for getServiceVersion")
        result = {"outcome": elem.get("outcome", "?"), "major": None, "minor": None}
        for child in elem:
            tag = _strip_namespace(child.tag)
            if tag == "VersionMajor":
                result["major"] = int((child.text or "0").strip())
            elif tag == "VersionMinor":
                result["minor"] = int((child.text or "0").strip())
        if result["major"] is None or result["minor"] is None:
            raise Z9ProtocolError(
                f"VersionMajor/VersionMinor not found in: {xml[:300]}"
            )
        result["version"] = f"{result['major']}.{result['minor']}"
        return result

    # ─── Write operations ────────────────────────────────────────────

    def new_custom_medium(self, donor_medium_id, name, language="en_US"):
        """Create a custom paper. To be implemented."""
        raise NotImplementedError("To implement (Block 1.2 - write)")

    def new_calibration(self, medium_id, user="freeglaz", calibration_date=None):
        """
        Launch a CLC (Color Linearization Calibration) on a paper.

        Endpoint: SOAP port 8086, Calibration namespace.
        Payload of the NEW-CALIBRATION (CLC) request.

        ⚠️ Asynchronous: returns immediately with an OperationId.
        Use get_status(operation_id) to track progress.

        :param medium_id: MediumId of the paper (32 hex or short numeric)
        :param user: user name (shown in the HP logs)
        :param calibration_date: ISO date (e.g. "2026-05-13"). If None,
                                 uses the current date.
        :return: dict with:
                   - "operation_id" : str — id for polling
                                     "-1" if the Z9 refused outright
                   - "outcome" : str — firmware code
                                * "OK"               : calibration accepted
                                * "MEDIUM-MISMATCH"  : different paper loaded
                                * "BADSIZE-MEDIUM"   : paper size too small
                                * (other codes possible)
        """
        import datetime as _dt
        if calibration_date is None:
            calibration_date = _dt.date.today().isoformat()

        body_xml = (
            f'<CAL:NEW-CALIBRATION-REQUEST '
            f'user="{_xml_escape_attr(user)}" '
            f'calibrationDate="{calibration_date}">'
            f'<CAL:MediumId>{medium_id}</CAL:MediumId>'
            f'</CAL:NEW-CALIBRATION-REQUEST>'
        )
        xml = self.call_calibration(action="newCalibration", body_xml=body_xml)
        elem = find_body_element(xml, expected_local_name="NEW-CALIBRATION-RESPONSE")
        if elem is None:
            raise Z9ProtocolError(f"Empty response for newCalibration: {xml[:300]}")
        return {
            "operation_id": elem.get("OperationId", "-1"),
            "outcome": elem.get("outcome", "?"),
        }

    def new_profile(self, medium_id, profile_name,
                    quality="BEST", gloss_enhancer="FULLPAGE",
                    max_detail="OFF", color_space="RGB",
                    workflow_kind="PRINT_AND_SCAN",
                    user="freeglaz", calibration_date=None):
        """
        Launch an ICC profiling workflow on a paper.

        Endpoint: SOAP port 8086, Calibration namespace.
        Payload of the NEW-PROFILE request.

        The PRINT_AND_SCAN workflow comprises:
          1. PREPARING-TO-PRINT (~20s warm-up)
          2. PRINTING (~3 min, printing the 464-patch chart)
          3. DRYING (~1 min)
          4. PREPARING-TO-SCAN (~2 min)
          5. SCANNING (~2 min, spectro measurement)
          6. CALCULATING (~1.5 min, ICC generation)
          7. DONE

        Total observed duration: ~10 min.

        :param medium_id: MediumId of the paper (32 hex or short numeric)
        :param profile_name: name of the ICC profile (visible in the paper list)
                             HP format: "HP_<PaperNameNoSpaces>_<GEON|GEOFF>, GE <ON|OFF>"
        :param quality: "BEST" | "NORMAL" | "FAST"
        :param gloss_enhancer: "FULLPAGE" | "OFF"
        :param max_detail: "ON" | "OFF"
        :param color_space: "RGB" | "GRAYSCALE"
        :param workflow_kind: "PRINT_AND_SCAN" (default) | "SCAN_ONLY"
        :param user: user name shown on the Z9 side
        :param calibration_date: ISO date. If None, today.

        :return: dict with:
                   - "operation_id" : str — id for polling getStatus
                                    "-1" if immediate refusal
                   - "outcome" : str — firmware code (OK, BADSIZE-MEDIUM, ...)
        """
        import datetime as _dt
        if calibration_date is None:
            calibration_date = _dt.date.today().isoformat()

        body_xml = (
            f'<CAL:NEW-PROFILE-REQUEST '
            f'user="{_xml_escape_attr(user)}" '
            f'calibrationDate="{calibration_date}" '
            f'workflowKind="{workflow_kind}">'
            f'<CAL:MediumId>{medium_id}</CAL:MediumId>'
            f'<CAL:Ticket profileName="{_xml_escape_attr(profile_name)}">'
            f'<CAL:Key>'
            f'<CAL:Quality>{quality}</CAL:Quality>'
            f'<CAL:GlossEnhancer>{gloss_enhancer}</CAL:GlossEnhancer>'
            f'<CAL:MaximumDetail>{max_detail}</CAL:MaximumDetail>'
            f'<CAL:ColorSpace>{color_space}</CAL:ColorSpace>'
            f'</CAL:Key>'
            f'</CAL:Ticket>'
            f'</CAL:NEW-PROFILE-REQUEST>'
        )
        xml = self.call_calibration(action="newProfile", body_xml=body_xml)
        elem = find_body_element(xml, expected_local_name="NEW-PROFILE-RESPONSE")
        if elem is None:
            raise Z9ProtocolError(f"Empty response for newProfile: {xml[:300]}")
        return {
            "operation_id": elem.get("OperationId", "-1"),
            "outcome": elem.get("outcome", "?"),
        }

    def get_status(self, operation_id):
        """
        Fetch the current state of an asynchronous operation (calibration
        or profiling).

        Endpoint: SOAP port 8086, Calibration namespace.

        Payload:
            <CAL:GET-STATUS-REQUEST OperationId="X"/>

        Typical response:
            <CAL:GET-STATUS-RESPONSE percent="43" process="DRYING" outcome="OK"/>

        Observed process states:
          - "PREPARING-TO-PRINT" (warm-up, percent may be -1)
          - "PRINTING"           (0 → 100)
          - "DRYING"             (0 → 100)
          - "PREPARING-TO-SCAN"  (0 → 100)
          - "SCANNING"           (0 → 100)
          - "CALCULATING"        (0 → 100)
          - "DONE"               (percent="-1", finished successfully)
          - "FAILED"             (typically with outcome="UNKNOWN-OPERATION"
                                  which may mean "job purged after success"
                                  → check via paper.details())

        :param operation_id: OperationId received from new_calibration / new_profile
        :return: dict with:
                   - "percent" : int  (-1 if N/A)
                   - "process" : str  (workflow state)
                   - "outcome" : str  ("OK", "UNKNOWN-OPERATION", ...)
        """
        body_xml = f'<CAL:GET-STATUS-REQUEST OperationId="{operation_id}"/>'
        xml = self.call_calibration(action="getStatus", body_xml=body_xml)
        elem = find_body_element(xml, expected_local_name="GET-STATUS-RESPONSE")
        if elem is None:
            raise Z9ProtocolError(f"Empty response for getStatus: {xml[:300]}")
        try:
            percent = int(elem.get("percent", "-1"))
        except (ValueError, TypeError):
            percent = -1
        return {
            "percent": percent,
            "process": elem.get("process", "?"),
            "outcome": elem.get("outcome", "?"),
        }

    def get_profile(self, medium_id, gloss_enhancer="FULLPAGE",
                    color_space="PRINTER_RGB"):
        """
        Fetch a complete ICC profile (factory or custom) in base64.

        Endpoint: SOAP port 8085, PaperManagement namespace.
        Payload of the GET-PROFILE request.

        IMPORTANT — DIFFERENCE WITH REST PIWS Paper/GetResource:
        ----------------------------------------------------------
        The REST PIWS Paper/GetResource is LIMITED to factory profiles.
        It IGNORES the selector-gloss-enhancer and systematically returns
        the default GEOFF profile, even for custom profiles.

        This SOAP method, on the other hand, correctly accesses custom
        profiles (created via newProfile) and honors the GlossEnhancer
        parameter.

        → THIS IS THE METHOD TO USE to fetch custom ICC.

        :param medium_id: MediumId of the paper
        :param gloss_enhancer: "FULLPAGE" | "OFF"
                              WARNING: SOAP value (not REST!)
        :param color_space: "PRINTER_RGB" | "PRINTER_GRAYSCALE"
                            WARNING: "PRINTER_" prefix mandatory
                            (different from the plain RGB of newProfile)
        :return: dict with:
                   - "icc_bytes" : bytes of the decoded ICC file
                   - "outcome"   : "OK" or firmware error code
        """
        body_xml = (
            f'<PM:GET-PROFILE-REQUEST>'
            f'<PM:MediumId>{medium_id}</PM:MediumId>'
            f'<PM:Key>'
            f'<EngineOperations:GlossEnhancer>{gloss_enhancer}</EngineOperations:GlossEnhancer>'
            f'<EngineOperations:ColorSpace>{color_space}</EngineOperations:ColorSpace>'
            f'</PM:Key>'
            f'</PM:GET-PROFILE-REQUEST>'
        )

        # EngineOperations namespace needed in the envelope
        namespaces = {
            "EngineOperations": "http://www.bpo.hp.com/EngineOperations",
        }
        xml = self.call_paper_mgmt(
            action="getProfile",
            body_xml=body_xml,
            namespaces=namespaces,
        )

        elem = find_body_element(xml, expected_local_name="GET-PROFILE-RESPONSE")
        if elem is None:
            raise Z9ProtocolError(
                f"Empty response for getProfile: {xml[:300]}"
            )
        outcome = elem.get("outcome", "?")

        # Extract the base64 content of <ProfileContents>
        # Note: HP uses a peculiar "<ProfileContents >" format with a space
        # before the >. ElementTree handles that correctly though.
        import base64
        from xml.etree import ElementTree as ET
        # The default namespace is PaperManagement
        # We look for ProfileContents without namespace (resolved by xmlns="...")
        pc_elem = None
        for child in elem:
            tag = _strip_namespace(child.tag)
            if tag == "ProfileContents":
                pc_elem = child
                break

        if pc_elem is None or not (pc_elem.text or "").strip():
            return {"outcome": outcome, "icc_bytes": None}

        b64 = (pc_elem.text or "").replace("\n", "").replace(" ", "").replace("\r", "")
        # Robust base64 padding
        padding = (-len(b64)) % 4
        b64 = b64 + ("=" * padding)
        try:
            icc_bytes = base64.b64decode(b64)
        except Exception as e:
            raise Z9ProtocolError(f"base64 profile decode failed: {e}")

        return {"outcome": outcome, "icc_bytes": icc_bytes}

    def set_profile(self, medium_id, icc_bytes, icc_name,
                    ticket_date, quality="BEST",
                    gloss_enhancer="FULLPAGE", maximum_detail="OFF",
                    color_space="PRINTER_RGB"):
        """
        Import an ICC profile into the Z9 via SOAP setProfile.

        The SOAP envelope must reproduce **exactly** the format expected by
        the setProfile service:
          - Complete namespaces (bsi, bsiwsdl, PaperManagementServiceNs, etc.)
          - PaperManagement: prefix in the body (not PM:)
          - SOAP-ENV:encodingStyle on the Body
          - NS_PM used as the full prefix

        Otherwise the firmware answers:
            "Method '...' not implemented: method name or namespace not recognized"

        :param medium_id: MediumId of the target paper
        :param icc_bytes: bytes of the ICC file
        :param icc_name: name of the profile (visible in paper show)
        :param ticket_date: timestamp format "YYYY-MM-DD HH:MM:SS"
        :param quality: "BEST" | "NORMAL" | "FAST"
        :param gloss_enhancer: "FULLPAGE" | "OFF"
        :param maximum_detail: "ON" | "OFF"
        :param color_space: "PRINTER_RGB" | "PRINTER_GRAYSCALE"
        :return: dict {"outcome": "OK"|...}
        """
        import base64
        b64_icc = base64.b64encode(icc_bytes).decode("ascii")

        # Escape the ICC name for XML
        def _esc(s):
            return (s.replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;")
                     .replace('"', "&quot;"))

        icc_name_escaped = _esc(icc_name)

        # EXACT setProfile SOAP format expected by the service (the port
        # 8085 parser is strict about namespaces/prefixes). DO NOT modify.
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<SOAP-ENV:Envelope'
            ' xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
            ' xmlns:bsi="http://www.hp.com/schemas/imaging/con/bsi/2003/08/21"'
            ' xmlns:bsiwsdl="http://www.hp.com/schemas/imaging/con/bsi/wsdl/2003/08/21"'
            ' xmlns:PaperManagementServiceNs="http://www.bpo.hp.com/PaperManagementService"'
            ' xmlns:PaperManagement="http://www.bpo.hp.com/PaperManagement"'
            ' xmlns:EngineOperations="http://www.bpo.hp.com/EngineOperations"'
            '>'
            '<SOAP-ENV:Body SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<PaperManagement:SET-PROFILE-REQUEST>'
            f'<MediumId>{medium_id}</MediumId>'
            f'<ProfilingTicket date="{ticket_date}" iccName="{icc_name_escaped}">'
            '<Key>'
            f'<EngineOperations:Quality>{quality}</EngineOperations:Quality>'
            f'<EngineOperations:GlossEnhancer>{gloss_enhancer}</EngineOperations:GlossEnhancer>'
            f'<EngineOperations:MaximumDetail>{maximum_detail}</EngineOperations:MaximumDetail>'
            f'<EngineOperations:ColorSpace>{color_space}</EngineOperations:ColorSpace>'
            '</Key>'
            '</ProfilingTicket>'
            f'<ProfileContents>{b64_icc}</ProfileContents>'
            '</PaperManagement:SET-PROFILE-REQUEST>'
            '</SOAP-ENV:Body>'
            '</SOAP-ENV:Envelope>'
        )

        soap_action = f"{NS_PM}/setProfile"
        xml = self._post(PAPER_MGMT_PORT, PAPER_MGMT_PATH, soap_action, envelope)

        elem = find_body_element(xml, expected_local_name="SET-PROFILE-RESPONSE")
        if elem is None:
            raise Z9ProtocolError(
                f"Empty response for setProfile: {xml[:300]}"
            )
        return {"outcome": elem.get("outcome", "?")}
