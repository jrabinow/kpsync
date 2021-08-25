#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync DB passwords according to syncconfig.yml
"""

import argparse
import getpass
import logging
import json
import os
import stat
import sys
from collections import namedtuple
from typing import Dict, List, Optional, Set, Tuple

import strictyaml as yaml
from pykeepass import PyKeePass as PyKeePassNoCache
from pykeepass.entry import Entry
from pykeepass.exceptions import CredentialsError
from pykeepass.group import Group
from xdg import xdg_config_home

LOG: logging.Logger = logging.getLogger()
LOG.setLevel("INFO")
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setFormatter(formatter)
LOG.addHandler(ch)


Database = namedtuple("Database", ["dbname", "dbfile", "keyfile"])
Job = namedtuple("Job", ["jobname", "db", "entries"])


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
    parser.add_argument("--config", help="manually specify config file")
    subparsers: argparse._SubParsersAction = parser.add_subparsers(
        dest="command", required=True
    )
    listparser: argparse.ArgumentParser = subparsers.add_parser(
        "list", help="list entities in the config file"
    )
    listparser.add_argument("-v", "--verbose", action="store_true", help="verbose mode")
    listparser.add_argument("ENTITY_TYPE", choices=["all", "db", "jobs"])

    runparser: argparse.ArgumentParser = subparsers.add_parser("run", help="run a job")
    runparser.add_argument(
        "--dry-run",
        action="store_true",
        help="don't save dbs, just print what would be done",
    )
    runparser.add_argument(
        "JOB_NAME", help="specify job name", nargs="*", default=["default"]
    )

    syncparser: argparse.ArgumentParser = subparsers.add_parser(
        "sync",
        help="specify dbs and entries to sync from the command-line. DBs must be registered in the config file",
    )
    syncparser.add_argument(
        "--dry-run",
        action="store_true",
        help="don't save dbs, just print what would be done",
    )
    syncparser.add_argument(
        "--db",
        required=True,
        nargs="+",
        help="db name, either registered in syncconfig.yml or in DBFILEPATH[:KEYFILEPATH] format",
    )
    syncparser.add_argument(
        "--entries", required=True, nargs="+", help="list of entries"
    )

    args: argparse.Namespace = parser.parse_args()
    return args


def is_dir_world_readable(directory: str = ".") -> bool:
    st: os.stat_result = os.stat(directory)
    return bool(st.st_mode & stat.S_IROTH)


def parse_config(configfile: str = None) -> Tuple[Dict[str, Database], Dict[str, Job]]:

    databases: Dict[str, Database] = {}
    jobs: Dict[str, Job] = {}

    if configfile is None:
        configfile = "syncconfig.yml"
        if not os.path.isfile(configfile):
            configfile = "{}/kpsync/syncconfig.yml".format(xdg_config_home())

    if os.path.isfile(configfile):
        config_schema: yaml.compound.Map = yaml.Map(
            {
                "db": yaml.MapPattern(
                    yaml.Str(), yaml.Map({"dbfile": yaml.Str(), "keyfile": yaml.Str()})
                ),
                "job": yaml.MapPattern(
                    yaml.Str(),
                    yaml.Map(
                        {"db": yaml.Seq(yaml.Str()), "entries": yaml.Seq(yaml.Str())}
                    ),
                ),
            }
        )
        with open(configfile) as f:
            data: str = f.read()
        config: Dict[str, Dict] = yaml.load(data, config_schema).data

    try:
        for dbname, dbvalues in config["db"].items():
            dbfile = os.path.expanduser(os.path.expandvars(dbvalues["dbfile"]))
            keyfile = os.path.expanduser(os.path.expandvars(dbvalues["keyfile"]))
            databases[dbname] = Database(dbname, dbfile, keyfile)
        for jobname, jobvalues in config["job"].items():
            jobs[jobname] = Job(jobname, jobvalues["db"], jobvalues["entries"])
    except KeyError as e:
        LOG.critical("malformed or missing syncconfig.yml file: {}".format(e))
        exit(0)

    return databases, jobs


def persist_entry(
    db_file: PyKeePassNoCache, uptodate_entry: Entry
) -> Tuple[Entry, bool]:
    group, dirty = ensure_group(
        db_file,
        uptodate_entry.group.path,
        uptodate_entry.group.icon,
        uptodate_entry.group.notes,
    )
    updated_entry: Entry
    existing_entry: Optional[Entry] = db_file.find_entries_by_path(uptodate_entry.path)
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
                "updating {}: {}/{}".format(db_file.filename, group, uptodate_entry)
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


def group_obj_nothrows_on_missing(
    db: PyKeePassNoCache, group_name: str
) -> Optional[Group]:
    group_list: List[Group] = db.find_groups_by_name(group_name)
    return group_list[0] if len(group_list) > 0 else None


def ensure_group(
    db: PyKeePassNoCache, group_path: List[str], icon: bytes = None, notes: str = None
) -> Tuple[Group, bool]:

    dirty: bool = False
    group: Group = db.find_groups_by_path(group_path)

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
    db_handles: List[PyKeePassNoCache],
    entry: str,
) -> Set[PyKeePassNoCache]:

    group_name: str = os.path.dirname(entry)
    entry_title: str = os.path.basename(entry)

    entry_dict: Dict[PyKeePassNoCache, Optional[Entry]] = {}
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
        updated_dbs: Set[PyKeePassNoCache] = set()
        for handle in db_handles:
            if handle != uptodate_db:
                _, dirty = persist_entry(handle, uptodate_entry)
                if dirty:
                    updated_dbs.add(handle)

        return updated_dbs


def create_db_handle(
    db_filepath: str,
    db_keypath: str = None,
    use_cache: bool = True,
    socket_path: str = "./pykeepass_socket",
) -> PyKeePassNoCache:

    # use_cache = not is_dir_world_readable()

    # if use_cache:
    #     if db_filepath not in cached_databases(socket_path=socket_path):
    #        password: str = getpass.getpass(
    #            prompt="Password for {}: ".format(db_filepath)
    #        )
    #     kp = PyKeePass(
    #        db_filepath,
    #        password=password,
    #        keyfile=db_keypath,
    #        timeout=600,
    #        socket_path=socket_path,
    #     )
    password: str = getpass.getpass(prompt="Password for {}: ".format(db_filepath))
    try:
        kp: PyKeePassNoCache = PyKeePassNoCache(
            db_filepath,
            password=password,
            keyfile=db_keypath,
        )
    except FileNotFoundError as e:
        LOG.critical("file not found: {}".format(e))
    return kp


def get_db_struct(dbname: str, db_list: Dict[str, Database]):
    if dbname in db_list:
        return db_list[dbname]
    else:
        parsed_dbname = dbname.split(":")
        new_db = Database(
            parsed_dbname[0],
            parsed_dbname[0],
            keyfile=parsed_dbname[1] if len(parsed_dbname) > 1 else None,
        )
        return new_db


def list_entities(
    args: argparse.Namespace, db_list: Dict[str, Database], jobs: Dict[str, Job]
):
    def print_dbs(db_list: Dict[str, Database]):
        for dbname, dbvalues in db_list.items():
            if args.verbose:
                print(
                    yaml.as_document(
                        {
                            dbname: {
                                "dbfile": dbvalues.dbfile,
                                "keyfile": dbvalues.keyfile,
                            }
                        }
                    ).as_yaml()
                )
            else:
                print(dbname)

    def print_jobs(jobs: Dict[str, Job]):
        for jobname, jobdata in jobs.items():
            if args.verbose:
                print(
                    yaml.as_document(
                        {
                            jobdata.jobname: {
                                "db": jobdata.db,
                                "entries": jobdata.entries,
                            }
                        }
                    ).as_yaml()
                )
            else:
                print(jobname)

    if args.ENTITY_TYPE == "all":
        print("------ db ------")
        print_dbs(db_list)
        print("----- jobs -----")
        print_jobs(jobs)
    elif args.ENTITY_TYPE == "db":
        print_dbs(db_list)
    elif args.ENTITY_TYPE == "jobs":
        print_jobs(jobs)


def run_job(db_handles: Dict[str, PyKeePassNoCache], job: Job, dry_run: bool):
    dbs_to_save: Set[PyKeePassNoCache] = set()
    sync_handles: List[PyKeePassNoCache] = [db_handles[dbname] for dbname in job.db]
    for entry in job.entries:
        updated_dbs: Set[PyKeePassNoCache] = sync_entry(sync_handles, entry)
        dbs_to_save.update(updated_dbs)

    if not dry_run:
        for db in dbs_to_save:
            LOG.info("saving db {}".format(db.filename))
            db.save()


def main():
    args: argparse.Namespace = parse_args()
    db_list: Dict[str, Database]
    jobs: Dict[str, Job]
    db_list, jobs = parse_config(args.config)

    def get_db_handles(dbs_to_open: Set[str]) -> Dict[str, PyKeePassNoCache]:
        try:
            db_handles: Dict[str, PyKeePassNoCache] = {
                db.dbname: create_db_handle(db.dbfile, db.keyfile) for db in dbs_to_open
            }
        except CredentialsError as e:
            LOG.fatal("bad credentials: {}".format(e))
            exit(1)
        return db_handles

    if args.command == "list":
        list_entities(args, db_list, jobs)
    elif args.command == "run":
        dbs_to_open = set(
            [
                get_db_struct(dbname, db_list)
                for jobname in args.JOB_NAME
                for dbname in jobs[jobname].db
            ]
        )
        db_handles = get_db_handles(dbs_to_open)
        for jobname in args.JOB_NAME:
            run_job(db_handles, jobs[jobname], args.dry_run)
    elif args.command == "sync":
        dbs_to_open = set([get_db_struct(dbname, db_list) for dbname in args.db])
        job = Job("synccmd", [db.dbname for db in dbs_to_open], args.entries)
        db_handles = get_db_handles(dbs_to_open)
        run_job(db_handles, job, args.dry_run)
    else:
        LOG.fatal("missing command")
        exit(1)


if __name__ == "__main__":
    main()
