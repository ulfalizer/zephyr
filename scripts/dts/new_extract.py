#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import argparse
import os
import sys

import edtlib


def str2ident(s):
    # Change ,-@/ to _ and uppercase
    return s.replace("-", "_") \
            .replace(",", "_") \
            .replace("@", "_") \
            .replace("/", "_") \
            .upper()


def write_dev_aliases(dev, ident, prop_name):
    # Generate alias defines for path aliases and instance aliases
    # for a given device's property name

    aliases = dev_path_aliases(dev, prop_name) + dev_instance_aliases(dev, prop_name)
    for alias in aliases:
        # Avoid writing aliases that overlap with the base identifier for
        # the register
        if alias != ident:
            out("#define {}\t{}".format(alias, ident))


def _reg_name_ident(dev, reg):
    # Returns the identifier (e.g., macro name) to be used for reg name aliases

    return "DT_{}_{:X}_{}".format(
        str2ident(dev.matching_compat), dev.regs[0].addr, str2ident(reg.name))


def _process_reg_common(dev, reg, prop_name, prop_val, base_fmt):
    # Comman handler for register define generation

    post = prop_name
    if len(dev.regs) > 1:
        post = "{}_{}".format(post, str(dev.regs.index(reg)))
    ident = "{}_{}".format(dev_ident(dev), post)

    out('#define {}\t{:#{base}}'.format(ident, prop_val, base=base_fmt))
    write_dev_aliases(dev, ident, post)

    if reg.name:
        post = "{}_{}".format(str2ident(reg.name), prop_name)
        write_dev_aliases(dev, ident, post)

        alias = "{}_{}".format(_reg_name_ident(dev, reg), prop_name)
        if alias != ident:
            # Avoid writing aliases that overlap with the base identifier for
            # the register
            out("#define {}\t{}".format(alias, ident))


def process_reg(dev, reg):
    # Generate define for register address
    #
    #   #define DT_<DEV_IDENT>_BASE_ADDRESS[_<N>] <ADDR>
    #
    #   if reg-name:
    #      #define DT_<DEV_IDENT>_<REG_NAME>_BASE_ADDRESS DT_<DEV_IDENT>_BASE_ADDRESS[_<N>]

    _process_reg_common(dev, reg, "BASE_ADDRESS", reg.addr, "x")


def main():
    global _out

    # Copied from extract_dts_includes.py
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dts", required=True, help="DTS file")
    parser.add_argument("-y", "--yaml", nargs="+", required=True,
                        help="YAML file directories, we allow multiple")
    parser.add_argument("-i", "--include",
                        help="path to write header to")
    parser.add_argument("-k", "--keyvalue",
                        help="path to write configuration file to")
    parser.add_argument("--old-alias-names", action="store_true",
                        help="Generate aliases also in the old way, without "
                             "compatibility information in their labels")
    args = parser.parse_args()

    edt = edtlib.EDT(args.dts, args.yaml[0])

    _out = open(args.keyvalue + "-new", "w")

    for dev in edt.devices:
        if dev.enabled and dev.binding:
            for reg in dev.regs:
                process_reg(dev, reg)

            # Generate defines of the form
            #
            #   #define DT_<COMPAT>_<INSTANCE> 1
            #
            # These are flags for which devices exist.
            for compat in dev.compats:
                out("#define DT_{}_{}\t1"
                    .format(str2ident(compat), dev.instance_no[compat]))

    # These are derived from /chosen

    # TODO: Check that regs[0] exists below?

    if edt.sram_dev:
        out("#define DT_SRAM_BASE_ADDRESS\t" + hex(edt.sram_dev.regs[0].addr))

    if edt.ccm_dev:
        out("#define DT_CCM_BASE_ADDRESS\t" + hex(edt.ccm_dev.regs[0].addr))

    if edt.flash_dev:
        write_flash(edt.flash_dev)

    for dev in edt.devices:
        # TODO: Feels a bit yanky to handle this separately from
        # zephyr,flash-dev
        if dev.name.startswith("partition@"):
            write_flash_partition(dev)


def dev_ident(dev):
    # Returns the identifier (e.g., macro name) to be used for property in the
    # output

    # TODO: Handle PWM on STM
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
    elif dev.parent.unit_addr is not None:
        ident += "_{:X}_{}".format(dev.parent.unit_addr, str2ident(dev.name))
    else:
        # This is a bit of a hack
        ident += "_{}".format(str2ident(dev.name))

    return ident


def dev_path_aliases(dev, prop_name):
    # Returns a list of aliases (e.g., macro names) to be used for property in the
    # output.
    #
    # Generates: DT_<DEV_IDENT>_<PATH_ALIAS>_<PROP_NAME>
    #
    # with <PATH_ALIAS> coming from the '/aliases' node

    aliases = []

    for dev_alias in dev.aliases:
        alias = "DT_{}_{}_{}".format(
                str2ident(dev.matching_compat), str2ident(dev_alias), prop_name)

        aliases.append(alias)

    return aliases


def dev_instance_aliases(dev, prop_name):
    # Returns a list of aliases for property based on the
    # instance number(s) of the register's device (based on how many instances
    # of that particular device there are).
    #
    # This is a list since a device can have multiple 'compatible' strings,
    # each with their own instance number.
    #
    # Generates: DT_<DEV_IDENT>_<INSTANCE>_<PROP_NAME>

    idents = []

    for compat in dev.compats:
        ident = "DT_{}_{}_{}".format(
            str2ident(compat), dev.instance_no[compat], prop_name)

        idents.append(ident)

    return idents


def write_flash(flash_dev):
    # Writes the size and address of the node pointed at by the zephyr,flash
    # property in /chosen

    if len(flash_dev.regs) != 1:
        _err("Expected zephyr,flash to have a single register, has {}"
             .format(len(flash_dev.regs)))

    reg = flash_dev.regs[0]

    out("#define DT_FLASH_BASE_ADDRESS\t0x{:x}".format(reg.addr))
    if reg.size is not None:
        out("#define DT_FLASH_SIZE\t{}".format(reg.size//1024))


def write_flash_partition(partition_dev):
    if partition_dev.label is None:
        _err("missing 'label' property on {!r}".format(partition_dev))

    label = str2ident(partition_dev.label)

    out("#define DT_FLASH_AREA_{0}_LABEL\t{0}".format(label))
    out("#define DT_FLASH_AREA_{}_READ_ONLY\t{}".format(
            label, 1 if partition_dev.read_only else 0))

    for i, reg in enumerate(partition_dev.regs):
        out("#define DT_FLASH_AREA_{}_OFFSET_{} {}".format(label, i, reg.addr))
        out("#define DT_FLASH_AREA_{}_SIZE_{} {}".format(label, i, reg.size))

    # Add aliases that points to the first sector
    #
    # TODO: Could we get rid of this? Code could just refer to sector _0 where
    # needed instead.

    out("#define DT_FLASH_AREA_{0}_OFFSET\tDT_FLASH_AREA_{0}_OFFSET_0".format(
            label))
    out("#define DT_FLASH_AREA_{0}_SIZE\tDT_FLASH_AREA_{0}_SIZE_0".format(
            label))


def out(s):
    # TODO: This is just for writing the header. Will get a .conf file later as
    # well.

    print(s, file=_out)


def _err(s):
    raise Exception(s)


if __name__ == "__main__":
    main()
