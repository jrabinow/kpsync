#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync DB passwords according to syncconfig.yml
"""

import argparse
import getpass
import logging
import os
import stat
from collections import namedtuple
from pathlib import PurePath
from typing import Dict, List, Optional, Set, Tuple, Type, Any

import strictyaml as yaml
from pykeepass import PyKeePass as PyKeePassNoCache
from pykeepass_cache import PyKeePass as PyKeePassCached, cached_databases
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
    parser.add_argument("--config", type=PurePath, help="manually specify config file")
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
        "--timeout",
        type=int,
        const=600,
        nargs="?",
        help="cache database credentials for TIMEOUT seconds (default 600)",
    )
    runparser.add_argument(
        "JOB_NAME", help="specify job name", nargs="*", default=["default"]
    )

    syncparser: argparse.ArgumentParser = subparsers.add_parser(
        "sync",
        help="specify dbs and entries to sync from the command-line",
    )
    syncparser.add_argument(
        "--dry-run",
        action="store_true",
        help="don't save dbs, just print what would be done",
    )
    syncparser.add_argument(
        "--timeout",
        type=int,
        const=600,
        nargs="?",
        help="cache database credentials for TIMEOUT seconds (default 600)",
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


def parse_config(
    configfile: Optional[PurePath] = None,
) -> Tuple[Dict[str, Database], Dict[str, Job]]:

    databases: Dict[str, Database] = {}
    jobs: Dict[str, Job] = {}

    if configfile is None:
        configfile = PurePath("syncconfig.yml")
        if not os.path.isfile(configfile):
            configfile = PurePath(xdg_config_home()) / "kpsync/syncconfig.yml"

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
    existing_entry: Optional[Entry] = db_file.find_entries(path=uptodate_entry.path)
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
    group_list: List[Group] = db.find_groups(name=group_name)
    return group_list[0] if len(group_list) > 0 else None


def ensure_group(
    db: PyKeePassNoCache, group_path: List[str], icon: bytes = None, notes: str = None
) -> Tuple[Group, bool]:

    dirty: bool = False
    group: Group = db.find_groups(path=group_path)

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

    # pull the entry from each db and store it in `entry_dict`
    entry_dict: Dict[PyKeePassNoCache, Optional[Entry]] = {}
    for handle in db_handles:
        matching_entries: List[Entry] = [e for e in handle.find_entries(
            title=entry_title,
            group=group_obj_nothrows_on_missing(handle, group_name),
            regex=True,
            flags="i"
        ) if e.group.name != "Recycle Bin"]
        assert (
            len(matching_entries) <= 1
            ), f"more than 2 entries found for '{entry_title}' in {handle.filename}: {matching_entries}"
        entry_dict[handle] = matching_entries[0] if len(matching_entries) > 0 else None

    # identify db/entry pair which was updated last
    uptodate_db, uptodate_entry = max(
        entry_dict.items(),
        key=lambda e: e[1].mtime.timestamp() if e[1] is not None else -1,
    )

    # make sure we have at least one entry
    if uptodate_entry is None:
        raise KeyError(
            f"failed to find entry '{entry_title}' in both databases. Check the entry title for typos"
        )

    # update all dbs with most uptodate entry
    updated_dbs: Set[PyKeePassNoCache] = set()
    for handle in db_handles:
        if handle != uptodate_db:
            _, dirty = persist_entry(handle, uptodate_entry)
            if dirty:
                updated_dbs.add(handle)

    return updated_dbs


def create_db_handle(
    db: Database,
    socket_path: str = "./pykeepass_socket",
    timeout: int = None,
) -> PyKeePassNoCache:

    password: str
    kp: Type[Any]

    if timeout is not None and is_dir_world_readable():
        LOG.warning(
            "dir is world-readable, disabling caching to prevent security issue"
        )
        timeout = None

    if timeout is not None:
        if db.dbfile in cached_databases(socket_path=socket_path):
            cached_db = cached_databases(socket_path=socket_path)[db.dbfile]
            # IMPORTANT: reload cached database. If you don't do this, kpsync won't take into
            # account any changes since it first opened and read the the database. This will
            # lead to much head scratching and time wasted on debugging stupid shit
            cached_db.reload()
            return cached_db
        PyKeePass = PyKeePassCached
        password = getpass.getpass(prompt=f"Password for {db.dbfile}: ")
        try:
            kp = PyKeePass(
                db.dbfile,
                password=password,
                keyfile=db.keyfile,
                timeout=600,
                socket_path=socket_path,
            )
        except FileNotFoundError as e:
            LOG.critical("file not found: {}".format(e))
    else:
        PyKeePass = PyKeePassNoCache
        password = getpass.getpass(prompt="Password for {}: ".format(db.dbfile))
        try:
            kp = PyKeePass(
                db.dbfile,
                password=password,
                keyfile=db.keyfile,
            )
        except FileNotFoundError as e:
            LOG.critical("file not found: {}".format(e))
    return kp


def get_db_struct(dbname: str, db_list: Dict[str, Database]):
    if dbname in db_list:
        return db_list[dbname]
    parsed_dbname = dbname.split(":")
    new_db = Database(
        parsed_dbname[0],
        parsed_dbname[0],
        keyfile=parsed_dbname[1] if len(parsed_dbname) > 1 else None,
    )
    return new_db


def get_db_handles(
    dbs_to_open: List[Database], timeout=None
) -> Dict[str, PyKeePassNoCache]:
    try:
        db_handles: Dict[str, PyKeePassNoCache] = {
            db.dbname: create_db_handle(db, timeout=timeout) for db in dbs_to_open
        }
    except CredentialsError as e:
        LOG.fatal("bad credentials: {}" % e)
        exit(1)
    return db_handles


def run_job(db_handles: Dict[str, PyKeePassNoCache], job: Job, dry_run: bool):
    dbs_to_save: Set[PyKeePassNoCache] = set()
    sync_handles: List[PyKeePassNoCache] = [db_handles[dbname] for dbname in job.db]
    for entry in job.entries:
        updated_dbs: Set[PyKeePassNoCache] = sync_entry(sync_handles, entry)
        dbs_to_save.update(updated_dbs)

    if not dry_run:
        for db in dbs_to_save:
            LOG.info(f"saving db {db.filename}")
            db.save()


def list_entities(
    entity_type: str,
    db_list: Dict[str, Database],
    jobs: Dict[str, Job],
    verbose: bool = False,
):
    def print_dbs(db_list: Dict[str, Database]):
        for dbname, dbvalues in db_list.items():
            if verbose:
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
            if verbose:
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

    if entity_type == "all":
        print("------ db ------")
        print_dbs(db_list)
        print("----- jobs -----")
        print_jobs(jobs)
    elif entity_type == "db":
        print_dbs(db_list)
    elif entity_type == "jobs":
        print_jobs(jobs)


def run(
    job_names: List[str],
    db_list: Dict[str, Database],
    jobs: Dict[str, Job],
    dry_run: bool = False,
    timeout: int = None,
):
    dbs_to_open = sorted(
        set(
            get_db_struct(dbname, db_list)
            for jobname in job_names
            for dbname in jobs[jobname].db
        ),
        key=lambda db: db.dbname,
    )
    db_handles = get_db_handles(dbs_to_open, timeout=timeout)
    for job in job_names:
        run_job(db_handles, jobs[job], dry_run)


def sync(
    db_names: List[str],
    entries: List[str],
    db_list: Dict[str, Database],
    dry_run: bool = False,
    timeout: int = None,
):
    dbs_to_open = sorted(
        set(get_db_struct(dbname, db_list) for dbname in db_names),
        key=lambda db: db.dbname,
    )
    db_handles = get_db_handles(dbs_to_open, timeout=timeout)
    job = Job("synccmd", [db.dbname for db in dbs_to_open], entries)
    run_job(db_handles, job, dry_run)


def main():
    args: argparse.Namespace = parse_args()
    db_list: Dict[str, Database]
    jobs: Dict[str, Job]
    db_list, jobs = parse_config(args.config)

    if args.command == "list":
        list_entities(args.ENTITY_TYPE, db_list, jobs, args.verbose)
    elif args.command == "run":
        run(args.JOB_NAME, db_list, jobs, args.dry_run, args.timeout)
    elif args.command == "sync":
        sync(args.db, args.entries, db_list, args.dry_run, args.timeout)
    else:
        LOG.fatal("missing command")
        exit(1)


if __name__ == "__main__":
    main()
