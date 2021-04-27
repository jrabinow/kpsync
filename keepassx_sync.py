#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync DB passwords according to syncconfig.ini
"""

from typing import List, Optional

# from pykeepass_cache import PyKeePass, cached_databases
from pykeepass import PyKeePass as PyKeePassNoCache
from pykeepass.exceptions import CredentialsError
from datetime import datetime

import argparse
import configparser
import getpass
import logging
import os
import stat
import sys

import rpyc

rpyc.core.vinegar._generic_exceptions_cache[
    "pykeepass.exceptions.CredentialsError"
] = CredentialsError

LOG: logging.Logger = logging.getLogger()
LOG.setLevel("INFO")
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setFormatter(formatter)
LOG.addHandler(ch)


def parse_args() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="enable debug logging information",
    )
    parser.add_argument(
        "--db",
        nargs="*",
        action="append",
        default=[],
        metavar=("database", "keyfile"),
        help="""replace db entries in syncconfig.ini with path/to/database path/to/keyfile.
Use this option once per database:
>>> {} --db db1 keyfile_for_db1 --db db2 --db db3 keyfile_for_db3\n
""".format(
            os.path.basename(sys.argv[0])
        ),
    )
    parser.add_argument(
        "--config", default="syncconfig.ini", help="manually specify config file"
    )

    args = parser.parse_args()
    args.database = [(db[0], db[1] if len(db) > 1 else None) for db in args.db]
    del args.db
    return args


def is_dir_world_readable(directory: str = ".") -> bool:
    st: os.stat_result = os.stat(directory)
    return bool(st.st_mode & stat.S_IROTH)


def parse_config(args):
    configfile = args.config or "syncconfig.ini"
    config = configparser.ConfigParser()
    config.read(configfile)
    entries = config["entries"]["entries"].strip().split("\n")

    db_list = args.database
    if len(db_list) == 0:
        for db_name, data in config["db"].items():
            db_info = data.strip().split("\n")
            db_file = os.path.expanduser(os.path.expandvars(db_info[0]))
            db_key = (
                os.path.expanduser(os.path.expandvars(db_info[1]))
                if len(db_info) > 1
                else None
            )
            db_list.append((db_file, db_key))

    return db_list, entries


def persist_entry(db_file, uptodate_entry):
    group, dirty = ensure_group(
        db_file,
        uptodate_entry.group.path,
        uptodate_entry.group.icon,
        uptodate_entry.group.notes,
    )
    existing_entry = db_file.find_entries_by_path(uptodate_entry.path)
    if existing_entry is None:
        LOG.info("adding {} to {} ({})".format(uptodate_entry, group, db_file.filename))
        dirty = True
        updated_entry = db_file.add_entry(
            group,
            uptodate_entry.title,
            uptodate_entry.username or "",
            uptodate_entry.password or "",
            url=uptodate_entry.url,
            notes=uptodate_entry.notes,
            expiry_time=uptodate_entry.expiry_time if uptodate_entry.expires else None,
            tags=uptodate_entry.tags,
            icon=uptodate_entry.icon,
        )
    else:
        if (
            existing_entry.title != uptodate_entry.title
            or existing_entry.username != uptodate_entry.username
            or existing_entry.password != uptodate_entry.password
            or existing_entry.url != uptodate_entry.url
            or existing_entry.notes != uptodate_entry.notes
            or (
                uptodate_entry.expires
                and existing_entry.expiry_time != uptodate_entry.expiry_time
            )
            or existing_entry.tags != uptodate_entry.tags
            or existing_entry.icon != uptodate_entry.icon
        ):
            LOG.info(
                "updating existing uptodate_entry {} in group {} ({})".format(
                    uptodate_entry, group, db_file.filename
                )
            )
            dirty = True
            existing_entry.title = uptodate_entry.title
            if uptodate_entry.username is not None:
                existing_entry.username = uptodate_entry.username
            if uptodate_entry.password is not None:
                existing_entry.password = uptodate_entry.password
            if uptodate_entry.url is not None:
                existing_entry.url = uptodate_entry.url
            if uptodate_entry.notes is not None:
                existing_entry.notes = uptodate_entry.notes
            # workaround expiry_time always set
            if (
                uptodate_entry.expires
                or uptodate_entry.expires != existing_entry.expires
            ) and uptodate_entry.expiry_time != existing_entry.expiry_time:
                existing_entry.expiry_time = uptodate_entry.expiry_time
            if uptodate_entry.tags is not None:
                existing_entry.tags = uptodate_entry.tags
            if uptodate_entry.icon is not None:
                existing_entry.icon = uptodate_entry.icon
    updated_entry = existing_entry

    return updated_entry, dirty


def group_obj_nothrows_on_missing(db, group_name):
    group_list = db.find_groups_by_name(group_name)
    return group_list[0] if len(group_list) > 0 else None


def ensure_group(db, group_path, icon=None, notes=None):
    dirty = False
    group = db.find_groups_by_path(group_path)

    if group is None:
        if len(group_path) <= 0:
            group = db.root_group
        else:
            parent_group, dirty = ensure_group(db, group_path[:-1])
            dirty = True
            LOG.info("adding group {} to {}".format("/".join(group_path), db.filename))
            group = db.add_group(parent_group, group_path[-1], icon, notes)

    return group, dirty


def sync_entry(
    db_handles: List,
    entry: str,
) -> List:

    group_name = os.path.dirname(entry)
    entry_title = os.path.basename(entry)

    entry_dict = {}
    for handle in db_handles:
        matching_entries = handle.find_entries_by_title(
            entry_title,
            group=group_obj_nothrows_on_missing(handle, group_name),
            flags="I",
        )
        assert (
            len(matching_entries) <= 1
        ), "more than 2 entries found for '{}' in {}".format(
            entry_title, handle.filename
        )
        entry_dict[handle] = matching_entries[0] if len(matching_entries) > 0 else None

    uptodate_db, uptodate_entry = max(
        entry_dict.items(),
        key=lambda e: e[1].ctime.timestamp() if e[1] is not None else -1,
    )

    if uptodate_entry is None:
        raise KeyError(
            "failed to find entry '{}' in both databases. Check the entry title for typos".format(
                entry_title
            )
        )
    else:
        updated_dbs = set()
        for handle in db_handles:
            if handle != uptodate_db:
                _, dirty = persist_entry(handle, uptodate_entry)
                if dirty:
                    updated_dbs.add(handle)

        return updated_dbs


def create_db_handle(
    db_filepath,
    db_keypath=None,
    use_cache=True,
    socket_path: str = "./pykeepass_socket",
):

    use_cache: bool = not is_dir_world_readable()

    password: Optional[str] = None
    if use_cache:
        # if db_filepath not in cached_databases(socket_path=socket_path):
        #    password: str = getpass.getpass(
        #        prompt="Password for {}: ".format(db_filepath)
        #    )
        # kp = PyKeePass(
        #    db_filepath,
        #    password=password,
        #    keyfile=db_keypath,
        #    timeout=600,
        #    socket_path=socket_path,
        # )
        password = getpass.getpass(prompt="Password for {}: ".format(db_filepath))
        try:
            kp = PyKeePassNoCache(
                db_filepath,
                password=password,
                keyfile=db_keypath,
            )
        except FileNotFoundError as e:
            LOG.fatal("file not found: {}".format(e))
        return kp
    return None


def main():
    args = parse_args()
    db_list, entries = parse_config(args)

    try:
        db_handles = [
            create_db_handle(db_filepath, key_path) for db_filepath, key_path in db_list
        ]
    except CredentialsError as e:
        LOG.fatal("bad credentials: {}".format(e))
        exit(1)

    dbs_to_save = set()
    for entry in entries:
        updated_dbs = sync_entry(db_handles, entry)
        dbs_to_save.update(updated_dbs)

    for db in dbs_to_save:
        LOG.info("saving db {}".format(db.filename))
        db.save()


if __name__ == "__main__":
    main()
