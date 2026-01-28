import logging
import re
import time
from typing import Any

import googleapiclient.discovery
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from . import SCOUTNET_RE_FILTER

MAX_RESULTS = 100
CREATE_DELAY = 10


class GoogleGroupSettings(BaseModel):
    model_config = ConfigDict(frozen=True, alias_generator=to_camel)

    allow_external_members: bool = Field(default=True)

    def get_api_body(self) -> dict[str, str]:
        return {
            "allowExternalMembers": str(self.allow_external_members).lower(),
        }


class GoogleGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str

    email: str
    aliases: list[str] = Field(default=[])

    members: list[str] = Field(default=[])

    settings: GoogleGroupSettings = Field(default=GoogleGroupSettings())

    def get_api_body(self) -> dict[str, str]:
        return {
            "email": self.email,
            "name": self.name,
            "description": self.description,
        }


class GoogleGroupMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    email: str


class GoogleDirectory:
    def __init__(self, domain: str, credentials: Any, readonly: bool = False) -> None:
        self.domain = domain

        self.admin_service = googleapiclient.discovery.build("admin", "directory_v1", credentials=credentials)
        self.group_settings_service = googleapiclient.discovery.build("groupssettings", "v1", credentials=credentials)
        self.readonly = readonly
        self.logger = logging.getLogger("GoogleDirectory")
        if self.readonly:
            self.logger = self.logger.getChild("READONLY")

    def sync_groups(self, groups: list[GoogleGroup]) -> None:
        """Synchronize mailing lists with Google"""

        self.delete_removed_groups(groups)
        for group in groups:
            self.logger.info("Synchronizing group %s", group.email)
            self.sync_group_info(group)
            self.sync_group_settings(group)
            self.sync_group_aliases(group)
            self.sync_group_members(group)

    def delete_removed_groups(self, groups: list[GoogleGroup]) -> None:
        """Delete removed groups"""

        current_groups = set(self.get_all_groups(SCOUTNET_RE_FILTER))
        old_groups = current_groups - set([group.email for group in groups])

        for group_key in old_groups:
            self.logger.info("Deleting group %s", group_key)
            if not self.readonly:
                self.admin_service.groups().delete(groupKey=group_key).execute()

    def sync_group_info(self, group: GoogleGroup) -> None:
        """Update/create group information"""

        group_key = group.email
        group_body = group.get_api_body()

        try:
            result = self.admin_service.groups().get(groupKey=group_key).execute()
            group_info = GoogleGroup.model_validate(result)

            if group_info.name == group.name and group_info.description == group.description:
                self.logger.debug("Group %s up to date", group_key)
            else:
                if not self.readonly:
                    result = self.admin_service.groups().update(groupKey=group_key, body=group_body).execute()
                self.logger.info("Group %s updated", group_key)
        except Exception as exc:
            self.logger.debug("Exception: %s", str(exc))
            self.logger.warning("Group %s not found, will create", group_key)
            self.create_group(group)

    def sync_group_settings(self, group: GoogleGroup) -> None:
        """Update/create group settings"""

        group_key = group.email
        group_settings_body = group.settings.get_api_body()

        result = self.group_settings_service.groups().get(groupUniqueId=group_key).execute()
        group_settings = GoogleGroupSettings.model_validate(result)

        if group_settings == group.settings:
            self.logger.debug("Group settings for %s up to date", group_key)
        else:
            if not self.readonly:
                result = (
                    self.group_settings_service.groups()
                    .update(groupUniqueId=group_key, body=group_settings_body)
                    .execute()
                )
            self.logger.info("Group settings for %s updated", group_key)

    def create_group(self, group: GoogleGroup) -> None:
        """Create group"""

        group_key = group.email
        group_body = group.get_api_body()

        self.logger.debug("Creating group %s: %s", group_key, group_body)

        if not self.readonly:
            group = self.admin_service.groups().insert(body=group_body).execute()

            try:
                group = self.admin_service.groups().get(groupKey=group_key).execute()
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.warning(
                    "Group %s not found once created, taking a short nap and retry",
                    group_key,
                )
                time.sleep(CREATE_DELAY)
                group = self.admin_service.groups().get(groupKey=group_key).execute()

            self.logger.debug("Google returned group %s", group)

        self.logger.info("Group %s created", group_key)

    def sync_group_aliases(self, group: GoogleGroup) -> None:
        """Update/create group information"""

        group_key = group.email

        result = self.admin_service.groups().aliases().list(groupKey=group_key).execute()

        current_group_aliases = set(entry["alias"] for entry in result.get("aliases", [])) if result else set()

        for alias in set(group.aliases) - current_group_aliases:
            self.logger.info("Adding alias: %s", alias)
            alias_body = {"alias": alias}
            if not self.readonly:
                result = self.admin_service.groups().aliases().insert(groupKey=group_key, body=alias_body).execute()
                self.logger.debug("Insert result: %s", result)

        for alias in current_group_aliases - set(group.aliases):
            self.logger.info("Removing alias: %s", alias)
            if not self.readonly:
                result = self.admin_service.groups().aliases().delete(groupKey=group_key, alias=alias).execute()
                self.logger.debug("Delete result: %s", result)

    def sync_group_members(self, group: GoogleGroup) -> None:
        """Sync group members"""

        group_key = group.email
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
                    self.admin_service.members().insert(groupKey=group_key, body=member_body).execute()
                self.logger.info("Added member %s to group %s", member_key, group_key)
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.error("Failed to add %s to group %s", member_key, group_key)

        for member_email in old_members:
            member_key = email_to_id[member_email]
            try:
                if not self.readonly:
                    self.admin_service.members().delete(groupKey=group_key, memberKey=member_key).execute()
                self.logger.info("Removed member %s from group %s", member_key, group_key)
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.error("Failed to delete %s from group %s", member_key, group_key)

    def get_all_groups(self, re_filter: re.Pattern) -> list[str]:
        """Get all groups matching filter"""

        all_groups: list[str] = []
        token = None
        max_results = MAX_RESULTS

        while True:
            result = (
                self.admin_service.groups().list(domain=self.domain, pageToken=token, maxResults=max_results).execute()
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
                self.admin_service.members().list(groupKey=group_key, pageToken=token, maxResults=max_results).execute()
            )
            for member in result.get("members", []):
                if "email" in member:
                    all_members.append(GoogleGroupMember(id=member["id"], email=member.get("email").lower()))
            token = result.get("nextPageToken")
            if token is None:
                break

        return all_members
