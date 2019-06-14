#!/usr/bin/env python3

# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

import edtlib


def fail(msg):
    raise Exception("test failed: " + msg)


def verify_eq(actual, expected):
    if actual != expected:
        # Put values on separate lines to make it easy to spot differences
        fail("not equal (expected value last):\n'{}'\n'{}'"
             .format(actual, expected))


edt = edtlib.EDT("test.dts", ".")

#
# Test interrupts
#

verify_eq(str(edt.get_dev("/interrupt-parent-test/node").interrupts),
          "[(<Device controller, 0 regs>, [1, 2, 3]), (<Device controller, 0 regs>, [4, 5, 6])]")

verify_eq(str(edt.get_dev("/interrupts-extended-test/node").interrupts),
          "[(<Device controller-0, 0 regs>, [1]), (<Device controller-1, 0 regs>, [2, 3]), (<Device controller-2, 0 regs>, [4, 5, 6])]")

verify_eq(str(edt.get_dev("/interrupt-map-test/node@0").interrupts),
          "[(<Device controller-0, 0 regs>, [0]), (<Device controller-1, 0 regs>, [0, 1]), (<Device controller-2, 0 regs>, [0, 0, 2])]")

verify_eq(str(edt.get_dev("/interrupt-map-test/node@1").interrupts),
          "[(<Device controller-0, 0 regs>, [3]), (<Device controller-1, 0 regs>, [0, 4]), (<Device controller-2, 0 regs>, [0, 0, 5])]")

print("all tests passed")
