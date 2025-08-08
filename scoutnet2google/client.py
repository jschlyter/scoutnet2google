import argparse
import configparser
import json
import logging

import google.oauth2
import googleapiclient.discovery
from scoutnet import ScoutnetClient

from . import (
    API_SERVICE_NAME,
    API_VERSION,
    DEFAULT_CONFIG_FILE,
    DEFAULT_CONFIG_SCOUTNET,
)
from .google import GoogleDirectory
from .scoutnet import mailinglist2groups


def main() -> None:
    """main"""

    parser = argparse.ArgumentParser(
        description="Convert Scoutnet mailinglist to Google groups"
    )

    parser.add_argument(
        "--limit",
        dest="limit",
        metavar="N",
        type=int,
        help="Only process n groups (dangerous!)",
    )
    parser.add_argument(
        "--output",
        dest="output",
        metavar="filename",
        help="Write all Google groups to file",
    )
    parser.add_argument(
        "--skip-google",
        dest="skip_google",
        action="store_true",
        help="Do not synchronize changes to Google Directory",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Test mode (no changes written)",
    )
    parser.add_argument(
        "--verbose", dest="verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--debug", dest="debug", action="store_true", help="Enable debugging output"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
        logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.DEBUG)
        logging.getLogger("googleapiclient.discovery").setLevel(logging.DEBUG)

    config = configparser.ConfigParser()
    config["scoutnet"] = DEFAULT_CONFIG_SCOUTNET
    config.read(DEFAULT_CONFIG_FILE)

    domain = config["google"]["domain"]

    if not args.skip_google:
        credentials = (
            google.oauth2.service_account.Credentials.from_service_account_file(
                config["google"]["service_account_file"]
            )
        )
        service = googleapiclient.discovery.build(
            API_SERVICE_NAME, API_VERSION, credentials=credentials
        )
        directory = GoogleDirectory(service, domain, args.dry_run)

    # Configure Scoutnet
    scoutnet = ScoutnetClient(
        api_endpoint=config["scoutnet"]["api_endpoint"],
        api_id=config["scoutnet"]["api_id"],
        api_key_customlists=config["scoutnet"]["api_key"],
    )

    # Fetch all mailing lists from Scoutnet
    all_lists = scoutnet.get_all_lists(args.limit)

    # Convert Scoutnet mailinglists to Google groups
    all_groups = []
    for mlist in all_lists.values():
        groups = mailinglist2groups(mlist)
        for group in groups:
            if group.address.endswith("@" + domain):
                all_groups.append(group)
            else:
                logging.warning("Ignored list with invalid domain: %s", group.address)

    # Optionally output all groups to file
    if args.output:
        with open(args.output, "w") as file:
            file.write(
                json.dumps([x.__dict__ for x in all_groups], sort_keys=True, indent=4)
            )

    # Syncronize with Google Directory
    if not args.skip_google:
        directory.sync_groups(sorted(all_groups, key=lambda g: g.address))


if __name__ == "__main__":
    main()
