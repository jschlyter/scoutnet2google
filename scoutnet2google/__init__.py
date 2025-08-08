import re
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("scoutnet2google")
except PackageNotFoundError:
    __version__ = "0.0.0"

DEFAULT_CONFIG_FILE = "scoutnet2google.ini"
DEFAULT_CONFIG_SCOUTNET = {"api_endpoint": "https://www.scoutnet.se/api"}

SCOUTNET_RE_FILTER = re.compile(r".*\(Scoutnet\)$")
SCOUTNET_TAG = "(Scoutnet)"

EMAIL_REWRITES = [(re.compile(r"^(.+)@googlemail\.com$"), "\\1@gmail.com")]

API_SERVICE_NAME = "admin"
API_VERSION = "directory_v1"
