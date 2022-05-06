import argparse
import configparser
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, List

import google.oauth2
import googleapiclient.discovery
from scoutnet import ScoutnetClient, ScoutnetMailinglist

DEFAULT_CONFIG_FILE = "scoutnet2google.ini"

DEFAULT_CONFIG_SCOUTNET = {"api_endpoint": "https://www.scoutnet.se/api"}

API_SERVICE_NAME = "admin"
API_VERSION = "directory_v1"

MAX_RESULTS = 100
CREATE_NAP = 10
SCOUTNET_RE_FILTER = ".*\\(Scoutnet\\)$"
SCOUTNET_TAG = "(Scoutnet)"

EMAIL_REWRITES = [(r"^(.+)@googlemail\.com$", "\\1@gmail.com")]


@dataclass(frozen=True)
class GoogleGroup:
    address: str
    aliases: List[str] = field(default_factory=list)
    members: List[str] = field(default_factory=list)
    title: str = None
    description: str = None


@dataclass(frozen=True)
class GoogleGroupMember:
    id: str
    email: str


class GoogleDirectory(object):
    def __init__(self, service: Any, domain: str, readonly: bool = False) -> None:
        self.service = service
        self.domain = domain
        self.readonly = readonly
        self.logger = logging.getLogger("GoogleDirectory")
        if self.readonly:
            self.logger = self.logger.getChild("READONLY")

    def sync_groups(self, groups: List[GoogleGroup]) -> None:
        """Syncronize mailing lists with Google"""
        self.delete_removed_groups(groups)
        for group in groups:
            self.logger.info("Synchronizing group %s", group.address)
            self.sync_group_info(group)
            self.sync_group_aliases(group)
            self.sync_group_members(group)

    def delete_removed_groups(self, groups: List[GoogleGroup]) -> None:
        current_groups = set(self.get_all_groups(SCOUTNET_RE_FILTER))
        old_groups = current_groups - set([group.address for group in groups])
        for group_key in old_groups:
            self.logger.info("Deleting group %s", group_key)
            if not self.readonly:
                self.service.groups().delete(groupKey=group_key).execute()

    def sync_group_info(self, group: GoogleGroup) -> None:
        """Update/create group information"""
        group_key = group.address
        group_body = {
            "email": group.address,
            "name": group.title,
            "description": group.description,
        }
        try:
            result = self.service.groups().get(groupKey=group_key).execute()
            if (
                result.get("name") == group.title
                and result.get("description") == group.description
            ):
                self.logger.debug("Group %s up to date", group_key)
            else:
                if not self.readonly:
                    result = (
                        self.service.groups()
                        .update(groupKey=group_key, body=group_body)
                        .execute()
                    )
                self.logger.info("Group %s updated", group_key)
        except Exception as exc:
            self.logger.debug("Exception: %s", str(exc))
            self.logger.warning("Group %s not found, will create", group_key)
            self.logger.debug("Creating group %s: %s", group_key, group_body)
            if not self.readonly:
                group = self.service.groups().insert(body=group_body).execute()
                try:
                    group = self.service.groups().get(groupKey=group_key).execute()
                except Exception as exc:
                    self.logger.debug("Exception: %s", str(exc))
                    self.logger.warning(
                        "Group %s not found once created, taking a short nap and retry",
                        group_key,
                    )
                    time.sleep(CREATE_NAP)
                    group = self.service.groups().get(groupKey=group_key).execute()
                self.logger.debug("Google returned group %s", group)
            self.logger.info("Group %s created", group_key)

    def sync_group_aliases(self, group: GoogleGroup) -> None:
        """Update/create group information"""
        group_key = group.address
        result = self.service.groups().aliases().list(groupKey=group_key).execute()
        if result is not None:
            current_group_aliases = set(
                entry["alias"] for entry in result.get("aliases", [])
            )
        else:
            current_group_aliases = set()
        for alias in set(group.aliases) - current_group_aliases:
            self.logger.info("Adding alias: %s", alias)
            alias_body = {"alias": alias}
            if not self.readonly:
                result = (
                    self.service.groups()
                    .aliases()
                    .insert(groupKey=group_key, body=alias_body)
                    .execute()
                )
                self.logger.debug("Insert result: %s", result)
        for alias in current_group_aliases - set(group.aliases):
            self.logger.info("Removing alias: %s", alias)
            if not self.readonly:
                result = (
                    self.service.groups()
                    .aliases()
                    .delete(groupKey=group_key, alias=alias)
                    .execute()
                )
                self.logger.debug("Delete result: %s", result)

    def sync_group_members(self, group: GoogleGroup) -> None:
        group_key = group.address
        members = set(group.members)
        all_members = self.get_all_members(group_key)
        current_members = set([x.email for x in all_members])
        email_to_id = {x.email: x.id for x in all_members}
        new_members = members - current_members
        old_members = current_members - members
        self.logger.debug("Current group members: %s", list(current_members))
        self.logger.debug("New group members: %s", list(new_members))
        self.logger.debug("Old group members: %s", list(old_members))
        for member_key in new_members:
            member_body = {"email": member_key}
            try:
                if not self.readonly:
                    self.service.members().insert(
                        groupKey=group_key, body=member_body
                    ).execute()
                self.logger.info("Added member %s to group %s", member_key, group_key)
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.error("Failed to add %s to group %s", member_key, group_key)
        for member_email in old_members:
            member_key = email_to_id[member_email]
            try:
                if not self.readonly:
                    self.service.members().delete(
                        groupKey=group_key, memberKey=member_key
                    ).execute()
                self.logger.info(
                    "Removed member %s from group %s", member_key, group_key
                )
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.error(
                    "Failed to delete %s from group %s", member_key, group_key
                )

    def get_all_groups(self, re_filter: str) -> List[str]:
        """Get all groups matching filter"""
        all_groups: List[str] = []
        token = None
        max_results = MAX_RESULTS
        while True:
            result = (
                self.service.groups()
                .list(domain=self.domain, pageToken=token, maxResults=max_results)
                .execute()
            )
            for group in result.get("groups", []):
                group_address = group["email"]
                group_name = group["name"]
                if re.match(re_filter, group_name):
                    self.logger.debug("Including group %s", group_address)
                    all_groups.append(group_address)
                else:
                    self.logger.info("Excluding group %s", group_address)
            token = result.get("nextPageToken")
            if token is None:
                break
        return all_groups

    def get_all_members(self, group_key: str) -> List[GoogleGroupMember]:
        """Get all members in group"""
        all_members: List[str] = []
        token = None
        max_results = MAX_RESULTS
        while True:
            result = (
                self.service.members()
                .list(groupKey=group_key, pageToken=token, maxResults=max_results)
                .execute()
            )
            for member in result.get("members", []):
                if "email" in member:
                    all_members.append(
                        GoogleGroupMember(
                            id=member["id"], email=member.get("email").lower()
                        )
                    )
            token = result.get("nextPageToken")
            if token is None:
                break
        return all_members


