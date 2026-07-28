"""
z9_client: Python library to drive the HP DesignJet Z9 via its REST
           PIWS API (port 443) and its internal SOAP (ports 8085/8086).

Typical usage:
    from lib.z9_client import Z9Client

    client = Z9Client.from_env()
    print(client.identification()["ModelName"])
    for p in client.paper.list():
        print(p["name"])

    # Printing pipeline:
    from lib.z9_client import PrintJob
    job = PrintJob.for_tiff("photo.tif", paper_id="...", sheet_w_mm=297, sheet_h_mm=420)
    client.print.send(job.centered())

Expected environment variables:
    Z9_HOST      : Z9 IP address (e.g. 192.168.1.50)
    Z9_ADMIN_PWD : admin password (optional, required for Settings/PrinterSettings)
    Z9_TIMEOUT   : timeout in seconds (default 10)

Put these variables in a .env file at the project root,
or in ~/.zshrc:
    export Z9_HOST=192.168.1.50
    export Z9_ADMIN_PWD='...'
"""

from .client import Z9Client
from .exceptions import (
    Z9Error,
    Z9ConnectionError,
    Z9AuthError,
    Z9ProtocolError,
    Z9SOAPFault,
    Z9RESTError,
    Z9PaperError,
    Z9CalibrationError,
    Z9CalibrationTimeout,
    Z9JobError,
    # Printing pipeline
    Z9PrintError,
    Z9GeometryError,
    Z9PreflightError,
    Z9SendError,
)
from .events import LEDMEventReader
from .printing import PrintJob, PrintOps, PrintResult, TiffInfo
from .preflight import (
    preflight_pdfx4,
    format_report as format_preflight_report,
    PreflightReport,
    PreflightCheck,
)

__all__ = [
    "Z9Client",
    "Z9Error",
    "Z9ConnectionError",
    "Z9AuthError",
    "Z9ProtocolError",
    "Z9SOAPFault",
    "Z9RESTError",
    "Z9PaperError",
    "Z9CalibrationError",
    "Z9CalibrationTimeout",
    "Z9JobError",
    # Printing pipeline
    "Z9PrintError",
    "Z9GeometryError",
    "Z9PreflightError",
    "Z9SendError",
    "LEDMEventReader",
    "PrintJob",
    "PrintOps",
    "PrintResult",
    "TiffInfo",
    "preflight_pdfx4",
    "format_preflight_report",
    "PreflightReport",
    "PreflightCheck",
]

__version__ = "0.2.0"
