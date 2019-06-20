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
            write_aliases(dev)
            write_props(dev)

            # Generate defines of the form
            #
            #   #define DT_<COMPAT>_<INSTANCE> 1
            #
            # These are flags for which devices exist.
            for compat in dev.compats:
                out("#define DT_{}_{}\t1"
                    .format(str2ident(compat), dev.instance_no[compat]))

    # These are derived from /chosen

    if edt.sram_dev:
        # TODO: Check that regs[0] exists
        reg = edt.sram_dev.regs[0]
        out("#define DT_SRAM_BASE_ADDRESS\t0x{:x}".format(reg.addr))
        out("#define DT_SRAM_SIZE\t{}".format(reg.size//1024))

    if edt.ccm_dev:
        # TODO: Check that regs[0] exists
        reg = edt.ccm_dev.regs[0]
        out("#define DT_CCM_BASE_ADDRESS\t0x{:x}".format(reg.addr))
        out("#define DT_CCM_SIZE\t{}".format(reg.size//1024))

    write_label("DT_UART_CONSOLE_LABEL", edt.console_dev)
    write_label("DT_UART_SHELL_LABEL",   edt.shell_uart_dev)
    write_label("DT_BT_UART_LABEL",      edt.bt_uart_dev)
    write_label("DT_UART_PIPE_LABEL",    edt.uart_pipe_dev)
    write_label("DT_BT_MONITOR_LABEL",   edt.bt_mon_uart_dev)
    write_label("DT_UART_MCUMGR_LABEL",  edt.uart_mcumgr_dev)

    if edt.flash_dev:
        write_flash(edt.flash_dev)

    for dev in edt.devices:
        # TODO: Feels a bit janky to handle this separately from
        # zephyr,flash-dev
        if dev.name.startswith("partition@"):
            write_flash_partition(dev)


def write_regs(dev):
    for reg in dev.regs:
        out("#define {}\t0x{:x}".format(reg_addr_ident(reg), reg.addr))
        if reg.size is not None:
            out("#define {}\t{}".format(reg_size_ident(reg), reg.size))


def write_aliases(dev):
    for reg in dev.regs:
        ident = reg_addr_ident(reg)
        for alias in reg_aliases(reg):
            # Avoid writing aliases that overlap with the base identifier for
            # the register
            if alias != ident:
                out("#define {}\t{}".format(alias, ident))


def write_props(dev):
    # Writes any properties defined in the "properties" section of the binding
    # for the device

    # TODO: The YAML for these isn't quite regular
    if dev.matching_compat in {"gpio-keys", "gpio-leds"}:
        return

    for name, val in dev.props.items():
        # Skip #size-cell and other property starting with #. Also skip mapping
        # properties like "gpio-map".
        if name[0] == "#" or name.endswith("-map"):
            continue

        # TODO: Add support for some of these properties elsewhere
        if name in {"reg", "interrupts", "clocks", "compatible"}:
            continue

        ident = "{}_{}".format(dev_ident(dev), str2ident(name))

        if isinstance(val, bool):
            out("#define {}\t{}".format(ident, 1 if val else 0))
        elif isinstance(val, str):
            out('#define {}\t"{}"'.format(ident, val))
        elif isinstance(val, int):
            out("#define {}\t{}".format(ident, val))
        elif isinstance(val, list):
            for i, elm in enumerate(val):
                if isinstance(elm, str):
                    elm = '"{}"'.format(elm)
                out("#define {}_{}\t{}".format(ident, i, elm))
        else:
            # Internal error
            assert False


def reg_addr_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the address of
    # 'reg' in the output

    dev = reg.dev

    return "{}_BASE_ADDRESS_{}".format(dev_ident(dev), dev.regs.index(reg))


def reg_size_ident(reg):
    # Returns the identifier (e.g., macro name) to be used for the size of
    # 'reg' in the output

    dev = reg.dev

    return "{}_SIZE_{}".format(dev_ident(dev), dev.regs.index(reg))


def dev_ident(dev):
    # Returns an identifier for the Device 'dev'. Used when building e.g. macro
    # names.

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
    else:
        # This is a bit of a hack
        ident += "_{}".format(str2ident(dev.name))

    return ident


def reg_aliases(reg):
    # Returns a list of aliases (e.g., macro names) to be used for 'reg' in the
    # output. TODO: give example output

    dev = reg.dev

    aliases = []

    for dev_alias in dev_aliases(dev):
        alias = dev_alias + "_BASE_ADDRESS"
        if len(dev.regs) > 1:
            alias += "_" + str(dev.regs.index(reg))
        aliases.append(alias)

        if reg.name:
            aliases.append("{}_{}_BASE_ADDRESS".format(
                dev_alias, str2ident(reg.name)))

    if reg.name:
        aliases.append(reg_name_alias(reg))

    return aliases


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

    return ["DT_{}_{}".format(compat_s, str2ident(alias))
            for alias in dev.aliases]


def dev_instance_aliases(dev):
    # Returns a list of aliases for the Device 'dev', based on the instance
    # number of the device (based on how many instances of that particular
    # device there are).
    #
    # This is a list since a device can have multiple 'compatible' strings,
    # each with their own instance number.

    return ["DT_{}_{}".format(str2ident(compat), dev.instance_no[compat])
            for compat in dev.compats]


def reg_name_alias(reg):
    # reg_aliases() helper. Returns an alias based on 'reg's name.
    # TODO: Is this needed?

    dev = reg.dev
    return "DT_{}_{:X}_{}_BASE_ADDRESS".format(
        str2ident(dev.matching_compat), dev.regs[0].addr, str2ident(reg.name))


def write_flash(flash_dev):
    # Writes the size and address of the node pointed at by the zephyr,flash
    # property in /chosen

    if len(flash_dev.regs) != 1:
        err("Expected zephyr,flash to have a single register, has {}"
            .format(len(flash_dev.regs)))

    reg = flash_dev.regs[0]

    out("#define DT_FLASH_BASE_ADDRESS\t0x{:x}".format(reg.addr))
    if reg.size is not None:
        out("#define DT_FLASH_SIZE\t{}".format(reg.size//1024))


def write_flash_partition(partition_dev):
    if partition_dev.label is None:
        err("missing 'label' property on {!r}".format(partition_dev))

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

    out('#define {}\t"{}"'.format(ident, dev.label))


def str2ident(s):
    # Change ,-@/ to _ and uppercase
    return s.replace("-", "_") \
            .replace(",", "_") \
            .replace("@", "_") \
            .replace("/", "_") \
            .upper()


def out(s):
    # TODO: This is just for writing the header. Will get a .conf file later as
    # well.

    print(s, file=_out)


def err(s):
    raise Exception(s)


if __name__ == "__main__":
    main()
