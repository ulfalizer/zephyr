#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
Lists maintainers for commits or files. Similar in function to
scripts/get_maintainer.pl from Linux, but geared towards GitHub. The mapping is
in MAINTAINERS.yml.

The comment at the top of MAINTAINERS.yml in Zephyr documents the file format.

Unless -f is passed, one or more commit ranges is expected. If run with no
arguments, HEAD~.. is used (just the tip commit). Commit ranges are passed to
'git diff --name-only' to get a list of changed files.

This executable doubles as a Python library. Identifiers not prefixed with '_'
are part of the library API. The library documentation can be viewed with this
command:

    $ pydoc get_maintainer
"""

import argparse
import glob
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
        maint = Maintainers(args.maintainers)
        if args.args_are_paths:
            for path in args.commit_ranges_or_paths:
                if not os.path.exists(path):
                    _serr("'{}': no such file or directory".format(path))

            subsystems = {
                subsys for path in args.commit_ranges_or_paths
                       for subsys in maint.path2subsystems(path)
            }
        else:
            commit_ranges = args.commit_ranges_or_paths or ("HEAD~..",)
            subsystems = {
                subsys for commit_range in commit_ranges
                       for subsys in maint.commits2subsystems(commit_range)
            }
    except (MaintainersError, GitError) as e:
        _serr(e)

    for subsys in subsystems:
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


def _parse_args():
    # Parses arguments when run as an executable

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-m", "--maintainers",
                        metavar="MAINTAINERS_FILE",
                        default="MAINTAINERS.yml",
                        help="Maintainers file to load (default: MAINTAINERS.yml)")

    parser.add_argument("-f", "--files",
                        dest="args_are_paths",
                        action="store_true",
                        help="Interpret arguments as paths instead of commit ranges")

    parser.add_argument("commit_ranges_or_paths",
                        nargs="*",
                        help="Commit ranges or (with -f) paths (default: HEAD~..)")

    return parser.parse_args()


class Maintainers:
    """
    Represents the contents of a MAINTAINERS.yml file. The filename passed to
    the constructor is available in the 'filename' attribute.
    """
    def __init__(self, filename="MAINTAINERS.yml"):
        """
        Creates a Maintainers instance.

        filename (default: "MAINTAINERS.yml"):
            Path to the maintainers file to parse.
        """
        self.filename = filename

        self.subsystems = []
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
            self.subsystems.append(subsys)

    def path2subsystems(self, path):
        """
        Returns a list of Subsystem instances for the subsystems that contain
        'path'
        """
        return [subsys for subsys in self.subsystems if subsys._contains(path)]

    def commits2subsystems(self, commits):
        """
        Returns a list of Subsystem instances for the subsystems that contain
        files that are modified by the commit range in 'commits'. 'commits'
        could be e.g. "HEAD~..", to inspect the tip commit
        """
        subsystems = set()
        # Final '--' is to disallow a path for 'commits', so that
        # './get_maintainers.py some/file' errors out instead of doing nothing.
        # That makes forgetting -f easier to notice.
        for path in _git("diff", "--name-only", commits, "--").splitlines():
            subsystems.update(self.path2subsystems(path))
        return subsystems

    def __repr__(self):
        return "<Maintainers for '{}'>".format(self.filename)


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
        if os.path.isdir(path):
            # Make directory paths end in '/' so that '-f foo/bar' matches
            # foo/bar/. Handy for command-line use.
            path = path.rstrip("/") + "/"

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
