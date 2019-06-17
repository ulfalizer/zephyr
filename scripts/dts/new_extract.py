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
            if dev.enabled and dev.binding:
                write_regs(dev, out)
                write_aliases(dev, out)

                # Generate defines of the form #define DT_<COMPAT>_<INSTANCE> 1
                for compat in dev.compats:
                    print('#define DT_{}_{}\t1'.format(str2ident(compat),
                        dev.instance_no[compat]), file=out)

        # These are derived from /chosen

        # TODO: Check that regs[0] exists below?

        if edt.sram_dev:
            print("#define DT_SRAM_BASE_ADDRESS\t"
                      + hex(edt.sram_dev.regs[0].addr),
                  file=out)

        if edt.ccm_dev:
            print("#define DT_CCM_BASE_ADDRESS\t"
                      + hex(edt.ccm_dev.regs[0].addr),
                  file=out)


def write_regs(dev, out):
    for reg in dev.regs:
        print("#define {}\t0x{:x}".format(reg_ident(reg), reg.addr), file=out)


def write_aliases(dev, out):
    for reg in dev.regs:
        ident = reg_ident(reg)
        for alias in reg_aliases(reg):
            # Avoid writing aliases that overlap with the base identifier for
            # the register
            if alias != ident:
                print("#define {}\t{}".format(alias, ident), file=out)

def dev_ident(dev):
    # Returns the identifier (e.g., macro name) to be used for property in the
    # output

    # TODO: Handle PWM on STM,
    # TODO: Better document the rules of how we generate things

    ident = "DT"

    # TODO: Factor out helper? Seems to be the same thing being done to the
    # node and the parent. Maybe elsewhere too.

    if dev.bus:
        ident += "_{}_{:X}".format(
            str2ident(dev.parent.matching_compat), dev.parent.unit_addr)

    ident += "_{}".format(str2ident(dev.matching_compat))

    if dev.unit_addr is not None:
        ident += "_{:X}".format(dev.unit_addr)
    else:
        # This is a bit of a hack
        ident += "_{}".format(str2ident(dev.name))

    return ident


def reg_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for 'reg' in the
    # output

    dev = reg.dev

    ident = dev_ident(dev) + "_BASE_ADDRESS"

    # TODO: Could the index always be added later, even if there's
    # just a single register? Might streamline things.
    if len(dev.regs) > 1:
        ident += "_" + str(dev.regs.index(reg))

    return ident


def reg_aliases(reg):
    # Returns a list of aliases (e.g., macro names) to be used for 'reg' in the
    # output. TODO: give example output

    aliases = reg_path_aliases(reg) + reg_instance_aliases(reg)

    if reg.name:
        aliases.append(reg_name_alias(reg))
    return aliases


def reg_path_aliases(reg):
    # reg_aliases() helper. Returns a list of aliases for 'reg' based on the
    # aliases registered for the register's device, in the /aliases node.
    #
    # Generates: DT_<COMPAT>_<ALIAS>_<PROP>

    dev = reg.dev

    aliases = []

    for dev_alias in dev.aliases:
        alias = "DT_{}_{}_BASE_ADDRESS".format(
            str2ident(dev.matching_compat), str2ident(dev_alias))

        if len(dev.regs) > 1:
            alias += "_" + str(dev.regs.index(reg))

        aliases.append(alias)

        if reg.name:
            aliases.append("DT_{}_{}_{}_BASE_ADDRESS".format(
                str2ident(dev.matching_compat), str2ident(dev_alias),
                str2ident(reg.name)))

    return aliases


def reg_instance_aliases(reg):
    # reg_aliases() helper. Returns a list of aliases for 'reg' based on the
    # instance number(s) of the register's device (based on how many instances
    # of that particular device there are).
    #
    # This is a list since a device can have multiple 'compatible' strings,
    # each with their own instance number.
    #
    # Generates: DT_<COMPAT>_<INSTANCE>_<PROP>

    dev = reg.dev

    idents = []

    for compat in dev.compats:
        ident = "DT_{}_{}_BASE_ADDRESS".format(
            str2ident(compat), dev.instance_no[compat])

        if len(dev.regs) > 1:
            ident += "_" + str(dev.regs.index(reg))

        idents.append(ident)

        if reg.name:
            idents.append("DT_{}_{}_{}_BASE_ADDRESS".format(
                str2ident(dev.matching_compat), dev.instance_no[compat],
                str2ident(reg.name)))

    return idents


def reg_name_alias(reg):
    dev = reg.dev
    # reg_aliases() helper. Returns an alias based on 'reg's name.
    return "DT_{}_{:X}_{}_BASE_ADDRESS".format(
        str2ident(dev.matching_compat), dev.regs[0].addr, str2ident(reg.name))


if __name__ == "__main__":
    main()
