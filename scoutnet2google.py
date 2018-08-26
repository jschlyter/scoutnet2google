#!/usr/bin/env python3

from typing import List, Optional, Union, Any, Set, Dict
import argparse
import configparser
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from distutils.util import strtobool
import requests
import googleapiclient.discovery
import google.auth.compute_engine
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


DEFAULT_CONFIG_FILE = 'scoutnet2google.ini'

DEFAULT_CONFIG_SCOUTNET = {
    'api_endpoint': 'https://www.scoutnet.se/api',
    'api_id': '',
    'api_key': '',
    'include_generic': 'True',
}

DEFAULT_CONFIG_GOOGLE = {
    'auth': 'standalone',
    'domain': '',
}

SCOPES = ['https://www.googleapis.com/auth/admin.directory.group']
API_SERVICE_NAME = 'admin'
API_VERSION = 'directory_v1'

CLIENT_SECRETS_FILE = "client_secret.json"
CLIENT_TOKEN_FILE = "client_token.json"
MAX_RESULTS = 100
CREATE_NAP = 10
SCOUTNET_RE_FILTER = '.*\(Scoutnet\)$'
SCOUTNET_TAG = '(Scoutnet)'

LOGGER = logging.getLogger('scoutnet2google')


@dataclass(frozen=True)
class ScoutnetMailinglist:
    id: str
    title: str = None
    description: str = None
    aliases: List[str] = field(default_factory=list)
    members: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GoogleGroup:
    address: str
    aliases: List[str] = field(default_factory=list)
    members: List[str] = field(default_factory=list)
    title: str = None
    description: str = None


class Scoutnet(object):

    def __init__(self, api_endpoint: str, api_id: str, api_key: str, domain: str) -> None:
        self.endpoint = api_endpoint
        self.session = requests.Session()
        self.session.auth = (api_id, api_key)
        self.domain = domain
        self.logger = LOGGER.getChild('Scoutnet')

    def customlists(self) -> Any:
        response = self.session.get('{}/group/customlists'.format(self.endpoint))
        return response.json()

    def get_list(self, list_data: dict) -> ScoutnetMailinglist:
        url = list_data.get('link')
        response = self.session.get(url).json()
        email_addresses = set()
        data: Dict[str, Any] = response.get('data')
        if len(data) > 0:
            for (_, member_data) in data.items():
                if 'email' in member_data:
                    email = member_data['email']['value']
                    email_addresses.add(email.lower())
                if 'extra_emails' in member_data:
                    extra_emails = json.loads(member_data['extra_emails']['value'])
                    for email in extra_emails:
                        email_addresses.add(email.lower())
        list_aliases = list_data.get('aliases', {})
        aliases = []
        if len(list_aliases) > 0:
            for alias in list(set(list_aliases.values())):
                if alias.endswith('@' + self.domain):
                    aliases.append(alias)
                else:
                    self.logger.error("Invalid domain in alias: %s", alias)
        return ScoutnetMailinglist(id=list_data['list_email_key'],
                                   members=list(email_addresses),
                                   aliases=aliases,
                                   title=list_data.get('title'),
                                   description=list_data.get('description'))


