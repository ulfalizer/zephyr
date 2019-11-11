#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
Lists maintainers for files or commits. Similar in function to
scripts/get_maintainer.pl from Linux, but geared towards GitHub. The mapping is
in MAINTAINERS.yml.

The comment at the top of MAINTAINERS.yml in Zephyr documents the file format.

See the help texts for the various subcommands for more information. They can
be viewed with e.g.

    ./get_maintainer.py path --help

This executable doubles as a Python library. Identifiers not prefixed with '_'
are part of the library API. The library documentation can be viewed with this
command:

    $ pydoc get_maintainer
"""

import argparse
import glob
import operator
import os
import re
import shlex
import subprocess
import sys

from yaml import load, YAMLError
try:
    # Use the speedier C LibYAML parser if available
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader


def _main():
    # Entry point when run as an executable

    args = _parse_args()

    try:
        args.cmd_fn(Maintainers(args.maintainers), args)
    except (MaintainersError, GitError) as e:
        _serr(e)


def _parse_args():
    # Parses arguments when run as an executable

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)

    parser.add_argument(
        "-m", "--maintainers",
        metavar="MAINTAINERS_FILE",
        default="MAINTAINERS.yml",
        help="Maintainers file to load (default: MAINTAINERS.yml)")

    subparsers = parser.add_subparsers(
        help="Available commands (each has a separate --help text)")

    id_parser = subparsers.add_parser(
        "path",
        help="List subsystem(s) for paths")
    id_parser.add_argument(
        "paths",
        metavar="PATH",
        nargs="*",
        help="Paths to list subsystems for")
    id_parser.set_defaults(cmd_fn=Maintainers._path_cmd)

    commits_parser = subparsers.add_parser(
        "commits",
        help="List subsystem(s) for commit range")
    commits_parser.add_argument(
        "commits",
        metavar="COMMIT_RANGE",
        nargs="*",
        help="Commit range(s) to list subsystems for (default: HEAD~..)")
    commits_parser.set_defaults(cmd_fn=Maintainers._commits_cmd)

    list_parser = subparsers.add_parser(
        "list",
        help="List files in subsystem")
    list_parser.add_argument(
        "subsystem",
        metavar="SUBSYSTEM",
        nargs="?",
        help="Name of subsystem to list files in. If not specified, all "
             "non-orphaned files are listed (all files that do not appear in "
             "any subsystems).")
    list_parser.set_defaults(cmd_fn=Maintainers._list_cmd)

    orphaned_parser = subparsers.add_parser(
        "orphaned",
        help="List orphaned files (files that do not appear in any subsystem)")
    orphaned_parser.add_argument(
        "path",
        metavar="PATH",
        nargs="?",
        help="Limit to files under PATH")
    orphaned_parser.set_defaults(cmd_fn=Maintainers._orphaned_cmd)

    args = parser.parse_args()
    if not hasattr(args, "cmd_fn"):
        # Called without a subcommand
        sys.exit(parser.format_usage().rstrip())

    return args


def _print_subsystems(subsystems):
    first = True
    for subsys in sorted(subsystems, key=operator.attrgetter("name")):
        if not first:
            print()
        first = False

        print("""\
{}
\tmaintainers: {}
\tcollaborators: {}
\tinform: {}
\tlabels: {}
\tdescription: {}""".format(subsys.name,
                            ", ".join(subsys.maintainers),
                            ", ".join(subsys.collaborators),
                            ", ".join(subsys.inform),
                            ", ".join(subsys.labels),
                            subsys.description or ""))


class Maintainers:
    """
    Represents the contents of a MAINTAINERS.yml file.

    These attributes are available:

    subsystems:
        A dictionary that maps subsystem names to Subsystem instances,
        for all subsystems defined in MAINTAINERS.yml

    filename:
        The maintainers filename passed to the constructor
    """
    def __init__(self, filename="MAINTAINERS.yml"):
        """
        Creates a Maintainers instance.

        filename (default: "MAINTAINERS.yml"):
            Path to the maintainers file to parse.
        """
        self.filename = filename

        self.subsystems = {}
        for subsys_name, subsys_dict in _load_maintainers(filename).items():
            subsys = Subsystem()
            subsys.name = subsys_name
            subsys.maintainers = subsys_dict.get("maintainers", [])
            subsys.collaborators = subsys_dict.get("collaborators", [])
            subsys.inform = subsys_dict.get("inform", [])
            subsys.labels = subsys_dict.get("labels", [])
            subsys.description = subsys_dict.get("description")
            subsys._files = subsys_dict.get("files")
            subsys._files_exclude = subsys_dict.get("files-exclude")
            subsys._files_regex = subsys_dict.get("files-regex")
            subsys._files_regex_exclude = subsys_dict.get("files-regex-exclude")
            self.subsystems[subsys_name] = subsys

    def path2subsystems(self, path):
        """
        Returns a list of Subsystem instances for the subsystems that contain
        'path'
        """
        if os.path.isdir(path):
            # Make directory paths end in '/' so that '-f foo/bar' matches
            # foo/bar/, which is handy for command-line use. Skip this check in
            # _contains(), because the isdir() makes it twice as slow in cases
            # where it's not needed.
            path = path.rstrip("/") + "/"

        return [subsys for subsys in self.subsystems.values()
                if subsys._contains(path)]

    def commits2subsystems(self, commits):
        """
        Returns a set() of Subsystem instances for the subsystems that contain
        files that are modified by the commit range in 'commits'. 'commits'
        could be e.g. "HEAD~..", to inspect the tip commit
        """
        res = set()
        # Final '--' is to disallow a path for 'commits', so that
        # './get_maintainers.py some/file' errors out instead of doing nothing.
        # That makes forgetting -f easier to notice.
        for path in _git("diff", "--name-only", commits, "--").splitlines():
            res.update(self.path2subsystems(path))
        return res

    def __repr__(self):
        return "<Maintainers for '{}'>".format(self.filename)

    #
    # Command-line subcommands
    #

    def _path_cmd(self, args):
        # 'path' subcommand implementation

        for path in args.paths:
            if not os.path.exists(path):
                _serr("'{}': no such file or directory".format(path))

        _print_subsystems({
            subsys for path in args.paths
                   for subsys in self.path2subsystems(path)
        })

    def _commits_cmd(self, args):
        # 'commits' subcommand implementation

        commits = args.commits or ("HEAD~..",)
        _print_subsystems({
            subsys for commits in args.commits
                   for subsys in self.commits2subsystems(commits)
        })

    def _list_cmd(self, args):
        # 'list' subcommand implementation

        if args.subsystem is None:
            # List all files that appear in some subsystem
            for path in _all_files():
                for subsys in self.subsystems.values():
                    if subsys._contains(path):
                        print(path)
                        break
        else:
            # List all files that appear in the given subsystem
            subsys = self.subsystems.get(args.subsystem)
            if subsys is None:
                _serr("'{}': no such subsystem defined in '{}'"
                      .format(args.subsystem, self.filename))

            for path in _all_files():
                if subsys._contains(path):
                    print(path)

    def _orphaned_cmd(self, args):
        # 'orphaned' subcommand implementation

        if args.path is not None and not os.path.exists(args.path):
            _serr("'{}': no such file or directory".format(args.path))

        for path in _all_files(args.path):
            for subsys in self.subsystems.values():
                if subsys._contains(path):
                    break
            else:
                print(path)  # We get here if we never hit the 'break'


class Subsystem:
    """
    Represents an entry for a subsystem in MAINTAINERS.yml.

    These attributes are available:

    maintainers:
        List of maintainers. Empty if the subsystem has no 'maintainers' key.

    collaborators:
        List of collaborators. Empty if the subsystem has no 'collaborators'
        key.

    inform:
        List of people to inform on pull requests. Empty if the subsystem has
        no 'inform' key.

    labels:
        List of GitHub labels for the subsystem. Empty if the subsystem has no
        'labels' key.

    description:
        Text from 'description' key, or None if the subsystem has no
        'description' key
    """
    def _contains(self, path):
        # Returns True if the subsystem contains 'path', and False otherwise

        # Test exclusions first

        if self._files_exclude is not None:
            for glob in self._files_exclude:
                if _glob_match(glob, path):
                    return False

        if self._files_regex_exclude is not None:
            for regex in self._files_regex_exclude:
                if re.search(regex, path):
                    return False

        if self._files is not None:
            for glob in self._files:
                if _glob_match(glob, path):
                    return True

        if self._files_regex is not None:
            for regex in self._files_regex:
                if re.search(regex, path):
                    return True

        return False

    def __repr__(self):
        return "<Subsystem {}>".format(self.name)


def _glob_match(glob, path):
    # Returns True if 'path' matches the pattern 'glob' from a
    # 'files(-exclude)' entry in MAINTAINERS.yml

    match_fn = re.match if glob.endswith("/") else re.fullmatch
    regex = glob.replace(".", "\\.").replace("*", "[^/]*").replace("?", "[^/]")
    return match_fn(regex, path)


def _load_maintainers(filename):
    # Returns the parsed contents of the maintainers file 'filename', also
    # running checks on the contents. The returned format is plain Python
    # dicts/lists/etc., mirroring the structure of the file.

    with open(filename, encoding="utf-8") as f:
        try:
            yaml = load(f, Loader=Loader)
        except YAMLError as e:
            raise MaintainersError("{}: YAML error: {}".format(filename, e))

        _check_maintainers(filename, yaml)
        return yaml


def _check_maintainers(filename, yaml):
    # Checks the maintainers data in 'yaml' (from 'filename')

    def ferr(msg):
        _err("{}: {}".format(filename, msg))  # Prepend the filename

    if not isinstance(yaml, dict):
        ferr("empty or malformed YAML (not a dict)")

    ok_keys = {"status", "maintainers", "collaborators", "inform", "files",
               "files-exclude", "files-regex", "files-regex-exclude",
               "labels", "description"}

    ok_status = {"maintained", "odd fixes", "orphaned", "obsolete"}
    ok_status_s = ", ".join('"' + s + '"' for s in ok_status)  # For messages

    for subsys_name, subsys_dict in yaml.items():
        if not isinstance(subsys_dict, dict):
            ferr("malformed entry for subsystem '{}' (not a dict)"
                 .format(subsys_name))

        for key in subsys_dict:
            if key not in ok_keys:
                ferr("unknown key '{}' in subsystem '{}'"
                     .format(key, subsys_name))

        if "status" not in subsys_dict:
            ferr("missing 'status' key on subsystem '{}', should be one of {}"
                 .format(subsys_name, ok_status_s))

        if subsys_dict["status"] not in ok_status:
            ferr("bad 'status' key on subsystem '{}', should be one of {}"
                 .format(subsys_name, ok_status_s))

        if not subsys_dict.keys() & {"files", "files-regex"}:
            ferr("either 'files' or 'files-regex' (or both) must be specified "
                 "for subsystem '{}'".format(subsys_name))

        for list_name in "maintainers", "collaborators", "inform", "files", \
                         "files-regex", "labels":
            if list_name in subsys_dict:
                lst = subsys_dict[list_name]
                if not (isinstance(lst, list) and
                        all(isinstance(elm, str) for elm in lst)):
                    ferr("malformed '{}' value for subsystem '{}' -- should "
                         "be a list of strings".format(list_name, subsys_name))

        for files_key in "files", "files-exclude":
            if files_key in subsys_dict:
                for glob_pattern in subsys_dict[files_key]:
                    # This could be changed if it turns out to be too slow,
                    # e.g. to only check non-globbing filenames
                    paths = glob.glob(glob_pattern)
                    if not paths:
                        ferr("glob pattern '{}' in '{}' in subsystem '{}' "
                             "does not match any files"
                             .format(glob_pattern, files_key,
                                     subsys_name))
                    if not glob_pattern.endswith("/"):
                        for path in paths:
                            if os.path.isdir(path):
                                ferr("glob pattern '{}' in '{}' in subsystem "
                                     "'{}' matches a directory, but has no "
                                     "trailing '/'"
                                     .format(glob_pattern, files_key,
                                             subsys_name))

        for files_regex_key in "files-regex", "files-regex-exclude":
            if files_regex_key in subsys_dict:
                for regex in subsys_dict[files_regex_key]:
                    try:
                        # This also caches the regex in the 're' module, so we
                        # don't need to worry
                        re.compile(regex)
                    except re.error as e:
                        ferr("bad regular expression '{}' in '{}' in "
                             "'{}': {}".format(regex, files_regex_key,
                                               subsys_name, e.msg))

        if "description" in subsys_dict and \
           not isinstance(subsys_dict["description"], str):
            ferr("malformed 'description' value for subsystem '{}' -- should "
                 "be a string".format(subsys_name))


def _git(*args):
    # Helper for running a Git command. Returns the rstrip()ed stdout output.
    # Called like git("diff"). Exits with SystemError (raised by sys.exit()) on
    # errors.

    git_cmd = ("git",) + args
    git_cmd_s = " ".join(shlex.quote(word) for word in git_cmd)  # For errors

    try:
        git_process = subprocess.Popen(
            git_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        _giterr("git executable not found (when running '{}'). Check that "
                "it's in listed in the PATH environment variable"
                .format(git_cmd_s))
    except OSError as e:
        _giterr("error running '{}': {}".format(git_cmd_s, e))

    stdout, stderr = git_process.communicate()
    if git_process.returncode:
        _giterr("error running '{}'\n\nstdout:\n{}\nstderr:\n{}".format(
            git_cmd_s, stdout.decode("utf-8"), stderr.decode("utf-8")))

    return stdout.decode("utf-8").rstrip()


def _all_files(path=None):
    cmd = ["ls-files"]
    if path is not None:
        cmd.append(path)
    return _git(*cmd).splitlines()


def _err(msg):
    raise MaintainersError(msg)


def _giterr(msg):
    raise GitError(msg)


def _serr(msg):
    # For reporting errors when get_maintainer.py is run as a script.
    # sys.exit() shouldn't be used otherwise.
    sys.exit("{}: error: {}".format(sys.argv[0], msg))


class MaintainersError(Exception):
    "Exception raised for MAINTAINERS.yml-related errors"


class GitError(Exception):
    "Exception raised for Git-related errors"


if __name__ == "__main__":
    _main()
