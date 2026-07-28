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

"""LEDM endpoint ``/Calibration/Calibration.xml`` — authoritative firmware
CLC statuses.

P3.A1. Primary source for the ``paper.clc.status`` field of the
webapp Paper contract. The ``<Calibration>`` block of the SOAP
``getMediumList`` is now a secondary source (fallback only
if LEDM is unreachable or if a MEDIAID does not appear in the
LEDM — edge case of a freshly created paper).

The LEDM exposes 4 real firmware statuses: ``completed`` / ``obsoleted``
/ ``pending`` / ``notDone``. The SOAP only exposes 2 (`obsolete=0/1`),
which masked the ``pending`` status (CLC created but not yet
run — typically a freshly created custom paper awaiting
its first calibration).

Observed XML format (namespace
``http://www.hp.com/schemas/imaging/con/ledm/calibration/2013/02/25``):

.. code-block:: xml

    <cb:Status xmlns:cb="...">
      <cb:Media>
        <cb:MediaCalibration>
          <cb:CalibrationStatus>completed</cb:CalibrationStatus>
          <cb:Type>colorLinearization</cb:Type>
          <cb:MediaKey>9E489F02AE027F9DD93191D872728C1D</cb:MediaKey>
          <cb:ImageType>text</cb:ImageType>
          <cb:TimeStamp>2026-05-25T10:57:18Z</cb:TimeStamp>
        </cb:MediaCalibration>
        ...
      </cb:Media>
    </cb:Status>

We only keep the entries of type ``colorLinearization``. The type
``paperAdvance`` (a distinct mechanical calibration) is ignored in V1.

Stateless. No internal cache — the caller (on the webapp side) manages its
own 30s cache (cf ``webapp/backend/services/`` P3.A2).
"""
import logging
import xml.etree.ElementTree as ET

import requests

from .exceptions import Z9ConnectionError, Z9RESTError
from .rest import make_z9_session

logger = logging.getLogger(__name__)

NS_CB = "{http://www.hp.com/schemas/imaging/con/ledm/calibration/2013/02/25}"

# Authoritative LEDM statuses. The firmware may potentially emit
# other undocumented values; we pass them through as-is to the
# caller, which decides the strategy (fallback "never" recommended).
_KNOWN_STATUSES = ("completed", "obsoleted", "pending", "notDone")


class CalibrationLEDMReader:
    """Reading ``/Calibration/Calibration.xml`` on the Z9.

    Same pattern as ``LEDMEventReader``: stateless, each call
    makes an HTTPS GET. No cache, no threading.
    """

    def __init__(self, host: str, admin_pwd: str | None = None, timeout: int = 10):
        self.host = host
        self.admin_pwd = admin_pwd
        self.timeout = timeout
        self._session = make_z9_session()

    def fetch_xml(self) -> str:
        """Raw XML GET of ``/Calibration/Calibration.xml``.

        :raises Z9ConnectionError: timeout or network error
        :raises Z9RESTError: HTTP >= 400 (404 if the endpoint disappears
            after a firmware update)
        """
        url = f"https://{self.host}/Calibration/Calibration.xml"
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

    def color_linearization_by_mediaid(self) -> dict[str, dict]:
        """Return ``{mediaid: {status, timestamp}}`` for all
        ``<MediaCalibration>`` entries of type ``colorLinearization``.

        - ``mediaid``: value of ``<MediaKey>`` (identical to the MEDIAID
          of the SOAP ``getMediumList``).
        - ``status``: raw value of ``<CalibrationStatus>`` (among
          ``_KNOWN_STATUSES``, or an unexpected value passed through as-is).
        - ``timestamp``: ISO 8601 of ``<TimeStamp>`` or ``None`` (absent
          on ``pending`` and ``notDone``).

        If several ``colorLinearization`` entries exist for the
        same MEDIAID (should not happen but the firmware can),
        the first one wins (XML order).

        :return: dict (empty if endpoint empty or partially malformed XML)
        """
        xml = self.fetch_xml()
        return parse_color_linearization(xml)


# ─── Pure parser (testable without network) ───────────────────────────────


def parse_color_linearization(xml: str) -> dict[str, dict]:
    """Parse an LEDM Calibration XML and return the dict
    ``{mediaid: {status, timestamp}}`` for colorLinearization.

    Separated from the class to ease unit testing
    (XML fixtures without network).
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        logger.warning("LEDM Calibration XML malformed: %s", e)
        return {}

    out: dict[str, dict] = {}
    media_block = root.find(f"{NS_CB}Media")
    if media_block is None:
        return out

    for mc in media_block.findall(f"{NS_CB}MediaCalibration"):
        ctype = (mc.findtext(f"{NS_CB}Type") or "").strip()
        if ctype != "colorLinearization":
            # paperAdvance, etc. — ignored
            continue
        mediaid = (mc.findtext(f"{NS_CB}MediaKey") or "").strip()
        if not mediaid or mediaid in out:
            continue
        status = (mc.findtext(f"{NS_CB}CalibrationStatus") or "").strip()
        timestamp = (mc.findtext(f"{NS_CB}TimeStamp") or "").strip() or None
        out[mediaid] = {
            "status": status,
            "timestamp": timestamp,
        }
    return out
