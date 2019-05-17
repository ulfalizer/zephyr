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
        for dev in edt.devices.values():
            if dev.enabled:
                write_regs(dev, out)
                write_aliases(dev, out)


def write_regs(dev, out):
    for reg in dev.regs:
        print("#define {}\t0x{:x}".format(reg_ident(reg), reg.addr), file=out)


def write_aliases(dev, out):
    for reg in dev.regs:
        print("#define {}\t{}".format(alias_ident(reg), reg_ident(reg)),
              file=out)


def reg_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for 'reg' in the
    # output

    dev = reg.dev

    ident = "DT"

    if dev.bus in {"i2c", "spi"}:
        ident += "_{}_{:X}".format(str2ident(dev.parent.matching_compat),
                                   dev.parent.regs[0].addr)

    ident += "_{}_{:X}_BASE_ADDRESS".format(
        str2ident(dev.matching_compat), reg.addr)

    # TODO: Could the index always be added later, even if there's
    # just a single register? Might streamline things.
    if len(dev.regs) > 1:
        ident += "_" + str(dev.regs.index(reg))

    return ident


def alias_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the alias of
    # 'reg' in the output

    dev = reg.dev

    ident = "DT_{}_{}_BASE_ADDRESS".format(
        str2ident(dev.matching_compat), dev.instance_no)

    if len(dev.regs) > 1:
        ident += "_" + str(dev.regs.index(reg))

    return ident


if __name__ == "__main__":
    main()
