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
Custom exceptions for Z9Client.

Hierarchy:
    Z9Error                          (base, all Z9 errors)
    ├── Z9ConnectionError            (network, timeout, host unreachable)
    ├── Z9AuthError                  (401, wrong admin/password)
    ├── Z9ProtocolError              (unexpected response, parsing failed)
    │   ├── Z9SOAPFault              (SOAP Fault from firmware)
    │   └── Z9RESTError              (5xx, 4xx other than auth)
    ├── Z9PaperError                 (unknown paper, already exists, etc.)
    ├── Z9CalibrationError           (CLC failed, non-calibrable paper)
    └── Z9JobError                   (profiling workflow error)
"""


class Z9Error(Exception):
    """Base exception for all Z9 errors."""
    pass


class Z9ConnectionError(Z9Error):
    """Cannot reach the Z9 (network, timeout, host down)."""
    pass


class Z9AuthError(Z9Error):
    """Authentication failure (401, wrong admin password)."""
    pass


class Z9ProtocolError(Z9Error):
    """The Z9 responds but with something unexpected."""
    pass


class Z9SOAPFault(Z9ProtocolError):
    """The Z9 firmware returned an explicit SOAP-Fault."""
    def __init__(self, fault_string, fault_code=None):
        super().__init__(f"SOAP Fault: {fault_string}")
        self.fault_code = fault_code
        self.fault_string = fault_string


class Z9RESTError(Z9ProtocolError):
    """A PIWS REST endpoint returned an error code (4xx other than 401, 5xx)."""
    def __init__(self, status_code, url, body=None):
        super().__init__(f"REST error {status_code} on {url}")
        self.status_code = status_code
        self.url = url
        self.body = body


class Z9PaperError(Z9Error):
    """Error related to paper management (custom medium)."""
    pass


class Z9CalibrationError(Z9Error):
    """Error in the calibration workflow (CLC)."""

    def __init__(self, message, code=None, description=None, context=None):
        """
        :param message: short fallback message (str)
        :param code: firmware outcome code (e.g. "NO-MEDIUM-LOADED")
        :param description: human explanation (may be multi-line)
        :param context: context (e.g. paper name, id, etc.)
        """
        super().__init__(message)
        self.code = code
        self.description = description
        self.context = context


class Z9CalibrationTimeout(Z9CalibrationError):
    """Timeout exceeded while polling a calibration."""
    pass


class Z9JobError(Z9Error):
    """Error in a long workflow (profiling, scan)."""
    pass


# === Print pipeline (TIFF/PDF → PDF/X-4 → PRN → Z9) ====================

class Z9PrintError(Z9Error):
    """Base for all print pipeline errors."""
    pass


class Z9GeometryError(Z9PrintError):
    """
    Invalid geometric parameters: negative offset, image off-sheet,
    insufficient margins, absurd dimensions, etc.
    """

    def __init__(self, message, details=None):
        """
        :param message: short message
        :param details: list of detected problems (str), or None
        """
        super().__init__(message)
        self.details = details or []


class Z9PreflightError(Z9PrintError):
    """
    Non-conformant PDF/X-4: missing output intent, missing ICC profile,
    image not 16-bit, etc. The `report` details each check.
    """

    def __init__(self, message, report=None):
        """
        :param message: short message
        :param report: PreflightReport (cf. preflight.py)
        """
        super().__init__(message)
        self.report = report


class Z9SendError(Z9PrintError):
    """
    Failure sending the PRN to port 9100 (nc unavailable, port closed,
    timeout, etc.).
    """
    pass
