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

"""LEDM ``/DevMgmt/ProductLogsDyn.xml`` — firmware event logs.

Standalone feature. The Z9 keeps the last 100 firmware events in a
sliding window. Each event contains: severity, event code, UTC
timestamp, human-readable event detail, C++ source file + line number,
and firmware version.

HTTPS endpoint without auth (Z9 self-signed certificate, verify=False).
"""
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

import requests

from .exceptions import Z9ConnectionError, Z9RESTError
from .rest import make_z9_session

logger = logging.getLogger(__name__)

NS_DD = "{http://www.hp.com/schemas/imaging/con/dictionaries/1.0/}"
NS_PLDYN = "{http://www.hp.com/schemas/imaging/con/ledm/productlogsdyn/2008/01/16}"

MAX_EVENTS = 100


@dataclass
class LogEvent:
    sequence_number: int
    event_code: str
    timestamp: str
    event_detail: str
    internal_error_code: int
    source_file: str
    source_line: int
    severity: str
    firmware_revision: str

    def to_dict(self) -> dict:
        return asdict(self)


class ProductLogsReader:
    """Reads ``/DevMgmt/ProductLogsDyn.xml`` on the Z9."""

    def __init__(self, host: str, timeout: int = 10):
        self.host = host
        self.timeout = timeout
        self._session = make_z9_session()

    def fetch_xml(self) -> str:
        url = f"https://{self.host}/DevMgmt/ProductLogsDyn.xml"
        try:
            r = self._session.get(url, timeout=self.timeout)
        except requests.exceptions.ConnectTimeout:
            raise Z9ConnectionError(f"Timeout connecting to {self.host}")
        except requests.exceptions.ConnectionError as e:
            raise Z9ConnectionError(f"Cannot reach {self.host}: {e}")
        except requests.exceptions.RequestException as e:
            raise Z9ConnectionError(f"Network error on {url}: {e}")
        if r.status_code >= 400:
            raise Z9RESTError(r.status_code, url, r.text)
        r.encoding = "utf-8"
        return r.text

    def get_events(
        self,
        limit: int = MAX_EVENTS,
        severity: Optional[str] = None,
        since: Optional[datetime] = None,
        event_code_prefix: Optional[str] = None,
    ) -> list[LogEvent]:
        xml_text = self.fetch_xml()
        root = ET.fromstring(xml_text)

        events = []
        for elem in root.iter():
            if not isinstance(elem.tag, str):
                continue
            if elem.tag != f"{NS_PLDYN}Event":
                continue
            ev = _parse_event(elem)
            if ev is None:
                continue
            if severity:
                allowed = [s.strip().lower() for s in severity.split(",")]
                if ev.severity.lower() not in allowed:
                    continue
            if since and ev.timestamp:
                try:
                    ts = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00"))
                    if ts < since:
                        continue
                except ValueError:
                    pass
            if event_code_prefix and not ev.event_code.startswith(event_code_prefix):
                continue
            events.append(ev)

        events.sort(key=lambda e: e.sequence_number, reverse=True)
        return events[:limit]


def _text(parent, ns_tag: str) -> str:
    el = parent.find(ns_tag)
    return el.text.strip() if el is not None and el.text else ""


def _parse_event(elem) -> Optional[LogEvent]:
    try:
        seq = int(_text(elem, f"{NS_DD}SequenceNumber") or "0")
    except ValueError:
        seq = 0

    source_code = elem.find(f"{NS_DD}SourceCode")
    source_file = ""
    source_line = 0
    if source_code is not None:
        source_file = _text(source_code, f"{NS_DD}FileName")
        try:
            source_line = int(_text(source_code, f"{NS_DD}LineNumber") or "0")
        except ValueError:
            source_line = 0

    fw_elem = elem.find(f"{NS_DD}FirmwareVersion")
    fw_rev = _text(fw_elem, f"{NS_DD}Revision") if fw_elem is not None else ""

    try:
        internal_code = int(_text(elem, f"{NS_DD}InternalErrorCode") or "0")
    except ValueError:
        internal_code = 0

    return LogEvent(
        sequence_number=seq,
        event_code=_text(elem, f"{NS_DD}EventCode"),
        timestamp=_text(elem, f"{NS_DD}TimeStamp"),
        event_detail=_text(elem, f"{NS_DD}EventDetail"),
        internal_error_code=internal_code,
        source_file=source_file,
        source_line=source_line,
        severity=_text(elem, f"{NS_DD}Severity"),
        firmware_revision=fw_rev,
    )
