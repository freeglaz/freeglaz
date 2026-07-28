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
Helpers for the PIWS REST API of the HP DesignJet Z9.

Endpoints accessible via HTTPS on the standard port 443,
path /LFPWebServices/PI/...

Three classes of endpoints detected:
  - PUBLIC: no authentication required (Identification, MediaSystem,
             DeviceStatus, Calibrations, PrintheadsStatus, InkSystem,
             Paper/List, Paper/Categories, DeviceCounters, Workflows,
             SwatchBook/SwatchBookFormulas, PrinterMaintenance)
  - PROTECTED: Basic Auth admin:[ADMIN_PWD] required (Settings, PrinterSettings)
  - DYNAMIC/OTHER: 404 out of activity, 503 (Alerts, Eventing, JQ,
                      InformationPages, AccessControl, Security)

The Z9 uses a self-signed certificate, so verify=False by default.

SSL NOTE:
The Z9's HP Jetdirect firmware negotiates in TLS 1.2 with old ciphers
(AES256-GCM-SHA384 without modern ALPN/PFS). Recent OpenSSL (macOS
Sequoia+, modern Linux) rejects this handshake by default.

So we use a custom HTTPSAdapter that forces a permissive cipher set
and SECLEVEL=0 to stay compatible with the HP firmware.
"""

import ssl

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .exceptions import (
    Z9Error,
    Z9ConnectionError,
    Z9AuthError,
    Z9RESTError,
)

# Disable self-signed certificate warnings (the Z9 has a self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# Permissive cipher set compatible with the HP Jetdirect firmware
# DEFAULT = standard OpenSSL ciphers
# @SECLEVEL=0 = allows legacy ciphers (RC4, AES-CBC, etc.)
HP_LEGACY_CIPHERS = "DEFAULT@SECLEVEL=0"


class HPLegacyHTTPSAdapter(HTTPAdapter):
    """
    HTTPSAdapter that forces a cipher set compatible with old HP
    Jetdirect firmwares (Z9, Z6, Z3200 series).

    Without this adapter, modern Python raises:
        SSLError: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]
    """

    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(ciphers=HP_LEGACY_CIPHERS)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            context.minimum_version = ssl.TLSVersion.TLSv1
        except AttributeError:
            pass
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        context = create_urllib3_context(ciphers=HP_LEGACY_CIPHERS)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = context
        return super().proxy_manager_for(*args, **kwargs)


def make_z9_session():
    """
    Create a requests.Session() configured to talk to the Z9:
      - HP legacy cipher set
      - No cert verification (self-signed)
      - Keep-alive enabled
    """
    session = requests.Session()
    session.verify = False
    session.mount("https://", HPLegacyHTTPSAdapter())
    return session


class PIWSClient:
    """
    REST client for the PIWS endpoints of the Z9.

    Usage:
        piws = PIWSClient(host="192.168.1.50", admin_pwd="...")
        xml = piws.get("/Identification.xml")
        xml = piws.get("/Settings", auth=True)
    """

    BASE_PATH = "/LFPWebServices/PI"

    def __init__(self, host, admin_pwd=None, timeout=10):
        """
        :param host: IP or hostname of the Z9 (e.g. "192.168.1.50")
        :param admin_pwd: Admin password for the protected endpoints
        :param timeout: Default timeout in seconds
        """
        self.host = host
        self.admin_pwd = admin_pwd
        self.timeout = timeout
        # HTTPS session configured for the Z9 (HP legacy ciphers)
        self._session = make_z9_session()

    def _build_url(self, endpoint):
        """Build the complete URL from a relative endpoint."""
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"https://{self.host}{self.BASE_PATH}{endpoint}"

    def get(self, endpoint, auth=False, raw=False, extra_headers=None):
        """
        GET on a PIWS endpoint.

        :param endpoint: Relative path (e.g. "/Identification.xml")
        :param auth: If True, sends Basic admin/password authentication
        :param raw: If True, returns the raw bytes; otherwise the decoded text
        :param extra_headers: optional dict of HTTP headers to send in
                              addition (e.g. ``{"Accept": "application/json"}``
                              for endpoints that do content negotiation on
                              the firmware side, like
                              ``/JQ/JobQueueCollection``).
        :return: Response content (str or bytes)
        :raises Z9ConnectionError: network, timeout, SSL
        :raises Z9AuthError: 401
        :raises Z9RESTError: other 4xx/5xx code
        """
        url = self._build_url(endpoint)
        kwargs = {"timeout": self.timeout}
        if auth:
            if not self.admin_pwd:
                raise Z9AuthError(
                    "Endpoint requires auth but admin_pwd not configured. "
                    "Set Z9_ADMIN_PWD env variable."
                )
            kwargs["auth"] = ("admin", self.admin_pwd)
        if extra_headers:
            kwargs["headers"] = dict(extra_headers)

        try:
            response = self._session.get(url, **kwargs)
        except requests.exceptions.SSLError as e:
            raise Z9ConnectionError(
                f"SSL handshake error with {self.host}. "
                f"This usually means cipher mismatch with HP firmware. "
                f"Details: {e}"
            )
        except requests.exceptions.ConnectTimeout:
            raise Z9ConnectionError(f"Timeout connecting to {self.host}")
        except requests.exceptions.ConnectionError as e:
            raise Z9ConnectionError(f"Cannot reach {self.host}: {e}")
        except requests.exceptions.RequestException as e:
            raise Z9ConnectionError(f"Network error on {url}: {e}")

        if response.status_code == 401:
            raise Z9AuthError(
                f"Authentication failed on {endpoint}. "
                f"Check Z9_ADMIN_PWD value."
            )
        if response.status_code >= 400:
            raise Z9RESTError(response.status_code, url, response.text)

        if raw:
            return response.content

        # Force UTF-8: the Z9 sends XML in UTF-8 (declared in the prologue
        # <?xml version="1.0" encoding="UTF-8"?>) but does not always
        # specify the charset in the Content-Type header. requests then
        # guesses latin-1 and breaks the accents (Hahnemühle → HahnemÃ¼hle).
        response.encoding = "utf-8"
        return response.text

    def ping(self):
        """
        Check that the Z9 responds. Returns True if OK, False otherwise.

        Only catches Z9Error (timeouts, SSL, auth, etc.).
        Other exceptions (Python bugs) propagate normally.
        """
        try:
            self.get("/Identification.xml")
            return True
        except Z9Error:
            return False

    def close(self):
        """Close the keep-alive HTTPS session. Called at backend
        shutdown (robustness). Idempotent."""
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def request(self, method, endpoint, json=None, auth=False, raw=False,
                extra_headers=None, files=None, data=None):
        """
        Arbitrary HTTP method (POST/PUT/etc.) with a JSON or multipart body.

        Used for PIWS operations that are not GETs:
          - PUT /Paper/Delete       (delete a paper)
          - POST /Paper/GetResource (export ICC)
          - POST /Paper/Operation   (rename / clone)
          - POST /Paper/SetResource (import ICC, multipart with `files`)
          - etc.

        :param method: "GET", "POST", "PUT", "DELETE"
        :param endpoint: relative path (e.g. "/Paper/Delete")
        :param json: dict sent as JSON in the body (mutually exclusive with files/data)
        :param auth: if True, sends Basic admin auth
        :param raw: if True, returns bytes; otherwise decoded str
        :param extra_headers: dict of headers to add
        :param files: dict or list of tuples (name, (filename, content, content_type))
                      for multipart/form-data requests. requests handles the encoding.
        :param data: dict sent form-encoded or raw body (with files)

        :return: bytes or str
        :raises Z9ConnectionError, Z9AuthError, Z9RESTError: depending on the case
        """
        url = self._build_url(endpoint)
        kwargs = {"timeout": self.timeout}
        if auth:
            if not self.admin_pwd:
                raise Z9AuthError(
                    "Endpoint requires auth but admin_pwd not configured."
                )
            kwargs["auth"] = ("admin", self.admin_pwd)

        # HP expects "Force-Content-Type: true" to accept the PIWS
        # operations that don't have a strict Content-Type on the firmware side
        headers = {"Force-Content-Type": "true"}
        if json is not None:
            if files is not None or data is not None:
                raise ValueError("Cannot mix 'json' with 'files'/'data'")
            kwargs["json"] = json
        if files is not None:
            kwargs["files"] = files
        if data is not None:
            kwargs["data"] = data
        if extra_headers:
            headers.update(extra_headers)
        kwargs["headers"] = headers

        try:
            response = self._session.request(method, url, **kwargs)
        except requests.exceptions.SSLError as e:
            raise Z9ConnectionError(
                f"SSL handshake error with {self.host}. Details: {e}"
            )
        except requests.exceptions.ConnectTimeout:
            raise Z9ConnectionError(f"Timeout connecting to {self.host}")
        except requests.exceptions.ConnectionError as e:
            raise Z9ConnectionError(f"Cannot reach {self.host}: {e}")
        except requests.exceptions.RequestException as e:
            raise Z9ConnectionError(f"Network error on {url}: {e}")

        if response.status_code == 401:
            raise Z9AuthError(
                f"Authentication failed on {endpoint}. Check Z9_ADMIN_PWD."
            )
        if response.status_code >= 400:
            raise Z9RESTError(response.status_code, url, response.text)

        if raw:
            return response.content
        response.encoding = "utf-8"
        return response.text

    def fetch_fs(self, uri):
        """
        Download a binary file from the Z9 temporary filesystem.

        PIWS REST operations like Paper/GetResource return a URI
        of the form '/LFPWebServices/FS/{uuid}' that points to a binary
        file (ICC, etc.) accessible separately from /LFPWebServices/PI/.

        :param uri: path starting with /LFPWebServices/FS/...
                    (retrieved from a previous REST response)
        :return: bytes of the file
        :raises Z9ConnectionError, Z9RESTError: depending on the case
        """
        if not uri.startswith("/"):
            uri = "/" + uri
        url = f"https://{self.host}{uri}"

        try:
            response = self._session.get(url, timeout=self.timeout)
        except requests.exceptions.SSLError as e:
            raise Z9ConnectionError(
                f"SSL handshake error with {self.host}. Details: {e}"
            )
        except requests.exceptions.ConnectTimeout:
            raise Z9ConnectionError(f"Timeout connecting to {self.host}")
        except requests.exceptions.ConnectionError as e:
            raise Z9ConnectionError(f"Cannot reach {self.host}: {e}")
        except requests.exceptions.RequestException as e:
            raise Z9ConnectionError(f"Network error on {url}: {e}")

        if response.status_code == 401:
            raise Z9AuthError(f"Authentication failed on {uri}")
        if response.status_code >= 400:
            raise Z9RESTError(response.status_code, url, response.text)

        return response.content
