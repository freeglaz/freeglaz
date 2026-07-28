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

"""Z9 wake from freeglaz (Inc 14 P4).

Empirical wake mechanism:

The Z9 has 2 sleep levels:
- **Intermediate**: Apache HTTPS responds but the PIWS/SOAP services
  return 503 on all endpoints. The root page serves an HP "machine
  asleep" page containing a hidden HTML form.
- **Deep**: TCP timeout (Errno 64 Host is down). Z9 completely
  unreachable at the network level.

HP firmware quirk — discovered 24/05/2026 evening after wake failure
via GET / (timeout 3x in a row). The asleep root page contains an
HTML form:

  <FORM method="post" name="WakeUp" ACTION="/wakeup.htm/config">
    <INPUT type="hidden" name="hidden_wakeup_Addr" value="">
    <INPUT type="submit" name="WakeUp" VALUE="Réactivation">
  </FORM>

The embedded Virata-EmWeb web server on this endpoint triggers the
hardware wake. Empirical test:

  POST /wakeup.htm/config (Content-Type form-urlencoded,
                           body hidden_wakeup_Addr=&WakeUp=Réactivation)
  -> HTTP 405 Method Not Allowed response
  -> BUT the Z9 does actually wake up (REST goes from 503 to 200 in
    5-15 s)

The 405 indicates the body/headers are not exactly what EmWeb expects,
but the mere FACT of hitting this endpoint is enough to trigger the
wake. To test in follow-up: find the conforming body that returns a
clean 200. For now, we accept 200/302/303/405 as "wakeup triggered on
the firmware side".

Pipeline of this function:

1. POST ``https://{host}/wakeup.htm/config`` (timeout 5 s) with
   form-urlencoded body. Accepted codes: 200, 302, 303, 405. Any other
   code or TCP/SSL failure = ``unreachable``.
2. Poll ``/LFPWebServices/PI/Paper/List`` (public GET without auth)
   every 2 s until HTTP 200 OR global timeout 45 s (empirical window
   observed: ~30 s between POST wakeup and first PIWS 200).

Everything is async (route ``/api/wake`` async) — during the sleeps and
the fetches threaded via ``asyncio.to_thread``, the event loop stays
free for the other routes.
"""
import asyncio
import logging
import time
from typing import Literal, TypedDict

import requests

from lib.z9_client.rest import make_z9_session

logger = logging.getLogger(__name__)

# HTTP codes accepted as "wakeup triggered" on /wakeup.htm/config.
# 200/302/303 = normal success of a form POST; 405 = method refused by
# EmWeb but hardware wake effective (cf. docstring).
WAKEUP_TRIGGERED_CODES = {200, 302, 303, 405}

# Body and headers of the HP wakeup form (capture 24/05/2026 18h08).
WAKEUP_ENDPOINT      = "/wakeup.htm/config"
WAKEUP_BODY          = "hidden_wakeup_Addr=&WakeUp=R%C3%A9activation"
WAKEUP_CONTENT_TYPE  = "application/x-www-form-urlencoded"

# Timeouts (exposed as module-level variables -> patchable in tests).
ROOT_PROBE_TIMEOUT_S = 5
POLL_INTERVAL_S      = 2.0
# Empirical window: ~30 s typical, 47 s observed on 26/05/2026.
# 75 s covers the slow cases widely without false timeout.
POLL_TIMEOUT_S       = 75.0


class WakeResult(TypedDict, total=False):
    status: Literal["awake", "timeout", "unreachable"]
    elapsed_seconds: float
    detail: str


def _trigger_wakeup(host: str) -> bool:
    """POST ``https://{host}/wakeup.htm/config`` with form body.

    Virata-EmWeb endpoint (HP embedded web server) that triggers the
    hardware wake. Typically returns HTTP 405 (cf. firmware quirk
    documented at the top of the module), but the wake is effective
    whatever the returned code — as long as we get an HTTP response, the
    server saw the request and the firmware was solicited.

    True = wakeup triggered (codes 200/302/303/405).
    False = no HTTP response / unexpected code (Z9 off network).
    """
    session = make_z9_session()
    url = f"https://{host}{WAKEUP_ENDPOINT}"
    try:
        r = session.post(
            url,
            data=WAKEUP_BODY,
            headers={
                "Content-Type": WAKEUP_CONTENT_TYPE,
                "Referer": f"https://{host}/",
            },
            timeout=ROOT_PROBE_TIMEOUT_S,
        )
    except requests.exceptions.RequestException as e:
        logger.info("wake: POST %s fails (%s) → unreachable", url, e)
        return False
    triggered = r.status_code in WAKEUP_TRIGGERED_CODES
    logger.info(
        "wake: POST %s → status=%d %s",
        url, r.status_code,
        "(wakeup triggered)" if triggered else "(unexpected code, probably not woken up)",
    )
    return triggered


def _ping_piws(host: str) -> bool:
    """GET ``https://{host}/LFPWebServices/PI/Paper/List`` — True if 200.

    Public endpoint chosen because it is light (paper list, no firmware
    computation) and always present. When the Z9 comes out of sleep,
    this endpoint goes from 503 to 200 as soon as the REST services
    restart.
    """
    session = make_z9_session()
    url = f"https://{host}/LFPWebServices/PI/Paper/List"
    try:
        r = session.get(url, timeout=ROOT_PROBE_TIMEOUT_S)
    except requests.exceptions.RequestException:
        return False
    return r.status_code == 200


async def wake_z9(host: str) -> WakeResult:
    """Complete wake pipeline. Returns a structured dict for the
    route ``POST /api/wake``.

    Does not raise on unreachable Z9 / timeout — just returns a
    ``status`` indicating the outcome. The caller (route) propagates it
    as HTTP 200 in all cases (success and failure are just info states,
    not server errors).
    """
    start = time.monotonic()

    # ─── 1. Trigger the wakeup on the firmware side ───────────────
    triggered = await asyncio.to_thread(_trigger_wakeup, host)
    if not triggered:
        elapsed = round(time.monotonic() - start, 1)
        return WakeResult(
            status="unreachable",
            elapsed_seconds=elapsed,
            detail=(
                f"TCP/HTTPS on {host} fails, Z9 probably off "
                f"or off-network."
            ),
        )

    # ─── 2. Poll PIWS until 200 or global timeout ─────────────────
    deadline = start + POLL_TIMEOUT_S
    poll_count = 0
    while time.monotonic() < deadline:
        poll_count += 1
        if await asyncio.to_thread(_ping_piws, host):
            elapsed = round(time.monotonic() - start, 1)
            logger.info(
                "wake: Z9 awake after %.1fs (%d PIWS polls)",
                elapsed, poll_count,
            )
            return WakeResult(status="awake", elapsed_seconds=elapsed)
        await asyncio.sleep(POLL_INTERVAL_S)

    elapsed = round(time.monotonic() - start, 1)
    logger.warning(
        "wake: timeout after %.1fs (%d PIWS polls) — wakeup triggered "
        "but REST does not come back",
        elapsed, poll_count,
    )
    return WakeResult(
        status="timeout",
        elapsed_seconds=elapsed,
        detail=(
            f"Wakeup triggered but REST services still unreachable "
            f"after {POLL_TIMEOUT_S:.0f}s. Check the physical state of "
            f"the Z9 (front panel, power)."
        ),
    )
