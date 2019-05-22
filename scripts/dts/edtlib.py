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
      A list of Device instances for the devices.

    sram_dev:
      The Device instance for the device chosen by the 'zephyr,sram' property
      on the /chosen node, or None if missing

    ccm_dev:
      The Device instance for the device chosen by the 'zephyr,ccm' property on
      the /chosen node, or None if missing
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

        dt = DT(dts)
        self._create_devices(dt)
        self._parse_chosen(dt)

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

    def _create_devices(self, dt):
        # Creates self.devices, which maps device names to Device instances.
        # Currently, a device is defined as a node whose 'compatible' property
        # contains a compat string covered by some binding. 'dt' is the
        # dtlib.DT instance for the device tree.

        self.devices = []

        # TODO: Remove the sorting later? It's there to make it easy to compare
        # output against extract_dts_include.py.
        for node in sorted(dt.node_iter(), key=lambda node: node.name):
            if "compatible" in node.props:
                for compat in node.props["compatible"].to_strings():
                    if compat in self._compat2binding:
                        self._create_device(node, compat)
                        break

    def _create_device(self, node, matching_compat):
        # Creates and registers a Device for 'node', which was matched to a
        # binding via the 'compatible' string 'matching_compat'

        dev = Device(self, node, matching_compat)
        self.devices.append(dev)
        self._node2dev[node] = dev

    def _parse_chosen(self, dt):
        # Extracts information from the device tree's /chosen node. 'dt' is the
        # dtlib.DT instance for the device tree.

        self.sram_dev = None
        self.ccm_dev = None

        if not dt.has_node("/chosen"):
            return

        chosen = dt.get_node("/chosen")

        # TODO: Get rid of some code duplication below?

        if "zephyr,sram" in chosen.props:
            # Value is the path of a node that represents the memory device
            path = chosen.props["zephyr,sram"].to_string()
            if not dt.has_node(path):
                raise EDTError(
                    "'zephyr,sram' points to '{}', which does not exist"
                    .format(path))

            node = dt.get_node(path)
            if node not in self._node2dev:
                raise EDTError(
                    "'zephyr,sram' points to '{}', which lacks a binding"
                    .format(path))

            self.sram_dev = self._node2dev[node]

        if "zephyr,ccm" in chosen.props:
            # Value is the path of a node that represents the CCM (Core Coupled
            # Memory) device
            path = chosen.props["zephyr,ccm"].to_string()
            if not dt.has_node(path):
                raise EDTError(
                    "'zephyr,ccm' points to '{}', which does not exist"
                    .format(path))

            node = dt.get_node(path)
            if node not in self._node2dev:
                raise EDTError(
                    "'zephyr,ccm' points to '{}', which lacks a binding"
                    .format(path))

            self.ccm_dev = self._node2dev[node]

    def __repr__(self):
        return "<EDT, {} devices>".format(len(self.devices))


class Device:
    """
    Represents a device.

    These attributes are available on Device instances:

    edt:
      The EDT instance this device is from

    name:
      The name of the device. This is fetched from the node name.

    aliases:
      A list of aliases for the device. This is fetched from the /aliases node.

    compats:
      A list of 'compatible' strings for the device

    binding:
      The data from the device's binding file, in the format returned by PyYAML
      (plain Python lists, dicts, etc.)

    regs:
      A list of Register instances for the device's registers

    bus:
      The bus the device is on, e.g. "i2c" or "spi", as a string, or None if
      non-applicable

    enabled:
      True unless the device's node has 'status = "disabled"'

    matching_compat:
      The 'compatible' string for the binding that matched the device

    instance_no:
      Dictionary that maps each 'compatible' string for the device to a unique
      index among all devices that have that 'compatible' string.

      As an example, 'instance_no["foo,led"] == 3' can be read as "this is the
      fourth foo,led device".

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
    def aliases(self):
        "See the class docstring"
        return [alias for alias, node in self._node.dt.alias_to_node.items()
                if node is self._node]

    @property
    def bus(self):
        "See the class docstring"
        if "parent" in self.binding:
            return self.binding["parent"].get("bus")
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

        self.compats = node.props["compatible"].to_strings()
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

        self.instance_no = {}

        for compat in self.compats:
            self.instance_no[compat] = 0
            for other_dev in self.edt.devices:
                if compat in other_dev.compats and other_dev.enabled:
                    self.instance_no[compat] += 1


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
        return _merge_binding(path, yaml.load(f, Loader=yaml.Loader))


def _merge_binding(binding_path, yaml_top):
    # Recursively merges yaml_top into the bindings in the 'inherits:' section
    # of the binding. !include's have already been processed at this point, and
    # leave the data for the !include'd file(s) in the 'inherits:' section.
    #
    # Properties from the !include'ing file override properties from the
    # !include'd file, which is why this logic might seem "backwards".

    _check_expected_props(binding_path, yaml_top)

    if 'inherits' in yaml_top:
        for inherited in yaml_top.pop('inherits'):
            inherited = _merge_binding(binding_path, inherited)
            _merge_props(binding_path, None, inherited, yaml_top)
            yaml_top = inherited

    return yaml_top


def _check_expected_props(binding_path, yaml_top):
    # Checks that the top-level YAML node 'node' has the expected properties.
    # Prints warnings and substitutes defaults otherwise.

    for prop in "title", "version", "description":
        if prop not in yaml_top:
            _warn("'{}' lacks '{}' property: {}".format(
                binding_path, prop, node))


def _merge_props(binding_path, parent_prop, to_dict, from_dict):
    # Recursively merges 'from_dict' into 'to_dict', to implement !include.
    #
    # binding_path is the path of the top-level binding, and parent_prop the
    # name of the dictionary containing 'prop'. These are used to generate
    # warnings for sketchy property overwrites.

    for prop in from_dict:
        if isinstance(from_dict[prop], dict) and \
           isinstance(to_dict.get(prop), dict):
            _merge_props(binding_path, prop, to_dict[prop], from_dict[prop])
        else:
            if _bad_overwrite(to_dict, from_dict, prop):
                _warn("{} (in '{}'): '{}' from !include'd file overwritten "
                      "('{}' replaced with '{}')".format(
                          binding_path, parent_prop, prop, from_dict[prop],
                          to_dict[prop]))

            to_dict[prop] = from_dict[prop]


def _bad_overwrite(to_dict, from_dict, prop):
    # _merge_props() helper. Returns True if overwriting to_dict[prop] with
    # from_dict[prop] seems bad. parent_prop is the name of the dictionary
    # containing 'prop'.

    if prop not in to_dict or to_dict[prop] == from_dict[prop]:
        return False

    # These are overriden deliberately
    if prop in {"title", "version", "description"}:
        return False

    # TODO: There's an old check for changing the category here. Add it back
    # later if it makes sense.

    return True


def _translate(addr, node):
    # Recursively translates 'addr' on 'node' to the address space(s) of its
    # parent(s), by looking at 'ranges' properties. Returns the translated
    # address.

    if "ranges" not in node.parent.props:
        # No translation
        return addr

    if not node.parent.props["ranges"].value:
        # DT spec.: "If the property is defined with an <empty> value, it
        # specifies that the parent and child address space is identical, and
        # no address translation is required."
        #
        # Treat this the same as a 'range' that explicitly does a one-to-one
        # mapping, as opposed to there not being any translation.
        return _translate(addr, node.parent)

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
