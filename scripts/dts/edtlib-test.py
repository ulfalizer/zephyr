#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import edtlib


edt = edtlib.EDT("test.dts", "../../dts/bindings")
for dev in edt.devices:
    print("Device " + dev.name)
    print("\tInterrupts: " + str(dev.interrupts))

    for i, reg in enumerate(dev.regs):
        print("\tRegister " + str(i))
        if reg.name is not None:
            print("\t\tName: " + reg.name)
        print("\t\tAddress: " + hex(reg.addr))
        print("\t\tSize: " + hex(reg.size))

    for i, gpio in enumerate(dev.gpios):
        print("\tGPIO " + str(i) + ": " + str(gpio))

if edt.sram_dev:
    print("SRAM device: " + str(edt.sram_dev))

if edt.ccm_dev:
    print("CCM device: " + str(edt.ccm_dev))
