#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import argparse
import os
import sys

import edtlib


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
            write_regs(dev)
            write_props(dev)

            # Generate defines of the form
            #
            #   #define DT_<COMPAT>_<INSTANCE> 1
            #
            # These are flags for which devices exist.
            for compat in dev.compats:
                out("{}_{}"
                    .format(str2ident(compat), dev.instance_no[compat]), 1)

    # These are derived from /chosen

    if edt.sram_dev:
        # TODO: Check that regs[0] exists
        reg = edt.sram_dev.regs[0]
        out("SRAM_BASE_ADDRESS", hex(reg.addr))
        out("SRAM_SIZE", reg.size//1024)

    if edt.ccm_dev:
        # TODO: Check that regs[0] exists
        reg = edt.ccm_dev.regs[0]
        out("CCM_BASE_ADDRESS", hex(reg.addr))
        out("CCM_SIZE", reg.size//1024)

    # NOTE: These defines aren't used by the code and just used by
    # the kconfig build system, we can remove them in the future
    # if we provide a function in kconfigfunctions.py to get
    # the same info
    write_label("UART_CONSOLE_ON_DEV_NAME", edt.console_dev)
    write_label("UART_SHELL_ON_DEV_NAME",   edt.shell_uart_dev)
    write_label("BT_UART_ON_DEV_NAME",      edt.bt_uart_dev)
    write_label("UART_PIPE_ON_DEV_NAME",    edt.uart_pipe_dev)
    write_label("BT_MONITOR_ON_DEV_NAME",   edt.bt_mon_uart_dev)
    write_label("UART_MCUMGR_ON_DEV_NAME",  edt.uart_mcumgr_dev)

    if edt.flash_dev:
        write_flash(edt.flash_dev)

    for dev in edt.devices:
        # TODO: Feels a bit janky to handle this separately from
        # zephyr,flash-dev
        if dev.name.startswith("partition@"):
            write_flash_partition(dev)


def write_regs(dev):
    # Writes address/size output for the registers in dev's 'reg' property

    for reg in dev.regs:
        out_dev(dev, reg_addr_ident(reg), hex(reg.addr))
        if reg.size is not None:
            out_dev(dev, reg_size_ident(reg), reg.size)


def write_props(dev):
    # Writes any properties defined in the "properties" section of the binding
    # for the device

    # TODO: The YAML for these isn't quite regular
    if dev.matching_compat in {"gpio-keys", "gpio-leds"}:
        return

    for prop in dev.props:
        # Skip #size-cell and other property starting with #. Also skip mapping
        # properties like "gpio-map".
        if prop.name[0] == "#" or prop.name.endswith("-map"):
            continue

        # TODO: Add support for some of these properties elsewhere
        if prop.name in {"reg", "interrupts", "clocks", "compatible"}:
            continue

        ident = "{}_{}".format(dev_ident(dev), str2ident(prop.name))

        if isinstance(prop.val, bool):
            out(ident, 1 if prop.val else 0)
        elif isinstance(prop.val, str):
            out(ident, '"{}"'.format(prop.val))
        elif isinstance(prop.val, int):
            out(ident, prop.val)
        elif isinstance(prop.val, list):
            for i, elm in enumerate(prop.val):
                if isinstance(elm, str):
                    elm = '"{}"'.format(elm)
                out("{}_{}".format(ident, i), elm)
        else:
            # Internal error
            assert False

        # Generate DT_..._ENUM if there's an 'enum:' key in the binding
        if prop.enum_index is not None:
            out(ident + "_ENUM", prop.enum_index)


def reg_addr_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the address of
    # 'reg' in the output

    dev = reg.dev

    # NOTE: to maintain compat wit the old script we special case if there's
    # only a single register (we drop the '_0').
    if len(dev.regs) > 1:
        return "BASE_ADDRESS_{}".format(dev.regs.index(reg))
    else:
        return "BASE_ADDRESS"


def reg_size_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the size of
    # 'reg' in the output

    dev = reg.dev

    # NOTE: to maintain compat wit the old script we special case if there's
    # only a single register (we drop the '_0').
    if len(dev.regs) > 1:
        return "SIZE_{}".format(dev.regs.index(reg))
    else:
        return "SIZE"


def reg_addr_aliases(reg):
    # Returns a list of aliases for the address of 'reg'

    return [alias + "_BASE_ADDRESS" for alias in reg_aliases(reg)]


def reg_name_alias(reg):
    # reg_aliases() helper. Returns an alias based on 'reg's name.
    # TODO: Is this needed?

    dev = reg.dev
    return "{}_{:X}_{}".format(
        str2ident(dev.matching_compat), dev.regs[0].addr, str2ident(reg.name))


def dev_ident(dev):
    # Returns an identifier for the Device 'dev'. Used when building e.g. macro
    # names.

    # TODO: Handle PWM on STM
    # TODO: Better document the rules of how we generate things

    ident = ""

    # TODO: Factor out helper? Seems to be the same thing being done to the
    # node and the parent. Maybe elsewhere too.

    if dev.bus:
        ident += "{}_{:X}_".format(
            str2ident(dev.parent.matching_compat), dev.parent.unit_addr)

    ident += "{}_".format(str2ident(dev.matching_compat))

    if dev.unit_addr is not None:
        ident += "{:X}".format(dev.unit_addr)
    else:
        # This is a bit of a hack
        ident += "{}".format(str2ident(dev.name))

    return ident


def dev_aliases(dev):
    # Returns a list of aliases for the Device 'dev', used e.g. when building
    # macro names

    return dev_path_aliases(dev) + dev_instance_aliases(dev)


def dev_path_aliases(dev):
    # Returns a list of aliases for the Device 'dev', based on the aliases
    # registered for the device, in the /aliases node. Used when building e.g.
    # macro names.

    if dev.matching_compat is None:
        return []

    compat_s = str2ident(dev.matching_compat)

    return ["ALIAS_{}".format(str2ident(alias)) for alias in dev.aliases]


def dev_instance_aliases(dev):
    # Returns a list of aliases for the Device 'dev', based on the instance
    # number of the device (based on how many instances of that particular
    # device there are).
    #
    # This is a list since a device can have multiple 'compatible' strings,
    # each with their own instance number.

    return ["INST_{}_{}".format(dev.instance_no[compat], str2ident(compat))
            for compat in dev.compats]


def write_flash(flash_dev):
    # Writes the size and address of the node pointed at by the zephyr,flash
    # property in /chosen

    if len(flash_dev.regs) != 1:
        err("Expected zephyr,flash to have a single register, has {}"
            .format(len(flash_dev.regs)))

    reg = flash_dev.regs[0]

    out("FLASH_BASE_ADDRESS", hex(reg.addr))
    if reg.size is not None:
        out("FLASH_SIZE", reg.size//1024)


def write_flash_partition(partition_dev):
    if partition_dev.label is None:
        err("missing 'label' property on {!r}".format(partition_dev))

    label = str2ident(partition_dev.label)

    out("FLASH_AREA_{}_LABEL".format(label), label)
    out("FLASH_AREA_{}_READ_ONLY".format(label),
            1 if partition_dev.read_only else 0)

    for i, reg in enumerate(partition_dev.regs):
        out("FLASH_AREA_{}_OFFSET_{}".format(label, i), reg.addr)
        out("FLASH_AREA_{}_SIZE_{}".format(label, i), reg.size)

    # Add aliases that points to the first sector
    #
    # TODO: Could we get rid of this? Code could just refer to sector _0 where
    # needed instead.

    out_alias("FLASH_AREA_{}_OFFSET".format(label),
              "FLASH_AREA_{}_OFFSET_0".format(label))
    out_alias("FLASH_AREA_{}_SIZE".format(label),
              "FLASH_AREA_{}_SIZE_0".format(label))


def write_label(ident, dev):
    # Helper function. Writes '#define <ident> <label>', where <label>
    # is the value of the 'label' property from 'dev'. Does nothing if
    # 'dev' is None.
    #
    # Errors out if 'dev' exists but has no label.

    if not dev:
        return

    if dev.label is None:
        err("missing 'label' property on {!r}".format(dev))

    out(ident, '"{}"'.format(dev.label))


def str2ident(s):
    # Change ,-@/ to _ and uppercase
    return s.replace("-", "_") \
            .replace(",", "_") \
            .replace("@", "_") \
            .replace("/", "_") \
            .upper()


def out(ident, val):
    # TODO: This is just for writing the header. Will get a .conf file later as
    # well.

    print("#define DT_{}\t{}".format(ident, val), file=_out)


def out_alias(ident, target):
    # TODO: This is just for writing the header. Will get a .conf file later as
    # well.

    # TODO: Make this check unnecessary?
    if ident != target:
        print("#define DT_{}\tDT_{}".format(ident, target), file=_out)


def out_dev(dev, ident, val):
    # TODO: Document

    ident_with_dev = dev_ident(dev) + "_" + ident

    # Write main entry
    out(ident_with_dev, val)

    # Write aliases that point to it
    for dev_alias in dev_aliases(dev):
        out_alias(dev_alias + "_" + ident, ident_with_dev)


def err(s):
    raise Exception(s)


if __name__ == "__main__":
    main()
