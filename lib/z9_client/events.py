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

"""LEDM Event Management endpoints: /EventMgmt/* on the Z9.

Endpoints exposed by the firmware (cf. /EventMgmt/EventMgmtManifest.xml):
  - /EventMgmt/EventTable.xml        : table of current events
  - /EventMgmt/EventCapabilities.xml : capabilities (categories, delivery options)
  - /EventMgmt/SubscriptionList.xml  : active subscriptions

The root path is DIFFERENT from PIWS (/LFPWebServices/PI/*) -> we do not
extend PIWSClient. We do, however, share the HTTPS session (HP legacy cipher)
via ``make_z9_session()``.

For the UDP/XMPP push subscription exposed by the firmware (cf.
``KickDeliveryOptionList``), this module only does the **read**.
The full push implementation (UDP socket + POST SubscriptionList) is
planned in ``Z9StatusUDPSubscriber`` on the webapp side and is not in the
current scope.
"""
import logging
from xml.etree import ElementTree as ET

import requests

from .exceptions import Z9AuthError, Z9ConnectionError, Z9RESTError
from .rest import make_z9_session

logger = logging.getLogger(__name__)

# LEDM XML namespaces used by the /EventMgmt/*.xml responses
NS_EV = "{http://www.hp.com/schemas/imaging/con/ledm/events/2007/09/16}"
NS_DD = "{http://www.hp.com/schemas/imaging/con/dictionaries/1.0/}"


class LEDMEventReader:
    """Reading the LEDM ``/EventMgmt/*`` endpoints of the Z9.

    Stateless. Each call makes an HTTPS GET on the Z9. No internal
    cache and no threading — the caller (on the webapp side:
    ``Z9StatusPollSubscriber``) manages its own cache and loop.
    """

    def __init__(self, host, admin_pwd=None, timeout=10):
        """
        :param host: IP/hostname of the Z9
        :param admin_pwd: Admin password (if endpoints are protected —
                          currently EventTable/Capabilities/Subscriptions
                          are accessible without auth, to confirm if firmware changes)
        :param timeout: Network timeout in seconds
        """
        self.host = host
        self.admin_pwd = admin_pwd
        self.timeout = timeout
        self._session = make_z9_session()

    def _get_xml(self, path: str, auth: bool = False) -> str:
        """Raw XML GET. Same exceptions as ``PIWSClient.get``."""
        if not path.startswith("/"):
            path = "/" + path
        url = f"https://{self.host}{path}"
        kwargs = {"timeout": self.timeout}
        if auth:
            if not self.admin_pwd:
                raise Z9AuthError(f"{path} requires auth but admin_pwd not set")
            kwargs["auth"] = ("admin", self.admin_pwd)

        try:
            r = self._session.get(url, **kwargs)
        except requests.exceptions.ConnectTimeout:
            raise Z9ConnectionError(f"Timeout connecting to {self.host}")
        except requests.exceptions.ConnectionError as e:
            raise Z9ConnectionError(f"Cannot reach {self.host}: {e}")
        except requests.exceptions.RequestException as e:
            raise Z9ConnectionError(f"Network error on {url}: {e}")

        if r.status_code == 401:
            raise Z9AuthError(f"401 on {path} — check Z9_ADMIN_PWD")
        if r.status_code >= 400:
            raise Z9RESTError(r.status_code, url, r.text)

        r.encoding = "utf-8"
        return r.text

    # ─── EventTable ────────────────────────────────────────────────

    def event_table(self) -> list[dict]:
        """Return the list of current events.

        Return format ::

            [
              {"category":      "ConsumableEvent",
               "aging_stamp":   "226-171",
               "resource_uri":  "/DevMgmt/ConsumableConfigDyn.xml",
               "resource_type": "ledm:hpLedmConsumableConfigDyn"},
              ...
            ]

        The ``aging_stamp`` (observed format "counter-tick") is the version
        identifier of the event: it changes when the event is re-emitted. This
        is what we diff between two polls to detect changes.
        """
        xml = self._get_xml("/EventMgmt/EventTable.xml")
        root = ET.fromstring(xml)
        events: list[dict] = []
        for evt in root.findall(f"{NS_EV}Event"):
            category = (evt.findtext(f"{NS_DD}UnqualifiedEventCategory") or "").strip()
            aging    = (evt.findtext(f"{NS_DD}AgingStamp") or "").strip()
            payload  = evt.find(f"{NS_EV}Payload")
            uri  = (payload.findtext(f"{NS_DD}ResourceURI")  or "").strip() if payload is not None else ""
            kind = (payload.findtext(f"{NS_DD}ResourceType") or "").strip() if payload is not None else ""
            events.append({
                "category":      category,
                "aging_stamp":   aging,
                "resource_uri":  uri,
                "resource_type": kind,
            })
        return events

    # ─── EventCapabilities ─────────────────────────────────────────

    def event_capabilities(self) -> dict:
        """Event capabilities declared by the firmware.

        Return format ::

            {"supported_events": ["AlertTableChanged", "JobEvent", ...],
             "max_persistent_subscriptions": 0,
             "delivery_protocols": ["UDP", "XMPP", "USBRaw", ...]}
        """
        xml = self._get_xml("/EventMgmt/EventCapabilities.xml")
        root = ET.fromstring(xml)

        supported = [
            (e.text or "").strip()
            for e in root.iter(f"{NS_DD}UnqualifiedEventCategory")
        ]
        # Under the <SupportedEventList> element, so already filtered by iter.
        # MaxPersistentSubscriptions may be absent -> 0 by default.
        max_persist_text = (root.findtext(f"{NS_EV}MaxPersistentSubscriptions") or "0").strip()
        try:
            max_persist = int(max_persist_text)
        except ValueError:
            max_persist = 0

        protocols = [
            (p.text or "").strip()
            for p in root.iter(f"{NS_EV}ProtocolType")
        ]
        return {
            "supported_events":             supported,
            "max_persistent_subscriptions": max_persist,
            "delivery_protocols":           protocols,
        }

    # ─── SubscriptionList ──────────────────────────────────────────

    def subscriptions(self) -> list[dict]:
        """List the active LEDM subscriptions on the Z9 side.

        Rarely used for A1 (we do not use UDP push), but useful for
        debugging, introspection, and a future A2.

        Return format ::

            [{"id": "358", "categories": ["DeviceCapabilitiesChanged"]}, ...]
        """
        xml = self._get_xml("/EventMgmt/SubscriptionList.xml")
        root = ET.fromstring(xml)
        subs: list[dict] = []
        for s in root.findall(f"{NS_EV}Subscription"):
            sub_id = (s.findtext(f"{NS_EV}SubscriptionId") or "").strip()
            cats = [
                (c.text or "").strip()
                for c in s.iter(f"{NS_DD}UnqualifiedEventCategory")
            ]
            subs.append({"id": sub_id, "categories": cats})
        return subs