class GoogleDirectory(object):

    def __init__(self, service: Any, domain: str) -> None:
        self.service = service
        self.domain = domain
        self.logger = LOGGER.getChild('GoogleDirectory')

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
            self.service.groups().delete(groupKey=group_key).execute()

    def sync_group_info(self, group: GoogleGroup) -> None:
        """Update/create group information"""
        group_key = group.address
        group_body = {
            'email': group.address,
            'name': group.title,
            'description': group.description
        }
        try:
            result = self.service.groups().get(groupKey=group_key).execute()
            if result.get('name') == group.title and result.get('description') == group.description:
                self.logger.info("Group %s up to date", group_key)
            else:
                result = self.service.groups().update(groupKey=group_key, body=group_body).execute()
                self.logger.info("Group %s updated", group_key)
        except Exception as exc:
            self.logger.debug("Exception: %s", str(exc))
            self.logger.warning("Group %s not found, will create", group_key)
            self.logger.debug("Creating group %s: %s", group_key, group_body)
            group = self.service.groups().insert(body=group_body).execute()
            try:
                group = self.service.groups().get(groupKey=group_key).execute()
                print(group)
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.warning("Group %s not found once created, taking a short nap and retry", group_key)
                time.sleep(CREATE_NAP)
                group = self.service.groups().get(groupKey=group_key).execute()
            self.logger.info("Group %s created", group_key)

    def sync_group_aliases(self, group: GoogleGroup) -> None:
        """Update/create group information"""
        group_key = group.address
        result = self.service.groups().aliases().list(groupKey=group_key).execute()
        if result is not None:
            current_group_aliases = set(entry['alias'] for entry in result.get('aliases', []))
        else:
            current_group_aliases = set()
        for alias in set(group.aliases) - current_group_aliases:
            self.logger.info("Adding alias: %s", alias)
            alias_body = {'alias': alias}
            result = self.service.groups().aliases().insert(groupKey=group_key, body=alias_body).execute()
            print(result)
        for alias in current_group_aliases - set(group.aliases):
            self.logger.info("Removing alias: %s", alias)
            result = self.service.groups().aliases().delete(groupKey=group_key, alias=alias).execute()
            print(result)

    def sync_group_members(self, group: GoogleGroup) -> None:
        group_key = group.address
        members = set(group.members)
        current_members = set(self.get_all_members(group_key))
        new_members = members - current_members
        old_members = current_members - members
        self.logger.debug(f"Current group members: {current_members}")
        self.logger.debug(f"New group members: {new_members}")
        self.logger.debug(f"Old group members: {old_members}")
        for member_key in new_members:
            member_body = {'email': member_key}
            try:
                self.service.members().insert(groupKey=group_key, body=member_body).execute()
                self.logger.info("Added member %s to group %s", member_key, group_key)
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.error("Failed to add %s to group %s", member_key, group_key)
        for member_key in old_members:
            try:
                self.service.members().delete(groupKey=group_key, memberKey=member_key).execute()
                self.logger.info("Removed member %s from group %s", member_key, group_key)
            except Exception as exc:
                self.logger.debug("Exception: %s", str(exc))
                self.logger.error("Failed to delete %s from group %s", member_key, group_key)

    def get_all_groups(self, re_filter: str) -> List[str]:
        """Get all groups matching filter"""
        all_groups: List[str] = []
        token = None
        max_results = MAX_RESULTS
        while True:
            result = self.service.groups().list(domain=self.domain, pageToken=token, maxResults=max_results).execute()
            for group in result.get('groups', []):
                group_address = group['email']
                group_name = group['name']
                if re.match(re_filter, group_name):
                    self.logger.debug("Including group %s", group_address)
                    all_groups.append(group_address)
                else:
                    self.logger.info("Excluding group %s", group_address)
            token = result.get('nextPageToken')
            if token is None:
                break
        return all_groups

    def get_all_members(self, group_key: str) -> List[str]:
        """Get all members in group"""
        all_members: List[str] = []
        token = None
        max_results = MAX_RESULTS
        while True:
            result = self.service.members().list(groupKey=group_key, pageToken=token, maxResults=max_results).execute()
            all_members.extend(entry['email'].lower() for entry in result.get('members', []))
            token = result.get('nextPageToken')
            if token is None:
                break
        return all_members


def google_auth_installed(secret_file: str, token_file: str, scopes: List[str]) -> Credentials:
    """Authenticate installed applications with Google"""
    logger = LOGGER.getChild('google_auth_installed')
    try:
        with open(token_file, 'rt') as token_file:
            token_data = json.load(token_file)
        credentials = Credentials(
            None,
            refresh_token=token_data.get('refresh_token'),
            token_uri=token_data.get('token_uri'),
            client_id=token_data.get('client_id'),
            client_secret=token_data.get('client_secret'),
        )
    except Exception as exc:
        logging.debug("Exception: %s", str(exc))
        flow = InstalledAppFlow.from_client_secrets_file(secret_file, scopes)
        credentials = flow.run_console()
        token_data = {
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
        }
        with open(token_file, 'wt') as token_file:
            json.dump(token_data, token_file)
        logger.info("Credentials saved to %s", token_file)
    return credentials


