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


edt = edtlib.EDT("test.dts", "bindings")

#
# Test interrupts
#

verify_eq(str(edt.get_dev("/interrupt-parent-test/node").interrupts),
          "[(<Device controller, 0 regs>, {'one': 1, 'two': 2, 'three': 3}), (<Device controller, 0 regs>, {'one': 4, 'two': 5, 'three': 6})]")

verify_eq(str(edt.get_dev("/interrupts-extended-test/node").interrupts),
          "[(<Device controller-0, 0 regs>, {'one': 1}), (<Device controller-1, 0 regs>, {'one': 2, 'two': 3}), (<Device controller-2, 0 regs>, {'one': 4, 'two': 5, 'three': 6})]")

verify_eq(str(edt.get_dev("/interrupt-map-test/node@0").interrupts),
          "[(<Device controller-0, 0 regs>, {'one': 0}), (<Device controller-1, 0 regs>, {'one': 0, 'two': 1}), (<Device controller-2, 0 regs>, {'one': 0, 'two': 0, 'three': 2})]")

verify_eq(str(edt.get_dev("/interrupt-map-test/node@1").interrupts),
          "[(<Device controller-0, 0 regs>, {'one': 3}), (<Device controller-1, 0 regs>, {'one': 0, 'two': 4}), (<Device controller-2, 0 regs>, {'one': 0, 'two': 0, 'three': 5})]")

verify_eq(str(edt.get_dev("/interrupt-map-bitops-test/node@70000000E").interrupts),
          "[(<Device controller, 0 regs>, {'one': 3, 'two': 2})]")

print("all tests passed")
