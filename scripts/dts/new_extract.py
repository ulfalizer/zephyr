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

    active_compats = set()

    for dev in edt.devices:
        if dev.enabled and dev.binding:
            write_regs(dev)
            write_irqs(dev)
            write_gpios(dev)
            write_pwms(dev)
            write_spi_dev(dev)
            write_props(dev)
            write_bus(dev)
            write_existence_flags(dev)

            active_compats.update(dev.compats)

    # Generate defines of the form
    #
    #   #define DT_COMPAT_<COMPAT> 1
    #
    # These are flags for which compats exist on (enabled) DT nodes.
    for compat in active_compats:
        out("COMPAT_{}".format(str2ident(compat)), 1)

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
    write_required_label("UART_CONSOLE_ON_DEV_NAME", edt.console_dev)
    write_required_label("UART_SHELL_ON_DEV_NAME",   edt.shell_uart_dev)
    write_required_label("BT_UART_ON_DEV_NAME",      edt.bt_uart_dev)
    write_required_label("UART_PIPE_ON_DEV_NAME",    edt.uart_pipe_dev)
    write_required_label("BT_MONITOR_ON_DEV_NAME",   edt.bt_mon_uart_dev)
    write_required_label("UART_MCUMGR_ON_DEV_NAME",  edt.uart_mcumgr_dev)
    write_required_label("BT_C2H_UART_ON_DEV_NAME",  edt.bt_c2h_uart_dev)

    write_flash(edt.flash_dev)

    flash_index = 0
    for dev in edt.devices:
        if dev.name.startswith("partition@"):
            write_flash_partition(dev, flash_index)
            flash_index += 1

    # Number of flash partitions
    out("FLASH_AREA_NUM", flash_index)


def write_regs(dev):
    # Writes address/size output for the registers in dev's 'reg' property

    for reg in dev.regs:
        out_dev(dev, reg_addr_ident(reg), hex(reg.addr))
        if reg.name:
            ident = str2ident(reg.name) + "_BASE_ADDRESS"
            out_name_aliases(dev, ident, reg_addr_ident(reg))

        if reg.size is not None:
            out_dev(dev, reg_size_ident(reg), reg.size)
            if reg.name:
                ident = str2ident(reg.name) + "_SIZE"
                out_name_aliases(dev, ident, reg_size_ident(reg))


def write_props(dev):
    # Writes any properties defined in the "properties" section of the binding
    # for the device

    for prop in dev.props:
        # Skip #size-cell and other property starting with #. Also skip mapping
        # properties like "gpio-map".
        if prop.name[0] == "#" or prop.name.endswith("-map"):
            continue

        # skip properties that we handle elsewhere
        if prop.name in {"reg", "interrupts", "pwms"} or prop.name.endswith("gpios"):
            continue
        # TODO: Add support for some of these properties elsewhere
        if prop.name in {"clocks", "compatible"}:
            continue

        ident = str2ident(prop.name)

        if isinstance(prop.val, bool):
            out_dev(dev, ident, 1 if prop.val else 0)
        elif isinstance(prop.val, str):
            out_dev_s(dev, ident, prop.val)
        elif isinstance(prop.val, int):
            out_dev(dev, ident, prop.val)
        elif isinstance(prop.val, list):
            for i, elm in enumerate(prop.val):
                out_fn = out_dev_s if isinstance(elm, str) else out_dev
                out_fn(dev, "{}_{}".format(ident, i), elm)
        else:
            # Internal error
            assert False

        # Generate DT_..._ENUM if there's an 'enum:' key in the binding
        if prop.enum_index is not None:
            out_dev(dev, ident + "_ENUM", prop.enum_index)


def write_bus(dev):
    # Generate bus-related #defines

    if not dev.bus:
        return

    if dev.parent.label is None:
        _err("missing 'label' property on {!r}".format(dev.parent))
    # #define DT_<DEV-IDENT>_BUS_NAME <BUS-LABEL>
    out_dev_s(dev, "BUS_NAME", str2ident(dev.parent.label))

    for compat in dev.compats:
        ident = "{}_BUS_{}".format(str2ident(compat), str2ident(dev.bus))
        # #define DT_<COMPAT>_BUS_<BUS TYPE> 1
        out(ident, 1)
        if compat == dev.matching_compat:
            # TODO
            pass