def mailinglist2groups(mlist: ScoutnetMailinglist) -> List[GoogleGroup]:
    """Convert Scoutnet mailinglist to Google groups"""
    groups = []
    for address in mlist.aliases:
        if mlist.title is not None:
            title = f"{mlist.title} {SCOUTNET_TAG}"
        else:
            title = f"{mlist.id} {SCOUTNET_TAG}"
        if mlist.description is not None:
            description = re.sub("\n|\r|=", "", mlist.description.strip())
        else:
            description = None
        groups.append(GoogleGroup(address=address,
                                  members=mlist.members,
                                  title=title,
                                  description=description))
    return groups


def main() -> None:
    """main"""

    parser = argparse.ArgumentParser(description='Convert DNS zonefile to JSON')

    parser.add_argument('--output',
                        dest='output',
                        metavar='filename',
                        help='Write all groups to file')
    parser.add_argument('--skip-google',
                        dest='skip_google',
                        action='store_true',
                        help="Do not synchronize changes to Google Directory")
    parser.add_argument('--verbose',
                        dest='verbose',
                        action='store_true',
                        help="Enable verbose output")
    parser.add_argument('--debug',
                        dest='debug',
                        action='store_true',
                        help="Enable debugging output")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    config = configparser.ConfigParser()
    config['scoutnet'] = DEFAULT_CONFIG_SCOUTNET
    config['google'] = DEFAULT_CONFIG_GOOGLE
    config.read(DEFAULT_CONFIG_FILE)

    count = 0
    limit: Optional[Union[int, str]] = config['scoutnet'].get('limit')
    if limit is not None:
        limit = int(limit)

    if not args.skip_google:
        # Authenticate with Google
        if config['google']['auth'] == 'installed':
            credentials = google_auth_installed(CLIENT_SECRETS_FILE, CLIENT_TOKEN_FILE, SCOPES)
        elif config['google']['auth'] == 'compute_engine':
            credentials = google.auth.compute_engine.Credentials()
        else:
            LOGGER.critical("Unknown authentication method")
            sys.exit(-1)
        service = googleapiclient.discovery.build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
        directory = GoogleDirectory(service, config['google']['domain'])

    # Configure Scoutnet
    scoutnet = Scoutnet(api_endpoint=config['scoutnet']['api_endpoint'],
                        api_id=config['scoutnet']['api_id'],
                        api_key=config['scoutnet']['api_key'],
                        domain=config['google']['domain'])

    # Fetch all mailing lists from Scoutnet
    all_lists = []
    include_generic = strtobool(config['scoutnet']['include_generic'])
    for (clist, cdata) in scoutnet.customlists().items():
        count += 1
        mlist = scoutnet.get_list(cdata)
        LOGGER.info("Fetched %s: %s", mlist.id, mlist.title)
        if len(mlist.aliases) > 0:
            LOGGER.debug("Including %s: %s", mlist.id, mlist.title)
            all_lists.append(mlist)
        else:
            LOGGER.debug("Excluding %s: %s", mlist.id, mlist.title)
        if limit is not None and count >= limit:
            break

    # Optionally output all groups to file
    if args.output:
        with open(args.output, 'wt') as file:
            file.write(json.dumps([x.__dict__ for x in all_lists], sort_keys=True, indent=4))

    # Convert Scoutnet mailinglists to Google groups
    all_groups = []
    for mlist in all_lists:
        all_groups.extend(mailinglist2groups(mlist))

    # Syncronize with Google Directory
    if not args.skip_google:
        directory.sync_groups(all_groups)


if __name__ == "__main__":
    main()
