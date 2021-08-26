# kpsync

## Partial sync of KeePassX databases through the command-line

Limitations:
- Yubikeys, OTP and other methods of unlocking are not supported (contributions welcome!)
- the sync algorithm is very dumb and does not handle conflicts: if you update the same entry in 2 different DBs, the most recent update will overwrite any changes made to the entry in the other DB

Install:
```
$ python setup.py install
```

Create a syncconfig.yml file (sample follows), either in the current directory, or in ~/.config/kpsync/:

```yaml
db:
  # You can add as many KeePassX DB files as you'd like to this section,
  # DB names are fooDB and barDB here, but you can name them anything they want
  # '~' and shell variables expansion in filepaths are supported.
  fooDB:
    dbfile: $creds/myfoodb.kdbx
    keyfile: /mnt/removable_usb/supersecret.pem
  barDB:
    dbfile: ~/alternate_db/shared_pw.kdbx

job:
  default:
    # This is the job named default
    db:
      - fooDB
      - barDB
    entries:
      # Each line is the title of one of the entries in your KeepassX DB. In case
      # you have several different accounts with the same title, you can
      # differentiate between those you want to sync by giving the group path
      # (checkout the 'google' example below).
      # Failure to specify which account to sync will make kpsync abort all
      # operations in case of ambiguity: we do not want to sync the wrong entry
      # by error.
      - Facebook
      - Github
      - Personal/Google
      - Pro Account/Google
      - Pr0nhub

  foojob:
    # this is a job named foojob
    db:
      - fooDB
      - barDB
    entries:
      - random account
```

Then, launch kpsync:
```
$ ./kpsync list jobs
default
foojob
$ ./kpsync run foojob           # run by specifying a job name
$ ./kpsync run                  # if no job is specified, `default` job is run
$ # or you can get fine-grained control by specifying everything from the command-line
$ ./kpsync sync --db fooDB path/to/otherdb/not/in/syncconfig.yml:path/to/keyfile --entries microsoft discord linkedin
```
KPSync will use the `syncconfig.yml` file found in the current directory by default - if that's not found, it will check `$XDG_CONFIG_HOME/kpsync/syncconfig.yml`. You can also specify the path to the config file by using the `--config` option

```
usage: kpsync.py [-h] [-d] [--config CONFIG] {list,run,sync} ...

positional arguments:
  {list,run,sync}
    list           list entities in the config file
    run            run a job
    sync           specify dbs and entries to sync from the command-line

optional arguments:
  -h, --help       show this help message and exit
  -d, --debug      enable debug logging information
  --config CONFIG  manually specify config file

#############################################
usage: kpsync.py list [-h] [-v] {all,db,jobs}

positional arguments:
  {all,db,jobs}

optional arguments:
  -v, --verbose  verbose mode

####################################################
usage: kpsync.py run [-h] [--dry-run] [JOB_NAME ...]

positional arguments:
  JOB_NAME    specify job name

optional arguments:
  --dry-run   don't save dbs, just print what would be done
#########################################################################
usage: kpsync.py sync [-h] [--dry-run] --db DB [DB ...] --entries ENTRIES
                      [ENTRIES ...]

optional arguments:
  --dry-run             don't save dbs, just print what would be done
  --db DB [DB ...]      db name, either registered in syncconfig.yml or in
                        DBFILEPATH[:KEYFILEPATH] format
  --entries ENTRIES [ENTRIES ...]
                        list of entries
```