def write_existence_flags(dev):
    # Generate #defines of the form
    #
    #   #define DT_<COMPAT>_<INSTANCE> 1
    #
    # These are flags for which devices exist.

    for compat in dev.compats:
        out("{}_{}".format(str2ident(compat), dev.instance_no[compat]), 1)


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
    elif dev.parent.unit_addr is not None:
        ident += "{:X}_{}".format(dev.parent.unit_addr, str2ident(dev.name))
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

    aliases = []
    for alias in dev.aliases:
        aliases.append("ALIAS_{}".format(str2ident(alias)))

        # TODO: See if we can remove or deprecate this form
        aliases.append("{}_{}".format(compat_s, str2ident(alias)))

    return aliases


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

    if not flash_dev:
        # No flash device. Write dummy values.
        out("FLASH_BASE_ADDRESS", 0)
        out("FLASH_SIZE", 0)
        return

    if len(flash_dev.regs) != 1:
        err("expected zephyr,flash to have a single register, has {}"
            .format(len(flash_dev.regs)))

    reg = flash_dev.regs[0]

    out("FLASH_BASE_ADDRESS", hex(reg.addr))
    if reg.size is not None:
        out("FLASH_SIZE", reg.size//1024)


def write_flash_partition(partition_dev, index):
    if partition_dev.label is None:
        err("missing 'label' property on {!r}".format(partition_dev))

    # Generate label-based identifiers
    write_flash_partition_prefix(
        "FLASH_AREA_" + str2ident(partition_dev.label), partition_dev, index)

    # Generate index-based identifiers
    write_flash_partition_prefix(
        "FLASH_AREA_{}".format(index), partition_dev, index)


def write_flash_partition_prefix(prefix, partition_dev, index):
    # write_flash_partition() helper. Generates identifiers starting with
    # 'prefix'.

    out_s("{}_LABEL".format(prefix), partition_dev.label)
    out("{}_ID".format(prefix), index)

    out("{}_READ_ONLY".format(prefix), 1 if partition_dev.read_only else 0)

    for i, reg in enumerate(partition_dev.regs):
        out("{}_OFFSET_{}".format(prefix, i), reg.addr)
        out("{}_SIZE_{}".format(prefix, i), reg.size)

    # Add aliases that points to the first sector
    #
    # TODO: Could we get rid of this? Code could just refer to sector _0 where
    # needed instead.

    out_alias("{}_OFFSET".format(prefix), "{}_OFFSET_0".format(prefix))
    out_alias("{}_SIZE".format(prefix), "{}_SIZE_0".format(prefix))

    controller = partition_dev.flash_controller
    if controller.label is not None:
        out_s("{}_DEV".format(prefix), controller.label)


def write_required_label(ident, dev):
    # Helper function. Writes '#define <ident> "<label>"', where <label>
    # is the value of the 'label' property from 'dev'. Does nothing if
    # 'dev' is None.
    #
    # Errors out if 'dev' exists but has no label.

    if not dev:
        return

    if dev.label is None:
        err("missing 'label' property on {!r}".format(dev))

    out_s(ident, dev.label)


def write_irqs(dev):
    # Writes IRQ num and data for the interrupts in dev's 'interrupt' property

    for irq_i, irq in enumerate(dev.interrupts):
        # We ignore the controller for now
        for cell_name, cell_value in irq.specifier.items():
            irq_ident = "IRQ_{}".format(irq_i)
            if cell_name != "irq":
                irq_ident += "_" + str2ident(cell_name)

            out_dev(dev, irq_ident, cell_value)

            # If the IRQ has a name (from 'interrupt-names'), write an alias
            # based on it
            if irq.name:
                irq_ident_name = "IRQ_{}".format(str2ident(irq.name))
                if cell_name != "irq":
                    irq_ident_name += "_" + str2ident(cell_name)

                out_name_aliases(dev, irq_ident_name, irq_ident)


def write_gpios(dev):
    # Writes GPIO controller data for the gpios in dev's 'gpios' property

    for gpios in dev.gpios.values():
        for gpio_i, gpio in enumerate(gpios):
            write_gpio(dev, gpio, gpio_i if len(gpios) > 1 else None)


def write_gpio(dev, gpio, index=None):
    # Writes GPIO controller & data for the GPIO object 'gpio'. If 'index' is
    # not None, it is added as a suffix to identifiers.

    ctrl_ident = "GPIOS_CONTROLLER"
    if gpio.name:
        ctrl_ident = str2ident(gpio.name) + "_" + ctrl_ident
    if index is not None:
        ctrl_ident += "_{}".format(index)

    out_dev_s(dev, ctrl_ident, gpio.controller.label)

    for cell, val in gpio.specifier.items():
        cell_ident = "GPIOS_" + str2ident(cell)
        if gpio.name:
            cell_ident = str2ident(gpio.name) + "_" + cell_ident
        if index is not None:
            cell_ident += "_{}".format(index)

        out_dev(dev, cell_ident, val)


def write_spi_dev(dev):
    # Writes SPI device GPIO chip select data if there is any

    cs_gpio = edtlib.spi_dev_cs_gpio(dev)
    if cs_gpio is not None:
        write_gpio(dev, cs_gpio)


def write_pwms(dev):
    # Writes PWM controller and specifier info for the PWMs in dev's 'pwms'
    # property

    for pwm in dev.pwms:
        out_dev_s(dev, "PWMS_CONTROLLER", pwm.controller.label)
        for spec, val in pwm.specifier.items():
            out_dev(dev, "PWMS_" + str2ident(spec), val)


def str2ident(s):
    # Change ,-@/ to _ and uppercase
    return s.replace("-", "_") \
            .replace(",", "_") \
            .replace("@", "_") \
            .replace("/", "_") \
            .replace("+", "PLUS") \
            .upper()


def out_dev_s(dev, ident, s):
    # Like out_dev(), but puts quotes around 's' and escapes any double quotes
    # and backslashes within it

    # \ must be escaped before " to avoid double escaping
    out_dev(dev, ident, '"{}"'.format(escape(s)))


def out_dev(dev, ident, val):
    # Writes an <ident>=<val> assignment, along with aliases for <ident> based
    # on 'dev'

    # Write assignment and aliases
    out(dev_ident(dev) + "_" + ident, val)
    out_dev_aliases(dev, ident, ident)


def out_name_aliases(dev, ident, target):
    # Writes aliases for 'target', based on 'dev' and 'ident'. The device
    # prefix is automatically added to 'target'.  This version is used when
    # you need to additional creat an alias between 'ident' and 'target'.
    # TODO: Give example
    out_dev_aliases(dev, ident, target)

    # Create an alias for something like:
    # <DEV_IDENT>_IRQ_COMMAND_COMPLETE <DEV_IDENT>_IRQ_0
    out_alias(dev_ident(dev) + "_" + ident,
              dev_ident(dev) + "_" + target)


def out_dev_aliases(dev, ident, target):
    # Writes aliases for 'target', based on 'dev' and 'ident'. The device
    # prefix is automatically added to 'target'.
    # TODO: Give example

    target = dev_ident(dev) + "_" + target
    for dev_alias in dev_aliases(dev):
        out_alias(dev_alias + "_" + ident, target)


# TODO: These are just for writing the header. Will get a .conf file later as
# well.


def out_s(ident, val):
    # Like out(), but puts quotes around 's' and escapes any double quotes and
    # backslashes within it

    out(ident, '"{}"'.format(escape(val)))


def out(ident, val):
    print("#define DT_{}\t{}".format(ident, val), file=_out)


def out_alias(ident, target):
    # TODO: This is just for writing the header. Will get a .conf file later as
    # well.

    if ident != target:
        print("#define DT_{}\tDT_{}".format(ident, target), file=_out)


def escape(s):
    # Backslash-escapes any double quotes and backslashes in 's'

    # \ must be escaped before " to avoid double escaping
    return s.replace("\\", "\\\\").replace('"', '\\"')


def err(s):
    raise Exception(s)


if __name__ == "__main__":
    main()
