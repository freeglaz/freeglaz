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
Translation of the outcome codes returned by the Z9 firmware in the SOAP
responses.

The firmware uses short codes in an `outcome="..."` attribute on the
responses (NEW-CALIBRATION-RESPONSE, NEW-PROFILE-RESPONSE, GET-STATUS-RESPONSE,
etc.). These codes are structured and discriminating, but not very readable
as-is for a user.

This module provides `describe(code)` to translate into a human message, and
`is_error(code)` to discriminate success vs error.

NOTE: This dictionary is fed **empirically** as observations accumulate.
We do NOT invent codes: we only add them when we have actually seen them
in a real Z9 response.
"""


# Codes observed empirically (observation date and context)
OUTCOME_DESCRIPTIONS = {
    # Success
    "OK": "Success",

    # newCalibration / newProfile refusals
    "MEDIUM-MISMATCH": (
        "The requested paper is not the one currently loaded in the Z9. "
        "Load the correct paper or choose another one."
    ),
    "BADSIZE-MEDIUM": (
        "The loaded paper size is insufficient. "
        "Native workflows (calibration, profiling) require at least "
        "A3 (297 x 420 mm) on most papers."
    ),
    "NO-MEDIUM-LOADED": (
        "No paper loaded in the Z9. "
        "Load a sheet (manual feed) or a roll before starting "
        "the operation."
    ),

    # getStatus polling
    "UNKNOWN-OPERATION": (
        "The operation is not (or no longer) known to the Z9. "
        "The job was probably purged after completion "
        "(success or failure to be checked via paper.details())."
    ),
}


# Codes we consider errors (non-OK)
# All known codes except OK are errors by default.
# A few special cases could be added here (warnings, etc.)
_KNOWN_ERROR_CODES = set(OUTCOME_DESCRIPTIONS.keys()) - {"OK"}


def describe(code, fallback=None):
    """
    Return a human message for an outcome code.

    :param code: firmware code (e.g. "BADSIZE-MEDIUM")
    :param fallback: message if the code is unknown.
                     Default: "Undocumented firmware code: <code>"
    :return: str
    """
    if code in OUTCOME_DESCRIPTIONS:
        return OUTCOME_DESCRIPTIONS[code]
    if fallback is not None:
        return fallback
    return f"Undocumented firmware code: '{code}'"


def is_error(code):
    """
    Determine whether an outcome code represents an error.

    "OK" → False
    Any other code (known or unknown) → True

    Note: we choose caution — an unknown code is treated as an
    error until we have validated its behavior.

    :param code: firmware code
    :return: bool
    """
    return code != "OK"


def format_error(code, context=None):
    """
    Compose a full error message with code + description + context.

    :param code: outcome code
    :param context: optional context string (e.g. paper name)
    :return: str

    Example:
        >>> format_error("BADSIZE-MEDIUM", "Hahnemühle Baryta A4")
        "BADSIZE-MEDIUM: The loaded paper size is insufficient.
         Native CLC requires at least A3 (297 x 420 mm) on most
         papers. (context: Hahnemühle Baryta A4)"
    """
    msg = f"{code}: {describe(code)}"
    if context:
        msg += f" (context: {context})"
    return msg
