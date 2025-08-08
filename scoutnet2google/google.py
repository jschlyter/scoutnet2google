import logging
import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import SCOUTNET_RE_FILTER

MAX_RESULTS = 100
CREATE_DELAY = 10


class GoogleGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    description: str

    address: str
    aliases: list[str] = Field(default=[])

    members: list[str] = Field(default=[])


class GoogleGroupMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    email: str


class GoogleDirectory:
    def __init__(self, service: Any, domain: str, readonly: bool = False) -> None:
        self.service = service
        self.domain = domain
        self.readonly = readonly
        self.logger = logging.getLogger("GoogleDirectory")
        if self.readonly:
            self.logger = self.logger.getChild("READONLY")

    def sync_groups(self, groups: list[GoogleGroup]) -> None:
        """Syncronize mailing lists with Google"""

        self.delete_removed_groups(groups)
        for group in groups:
            self.logger.info("Synchronizing group %s", group.address)
            self.sync_group_info(group)
            self.sync_group_aliases(group)
            self.sync_group_members(group)

    def delete_removed_groups(self, groups: list[GoogleGroup]) -> None:
        """Delete removed groups"""

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
                    time.sleep(CREATE_DELAY)
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
        """Sync group members"""

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

    def get_all_groups(self, re_filter: re.Pattern) -> list[str]:
        """Get all groups matching filter"""

        all_groups: list[str] = []
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
                if re_filter.match(group_name):
                    self.logger.debug("Including group %s", group_address)
                    all_groups.append(group_address)
                else:
                    self.logger.info("Excluding group %s", group_address)
            token = result.get("nextPageToken")
            if token is None:
                break

        return all_groups

    def get_all_members(self, group_key: str) -> list[GoogleGroupMember]:
        """Get all members in group"""

        all_members: list[GoogleGroupMember] = []
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
