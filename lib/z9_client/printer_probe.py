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

"""Connection test for a printer IP (IP configuration, B-IP 2/3).

Tests an **arbitrary** IP (user input, BEFORE registration) with a
DISPOSABLE client — not the global lifespan client. Reads ``/Identification.xml``
(REST PIWS) and classifies the result. **Persists NOTHING** (registration in
store.json = separate action via ``cache.add_printer``, triggered by the UI).

TWO-level check (decision: open up the DesignJet range honestly), both from
``printer_model.classify``:
- **Acceptance**: ``"DesignJet"`` in ``ModelName`` (casefold) → accepted (Z9,
  Z9 Pro, Z3200…); otherwise ``not_a_designjet``. Never a strict equality (24in/44in).
- **Family and reported support**: ``z9`` → ``model_support = "validated"`` (tested
  on the real Z9); ``z9pro`` → ``"partial"`` (known limits, never "validated" even
  though its ModelName contains "DesignJet Z9"); any other DesignJet → ``"untested"``.
  The backend SIGNALS, the UI warns — we do not forbid the non-validated ones.

Four statuses: ``ok`` / ``unreachable`` / ``not_a_designjet`` / ``error`` — never
a crashing exception. ``error`` = a fault on OUR side (e.g. a broken bundle with
no XML parser, v0.1.11 on macOS 15): it must not be blamed on the printer.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# SHORT timeout: an unreachable host must not make the user wait (not 10/30 s).
TEST_TIMEOUT_S = 4.0


def test_connection(ip: str, *, timeout: float = TEST_TIMEOUT_S) -> dict:
    """Test the IP ``ip``. Returns a dict ``{status, …}`` (never raises):

    - ``{"status": "unreachable", "message": …}`` — no response (TCP/SSL/timeout);
    - ``{"status": "not_a_designjet", "message"|"model": …}`` — responded but not
      a usable DesignJet;
    - ``{"status": "error", "message": …}`` — local fault, not the printer's;
    - ``{"status": "ok", "model", "serial", "firmware", "part", "family",
      "model_support"}``.
    """
    from xml.etree.ElementTree import ParseError

    from .client import Z9Client
    from .exceptions import Z9ConnectionError, Z9Error
    from .printer_model import classify, support_level

    ip = (ip or "").strip()
    if not ip:
        return {"status": "unreachable", "message": "IP vide"}

    client = Z9Client(host=ip, timeout=timeout)
    try:
        try:
            ident = client.identification()
        except Z9ConnectionError as e:
            return {"status": "unreachable", "message": str(e)}
        except (Z9Error, ParseError) as e:  # responded, not a usable identification
            logger.info("test_connection %s: unusable response: %s", ip, e)
            return {"status": "not_a_designjet",
                    "message": f"Unexpected response from {ip} : {e}"}
        except Exception as e:  # noqa: BLE001 — local fault, report it as such
            logger.exception("test_connection %s: internal error", ip)
            return {"status": "error",
                    "message": f"{type(e).__name__}: {e}"}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — best-effort close
            pass

    model = (ident.get("ModelName") or "").strip()
    family = classify(ident)
    if family is None:
        return {"status": "not_a_designjet",
                "message": f"{ip} responded but is not a DesignJet printer",
                "model": model or None}

    firmware = ident.get("FWVersion") or ident.get("FwReleaseName")
    return {
        "status": "ok",
        "model": model,
        "serial": ident.get("SerialNumber"),
        "firmware": firmware,
        "part": ident.get("PartNumber"),
        "family": family,
        "model_support": support_level(family),
    }
