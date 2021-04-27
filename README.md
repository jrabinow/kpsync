# kpsync

## Partial sync of KeePassX databases through the command-line

Shortcomings:
- Yubikeys, OTP and other methods of unlocking are not supported
- the sync algorithm is very dumb and does not handle conflicts: if you update the same entry in 2 different DBs, the most recent update will overwrite any changes made to the entry in the other DB

Create a syncconfig.ini file (sample follows).

```
[db]
    # You can add as many KeePassX DB files as you'd like to this section,
    # kpsync will sync them all amongst each other by using the most recently
    # updated entry.
    # DB names are fooDB and barDB here, but their naming can be anything you
    # want as long as it's under the [db] section.
    # KeePassX DBs are made up of a path to a keepassx db and an optional path
    # to a key file on a separate line.
    # '~' and shell variables expansion in filepaths are supported.

    fooDB =
        $creds/myfoodb.kdbx
        /mnt/removable_usb/supersecret.pem
    barDB =
        ~/alternate_db/shared_pw.kdbx

[entries]
    # Each line is the title of one of the entries in your KeepassX DB. In case
    # you have several different accounts with the same title, you can
    # differentiate between those you want to sync by giving the group path
    # (checkout the 'google' example below). Failure to specify which account to
    # sync will make kpsync abort all operations in case of ambiguity: we do not
    # want to sync the wrong entry by error.

    entries =
        Facebook
        Github
        Personal/Google
        Pro Account/Google
        Pr0nhub
```

Then, launch kpsync:
```
$ ./kpsync
```
KPSync will use the `syncconfig.ini` file found in the current directory. You can also specify the path to the config file by using the `--config` option

```
usage: kpsync.py [-h] [-d] [--db [database [keyfile ...]]] [--config CONFIG]

optional arguments:
  -h, --help            show this help message and exit
  -d, --debug           enable debug logging information
  --db database [keyfile ...]
                        replace db entries in syncconfig.ini with path/to/database path/to/keyfile.
                        Use this option once per database:
                        >>> kpsync.py --db db1 keyfile_for_db1 --db db2 --db db3 keyfile_for_db3

  --config CONFIG       manually specify config file
```