def mailinglist2groups(mlist: ScoutnetMailinglist) -> List[GoogleGroup]:
    """Convert Scoutnet mailinglist to Google groups"""
    groups = []
    members = []
    for address in mlist.aliases:
        if mlist.title is not None:
            title = f"{mlist.title} {SCOUTNET_TAG}"
            title = re.sub("@", "(a)", title)
        else:
            title = f"{mlist.id} {SCOUTNET_TAG}"
        if mlist.description is not None:
            description = re.sub("\n|\r|=", "", mlist.description.strip())
        else:
            description = None
        for recipient in mlist.recipients:
            for (pattern, repl) in EMAIL_REWRITES:
                rewritten = re.sub(pattern, repl, recipient)
                if rewritten != recipient:
                    logging.debug("Address %s rewritten to %s", recipient, rewritten)
                members.append(rewritten)
        groups.append(
            GoogleGroup(
                address=address.lower(),
                members=members,
                title=title,
                description=description,
            )
        )
    return groups


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
        "--output", dest="output", metavar="filename", help="Write all groups to file"
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

    # Optionally output all groups to file
    if args.output:
        with open(args.output, "wt") as file:
            file.write(
                json.dumps([x.__dict__ for x in all_lists], sort_keys=True, indent=4)
            )

    # Convert Scoutnet mailinglists to Google groups
    all_groups = []
    for mlist in all_lists.values():
        groups = mailinglist2groups(mlist)
        for group in groups:
            if group.address.endswith("@" + domain):
                all_groups.append(group)
            else:
                logging.warning("Ignored list with invalid domain: %s", group.address)

    # Syncronize with Google Directory
    if not args.skip_google:
        directory.sync_groups(sorted(all_groups, key=lambda g: g.address))


if __name__ == "__main__":
    main()
