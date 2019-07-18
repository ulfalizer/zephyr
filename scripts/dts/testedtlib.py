#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import edtlib


def fail(msg):
    raise Exception("test failed: " + msg)


def verify_streq(actual, expected):
    actual = str(actual)
    if actual != expected:
        # Put values on separate lines to make it easy to spot differences
        fail("not equal (expected value last):\n'{}'\n'{}'"
             .format(actual, expected))


edt = edtlib.EDT("test.dts", "bindings")

#
# Test interrupts
#

verify_streq(edt.get_dev("/interrupt-parent-test/node").interrupts,
             "[<Interrupt, name: foo, target: <Device /interrupt-parent-test/controller in 'test.dts', binding bindings/interrupt-3-cell.yaml>, specifier: {'one': 1, 'two': 2, 'three': 3}>, <Interrupt, name: bar, target: <Device /interrupt-parent-test/controller in 'test.dts', binding bindings/interrupt-3-cell.yaml>, specifier: {'one': 4, 'two': 5, 'three': 6}>]")

verify_streq(edt.get_dev("/interrupts-extended-test/node").interrupts,
             "[<Interrupt, target: <Device /interrupts-extended-test/controller-0 in 'test.dts', binding bindings/interrupt-1-cell.yaml>, specifier: {'one': 1}>, <Interrupt, target: <Device /interrupts-extended-test/controller-1 in 'test.dts', binding bindings/interrupt-2-cell.yaml>, specifier: {'one': 2, 'two': 3}>, <Interrupt, target: <Device /interrupts-extended-test/controller-2 in 'test.dts', binding bindings/interrupt-3-cell.yaml>, specifier: {'one': 4, 'two': 5, 'three': 6}>]")

verify_streq(edt.get_dev("/interrupt-map-test/node@0").interrupts,
             "[<Interrupt, target: <Device /interrupt-map-test/controller-0 in 'test.dts', binding bindings/interrupt-1-cell.yaml>, specifier: {'one': 0}>, <Interrupt, target: <Device /interrupt-map-test/controller-1 in 'test.dts', binding bindings/interrupt-2-cell.yaml>, specifier: {'one': 0, 'two': 1}>, <Interrupt, target: <Device /interrupt-map-test/controller-2 in 'test.dts', binding bindings/interrupt-3-cell.yaml>, specifier: {'one': 0, 'two': 0, 'three': 2}>]")

verify_streq(edt.get_dev("/interrupt-map-test/node@1").interrupts,
             "[<Interrupt, target: <Device /interrupt-map-test/controller-0 in 'test.dts', binding bindings/interrupt-1-cell.yaml>, specifier: {'one': 3}>, <Interrupt, target: <Device /interrupt-map-test/controller-1 in 'test.dts', binding bindings/interrupt-2-cell.yaml>, specifier: {'one': 0, 'two': 4}>, <Interrupt, target: <Device /interrupt-map-test/controller-2 in 'test.dts', binding bindings/interrupt-3-cell.yaml>, specifier: {'one': 0, 'two': 0, 'three': 5}>]")

verify_streq(edt.get_dev("/interrupt-map-bitops-test/node@70000000E").interrupts,
             "[<Interrupt, target: <Device /interrupt-map-bitops-test/controller in 'test.dts', binding bindings/interrupt-2-cell.yaml>, specifier: {'one': 3, 'two': 2}>]")

#
# Test GPIOS
#

verify_streq(edt.get_dev("/gpio-test/node").gpios,
             "{'': [<GPIO, name: , target: <Device /gpio-test/controller-0 in 'test.dts', binding bindings/gpio-1-cell.yaml>, specifier: {'one': 1}>, <GPIO, name: , target: <Device /gpio-test/controller-1 in 'test.dts', binding bindings/gpio-2-cell.yaml>, specifier: {'one': 2, 'two': 3}>], 'foo': [<GPIO, name: foo, target: <Device /gpio-test/controller-1 in 'test.dts', binding bindings/gpio-2-cell.yaml>, specifier: {'one': 4, 'two': 5}>], 'bar': [<GPIO, name: bar, target: <Device /gpio-test/controller-1 in 'test.dts', binding bindings/gpio-2-cell.yaml>, specifier: {'one': 6, 'two': 7}>]}")

#
# Test 'reg'
#

verify_streq(edt.get_dev("/reg-zero-address-cells/node").regs,
             "[<Register, addr: 0x0, size: 0x1>, <Register, addr: 0x0, size: 0x2>]")

verify_streq(edt.get_dev("/reg-zero-size-cells/node").regs,
             "[<Register, addr: 0x1, size: 0x0>, <Register, addr: 0x2, size: 0x0>]")

verify_streq(edt.get_dev("/reg-ranges/parent/node").regs,
             "[<Register, addr: 0x5, size: 0x1>, <Register, addr: 0xe0000000f, size: 0x1>, <Register, addr: 0xc0000000e, size: 0x1>, <Register, addr: 0xc0000000d, size: 0x1>, <Register, addr: 0xa0000000b, size: 0x1>, <Register, addr: 0x0, size: 0x1>]")

verify_streq(edt.get_dev("/reg-nested-ranges/grandparent/parent/node").regs,
             "[<Register, addr: 0x30000000200000001, size: 0x0>]")

#
# Test !include in bindings
#

verify_streq(edt.get_dev("/binding-include").description,
             "Parent binding")

verify_streq(edt.get_dev("/binding-include").props,
             "{'compatible': <Property, name: compatible, value: ['binding-include-test']>, 'foo': <Property, name: foo, value: 0>, 'bar': <Property, name: bar, value: 1>, 'baz': <Property, name: baz, value: 2>}")


print("all tests passed")
