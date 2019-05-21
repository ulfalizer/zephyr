#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import argparse
import os
import sys

import edtlib


def str2ident(s):
    # Change ,-@/ to _ and uppercase
    return s.replace('-', '_') \
            .replace(',', '_') \
            .replace('@', '_') \
            .replace('/', '_') \
            .upper()


regs_chosen = {
    'zephyr,sram'  : 'DT_SRAM_BASE_ADDRESS',
    'zephyr,ccm'   : 'DT_CCM_BASE_ADDRESS'
}

def main():
    # Copied from extract_dts_includes.py
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dts", required=True, help="DTS file")
    parser.add_argument("-y", "--yaml", nargs='+', required=True,
                        help="YAML file directories, we allow multiple")
    parser.add_argument("-i", "--include",
                        help="path to write header to")
    parser.add_argument("-k", "--keyvalue",
                        help="path to write configuration file to")
    parser.add_argument("--old-alias-names", action='store_true',
                        help="Generate aliases also in the old way, without "
                             "compatibility information in their labels")
    args = parser.parse_args()

    edt = edtlib.EDT(args.dts, args.yaml[0])

    with open(args.keyvalue + "-new", "w") as out:
        for dev in edt.devices:
            if dev.enabled:
                write_regs(dev, out)
                write_aliases(dev, out)
        for prop in edt.dt.get_node("/chosen").props.values():
            if prop.name in regs_chosen:
                d = edt._node2dev[edt.dt.get_node(prop.to_string())]
                print("#define {}\t0x{:x}".format(regs_chosen[prop.name],
                      d.regs[0].addr), file=out)


def write_regs(dev, out):
    for reg in dev.regs:
        print("#define {}\t0x{:x}".format(reg_ident(reg), reg.addr), file=out)


def write_aliases(dev, out):
    for reg in dev.regs:
        for alias in reg_aliases(reg):
            # Avoid writing aliases that overlap with the base identifier for
            # the register
            if alias != reg_ident(reg):
                print("#define {}\t{}".format(alias, reg_ident(reg)), file=out)


def reg_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for 'reg' in the
    # output

    dev = reg.dev

    ident = "DT"

    if dev.bus:
        ident += "_{}_{:X}".format(str2ident(dev.parent.matching_compat),
                                   dev.parent.regs[0].addr)

    ident += "_{}_{:X}_BASE_ADDRESS".format(
        str2ident(dev.matching_compat), dev.regs[0].addr)

    # TODO: Could the index always be added later, even if there's
    # just a single register? Might streamline things.
    if len(dev.regs) > 1:
        ident += "_" + str(dev.regs.index(reg))

    return ident

def reg_name_aliases(reg):
    idents = []
    dev = reg.dev
    if reg.name:
        ident = "DT_{}_{:X}_{}_BASE_ADDRESS".format(
                str2ident(dev.matching_compat), dev.regs[0].addr, str2ident(reg.name))
        idents.append(ident)

    return idents


def reg_aliases(reg):
    # Returns a list of aliases (e.g., macro names) to be used for 'reg' in the
    # output. TODO: give example output

    return reg_path_aliases(reg) + reg_instance_alias(reg) + reg_path_aliases(reg)


def reg_path_aliases(reg):
    # reg_aliases() helper. Returns a list of aliases for 'reg' based on the
    # aliases registered for the register's device, in the /aliases node.

    dev = reg.dev

    aliases = []

    for dev_alias in dev.aliases:
        alias = "DT_{}_{}_BASE_ADDRESS".format(
            str2ident(dev.matching_compat), str2ident(dev_alias))

        if len(dev.regs) > 1:
            alias += "_" + str(dev.regs.index(reg))

        aliases.append(alias)

        if reg.name:
            ident = "DT_{}_{:X}_{}_BASE_ADDRESS".format(
                    str2ident(dev.matching_compat), reg.addr, str2ident(reg.name))
            aliases.append(ident)

    return aliases


def reg_instance_alias(reg):
    # reg_aliases() helper. Returns an alias for 'reg' based on the instance
    # number of the register's device (based on how many instances of that
    # particular device there are).

    dev = reg.dev

    idents = []

    for compat in dev.compats:

        ident = "DT_{}_{}_BASE_ADDRESS".format(
            str2ident(compat), dev.instance_no[compat])

        if len(dev.regs) > 1:
            ident += "_" + str(dev.regs.index(reg))

        idents.append(ident)

        if reg.name:
            ident = "DT_{}_{}_{}_BASE_ADDRESS".format(
                    str2ident(dev.matching_compat), dev.instance_no[compat],
                    str2ident(reg.name))
            idents.append(ident)

    return idents


if __name__ == "__main__":
    main()
