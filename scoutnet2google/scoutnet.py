import logging
import re

from scoutnet import ScoutnetMailinglist

from . import EMAIL_REWRITES, SCOUTNET_TAG
from .google import GoogleGroup


def mailinglist2groups(mlist: ScoutnetMailinglist) -> list[GoogleGroup]:
    """Convert Scoutnet mailing list to Google groups"""

    groups = []
    members = []

    for address in mlist.aliases:
        if mlist.title is not None:
            title = f"{mlist.title} {SCOUTNET_TAG}"
            title = re.sub("@", "(a)", title)
        else:
            title = f"{mlist.id} {SCOUTNET_TAG}"

        description = (
            re.sub(r"\n|\r|=", "", mlist.description.strip())
            if mlist.description
            else ""
        )

        for recipient in mlist.recipients or []:
            for pattern, repl in EMAIL_REWRITES:
                rewritten = pattern.sub(repl, recipient)
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
