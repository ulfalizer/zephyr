#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import edtlib


edt = edtlib.EDT("test.dts", "../../dts/bindings")
for dev in edt.devices:
    print("registers in " + dev.name + ":")
    for i, reg in enumerate(dev.regs):
        print("register " + str(i) + ": ")
        if reg.name is not None:
            print("\tname: " + reg.name)
        print("\taddress: " + hex(reg.addr))
        print("\tsize: " + hex(reg.size))
    print()
    if dev.interrupt_parent:
        print("interrupt parent for " + dev.name + ":")
        print(dev.interrupt_parent)
        print()
        print("interrupts for " + dev.name + ":")
        print(dev.interrupts)
        print()
