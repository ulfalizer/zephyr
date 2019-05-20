# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
Helper library for working with .dts files at a higher level compared to dtlib.
Deals with devices, registers, bindings, etc.
"""
import fnmatch
import os
import re
import sys

import yaml

from dtlib import DT, to_num


class EDT:
    """
    Represents a "high-level" view of a device tree, with a list of devices
    that each have some number of registers, etc.

    These attributes are available on EDT instances:

    devices:
      A dictionary that maps device names to Device instances.
    """
    def __init__(self, dts, bindings_dir):
        self._find_bindings(bindings_dir)
        self._create_compat2binding()

        # Add '!include foo.yaml' handling.
        #
        # Do yaml.Loader.add_constructor() instead of yaml.add_constructor() to be
        # compatible with both version 3.13 and version 5.1 of PyYAML.
        #
        # TODO: Is there some 3.13/5.1-compatible way to only do this once, even
        # if multiple EDT instances are created?
        yaml.Loader.add_constructor("!include", self._binding_include)

        # Maps dtlib.Node's to their corresponding Devices
        self._node2dev = {}

        self._create_devices(dts)

    def _find_bindings(self, bindings_dir):
        # Creates a list with paths to all binding files, in self._bindings

        self._bindings = []

        for root, _, filenames in os.walk(bindings_dir):
            for filename in fnmatch.filter(filenames, "*.yaml"):
                self._bindings.append(os.path.join(root, filename))

    def _create_compat2binding(self):
        # Creates self._compat2binding, which maps each compat that's
        # implemented by some binding to the path to the binding

        self._compat2binding = {}

        for binding_path in self._bindings:
            with open(binding_path) as binding:
                for line in binding:
                    match = re.match(r'\s+constraint:\s*"([^"]*)"', line)
                    if match:
                        self._compat2binding[match.group(1)] = binding_path
                        break

    def _binding_include(self, loader, node):
        # Implements !include. Returns a list with the YAML structures for the
        # included files (a single-element list if the !include is for a single
        # file).

        if isinstance(node, yaml.ScalarNode):
            # !include foo.yaml
            return [self._binding_include_file(loader.construct_scalar(node))]

        if isinstance(node, yaml.SequenceNode):
            # !include [foo.yaml, bar.yaml]
            return [self._binding_include_file(filename)
                    for filename in loader.construct_sequence(node)]

        _yaml_inc_error("Error: unrecognised node type in !include statement")

    def _binding_include_file(self, filename):
        # _binding_include() helper for loading an !include'd file. !include
        # takes just the basename of the file, so we need to make sure there
        # aren't multiple candidates.

        paths = [path for path in self._bindings
                 if os.path.basename(path) == filename]

        if not paths:
            _yaml_inc_error("Error: '{}' not found".format(filename))

        if len(paths) > 1:
            _yaml_inc_error("Error: multiple candidates for '{}' in "
                            "!include: {}".format(filename, ", ".join(paths)))

        with open(paths[0], encoding="utf-8") as f:
            return yaml.load(f, Loader=yaml.Loader)

    def _create_devices(self, dts):
        # Creates self.devices, which maps device names to Device instances.
        # Currently, a device is defined as a node whose 'compatible' property
        # contains a compat string covered by some binding.

        self.devices = {}

        for node in DT(dts).node_iter():
            if "compatible" in node.props:
                for compat in node.props["compatible"].to_strings():
                    if compat in self._compat2binding:
                        self._create_device(node, compat)

    def _create_device(self, node, matching_compat):
        # Creates and registers a Device for 'node', which was matched to a
        # binding via the 'compatible' string 'matching_compat'

        dev = Device(self, node, matching_compat)
        self.devices[dev.name] = dev
        self._node2dev[node] = dev

    def __repr__(self):
        return "<EDT, {} devices>".format(len(self.devices))


class Device:
    """
    Represents a device.

    These attributes are available on Device instances:

    edt:
      The EDT instance this device is from.

    name:
      The name of the device. This is fetched from the node name.

    binding:
      The data from the device's binding file, in the format returned by PyYAML
      (plain Python lists, dicts, etc.)

    regs:
      A list of Register instances for the device's registers.

    bus:
      The bus the device is on, e.g. "i2c" or "spi", as a string, or None if
      non-applicable

    enabled:
      True unless the device's node has 'status = "disabled"'

    matching_compat:
      The 'compatible' string for the binding that matched the device

    instance_no:
      Unique numeric ID for the device among all devices that matched the same
      binding. Counts from zero.

      Only enabled devices (status != "disabled") are counted. 'instance_no' is
      meaningless for disabled devices.

    parent:
      The parent Device, or None if there is no parent
    """
    @property
    def name(self):
        "See the class docstring"
        return self._node.name

    @property
    def bus(self):
        "See the class docstring"
        # Complete hack to get the bus, this really should come from YAML
        possible_bus = self._node.parent.name.split("@")[0]
        if possible_bus in {"i2c", "spi"}:
            return possible_bus
        return None

    @property
    def enabled(self):
        "See the class docstring"
        return "status" not in self._node.props or \
            self._node.props["status"].to_string() != "disabled"

    @property
    def parent(self):
        "See the class docstring"
        return self.edt._node2dev.get(self._node.parent)

    def __repr__(self):
        return "<Device {}, {} regs>".format(
            self.name, len(self.regs))

    def __init__(self, edt, node, matching_compat):
        "Private constructor. Not meant to be called by clients."

        self.edt = edt
        self.matching_compat = matching_compat
        self.binding = _load_binding(edt._compat2binding[matching_compat])
        self._node = node

        self._create_regs()
        self._set_instance_no()

    def _create_regs(self):
        # Initializes self.regs with a list of Register instances

        node = self._node

        self.regs = []

        if "reg" not in node.props:
            return

        address_cells = _address_cells(node)
        size_cells = _size_cells(node)

        for raw_reg in _slice(node, "reg", 4*(address_cells + size_cells)):
            reg = Register()
            reg.dev = self
            reg.addr = _translate(to_num(raw_reg[:4*address_cells]), node)
            if size_cells != 0:
                reg.size = to_num(raw_reg[4*address_cells:])
            else:
                reg.size = None

            self.regs.append(reg)

        if "reg-names" in node.props:
            reg_names = node.props["reg-names"].to_strings()
            if len(reg_names) != len(self.regs):
                raise EDTError(
                    "'reg-names' property in {} has {} strings, but there are "
                    "{} registers".format(node.name, len(reg_names),
                                          len(self.regs)))

            for reg, name in zip(self.regs, reg_names):
                reg.name = name
        else:
            for reg in self.regs:
                reg.name = None

    def _set_instance_no(self):
        # Initializes self.instance_no

        self.instance_no = 0
        for other_dev in self.edt.devices.values():
            if other_dev.matching_compat == self.matching_compat and \
                other_dev.enabled:

                self.instance_no += 1


class Register:
    """
    Represents a register on a device.

    These attributes are available on Register instances:

    dev:
      The Device instance this register is from.

    name:
      The name of the register as given in the 'reg-names' property, or None if
      there is no 'reg-names' property.

    addr:
      The starting address of the register, in the parent address space. Any
      'ranges' properties are taken into account.

    size:
      The length of the register in bytes.
    """
    def __repr__(self):
        fields = []

        if self.name is not None:
            fields.append("name: " + self.name)
        fields.append("addr: " + hex(self.addr))

        if self.size:
            fields.append("size: " + hex(self.size))

        return "<Register, {}>".format(", ".join(fields))


class EDTError(Exception):
    "Exception raised for Extended Device Tree-related errors"


#
# Private global functions
#


def _yaml_inc_error(msg):
    # Helper for reporting errors in the !include implementation

    raise yaml.constructor.ConstructorError(None, None, msg)


def _load_binding(path):
    # Loads a top-level binding .yaml file from 'path', also handling any
    # !include'd files. Returns the parsed PyYAML output (Python
    # lists/dictionaries/strings/etc. representing the file).

    with open(path, encoding="utf-8") as f:
        # TODO: Nicer way to deal with this?
        return _merge_binding(yaml.load(f, Loader=yaml.Loader))


def _merge_binding(yaml_top):
    # Recursively merges in bindings from from the 'inherits:' section of the
    # binding. !include's have already been processed at this point, and leave
    # the data for the !include'd file(s) in the 'inherits:' section.

    _check_expected_props(yaml_top)

    if 'inherits' in yaml_top:
        for inherited in yaml_top.pop('inherits'):
            inherited = _merge_binding(inherited)
            _merge_props(inherited, yaml_top)
            yaml_top = inherited

    return yaml_top


def _check_expected_props(yaml_top):
    # Checks that the top-level YAML node 'node' has the expected properties.
    # Prints warnings and substitutes defaults otherwise.

    for prop in "title", "version", "description":
        if prop not in yaml_top:
            _warn("binding lacks '{}' property: {}".format(prop, node))


def _merge_props(to_dict, from_dict):
    # Recursively merges 'from_dict' into 'to_dict', to implement !include

    for key in from_dict:
        if isinstance(from_dict[key], dict) and \
           isinstance(to_dict.get(key), dict):
            _merge_props(to_dict[key], from_dict[key])
        else:
            # TODO: Add back previously broken override check?
            to_dict[key] = from_dict[key]


def _translate(addr, node):
    # Recursively translates 'addr' on 'node' to the address space(s) of its
    # parent(s), by looking at 'ranges' properties. Returns the translated
    # address.

    if "ranges" not in node.parent.props:
        # No translation
        return addr

    # Gives the size of each component in a translation 3-tuple in 'ranges'
    child_address_cells = _address_cells(node)
    parent_address_cells = _address_cells(node.parent)
    child_size_cells = _size_cells(node)

    # Number of cells for one translation 3-tuple in 'ranges'
    entry_cells = child_address_cells + parent_address_cells + child_size_cells

    for raw_range in _slice(node.parent, "ranges", 4*entry_cells):
        child_addr = to_num(raw_range[:4*child_address_cells])
        child_len = to_num(
            raw_range[4*(child_address_cells + parent_address_cells):])

        if child_addr <= addr <= child_addr + child_len:
            # 'addr' is within range of a translation in 'ranges'. Recursively
            # translate it and return the result.
            parent_addr = to_num(
                raw_range[4*child_address_cells:
                          4*(child_address_cells + parent_address_cells)])
            return _translate(parent_addr + addr - child_addr, node.parent)

    # 'addr' is not within range of any translation in 'ranges'
    return addr


def _address_cells(node):
    # Returns the #address-cells setting for 'node', giving the number of <u32>
    # cells used to encode the address in the 'reg' property

    if "#address-cells" in node.parent.props:
        return node.parent.props["#address-cells"].to_num()
    return 2  # Default value per DT spec.


def _size_cells(node):
    # Returns the #size-cells setting for 'node', giving the number of <u32>
    # cells used to encode the size in the 'reg' property

    if "#size-cells" in node.parent.props:
        return node.parent.props["#size-cells"].to_num()
    return 1  # Default value per DT spec.


def _slice(node, prop_name, size):
    # Splits node.props[prop_name].value into 'size'-sized chunks, returning a
    # list of chunks. Raises EDTError if the length of the property is not
    # evenly divisible by 'size'.

    raw = node.props[prop_name].value
    if len(raw) % size:
        raise EDTError(
            "'{}' property in {} has length {}, which is not evenly divisible "
            "by {}".format(prop_name, len(raw), size))

    return [raw[i:i + size] for i in range(0, len(raw), size)]


def _warn(msg):
    print("warning: " + msg, file=sys.stderr)
