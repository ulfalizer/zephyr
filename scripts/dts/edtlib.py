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

from dtlib import DT, to_num, to_nums


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
        self._create_compat2binding(bindings_dir)

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

    def _create_compat2binding(self, bindings_dir):
        # Creates self._compat2binding, which maps each compat that's
        # implemented by some binding to the path to the binding

        self._compat2binding = {}

        self._find_bindings(bindings_dir)
        for binding_path in self._bindings:
            compat = _binding_compat(binding_path)
            if compat:
                self._compat2binding[compat] = binding_path

    def _find_bindings(self, bindings_dir):
        # Creates a list with paths to all binding files, in self._bindings

        self._bindings = []

        for root, _, filenames in os.walk(bindings_dir):
            for filename in fnmatch.filter(filenames, "*.yaml"):
                self._bindings.append(os.path.join(root, filename))

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
        # Creates a list of devices (Device instances) from the DT nodes, in
        # self.devices. 'dt' is the dtlib.DT instance for the device tree.

        self.devices = []

        # TODO: Remove the sorting later? It's there to make it easy to compare
        # output against extract_dts_include.py.
        for node in sorted(dt.node_iter(), key=lambda node: node.name):
            dev = Device(self, node)
            self.devices.append(dev)
            self._node2dev[node] = dev

    def _parse_chosen(self, dt):
        # Extracts information from the device tree's /chosen node. 'dt' is the
        # dtlib.DT instance for the device tree.

        self.sram_dev = self._chosen_dev(dt, "zephyr,sram")
        self.ccm_dev = self._chosen_dev(dt, "zephyr,ccm")

    def _chosen_dev(self, dt, prop_name):
        # _parse_chosen() helper. Returns the device pointed to by prop_name in
        # /chosen in 'dt', or None if /chosen has no property named prop_name.

        if not dt.has_node("/chosen"):
            return None

        chosen = dt.get_node("/chosen")

        if prop_name in chosen.props:
            # Value is the path of a node that represents the memory device
            path = chosen.props[prop_name].to_string()
            if not dt.has_node(path):
                raise EDTError(
                    "{} points to {}, which does not exist"
                    .format(prop_name, path))

            return self._node2dev[dt.get_node(path)]

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

    unit_addr:
      An integer with the ...@<unit-address> portion of the node name,
      translated through any 'ranges' properties on parent nodes, or None if
      the node name has no unit-address portion

    aliases:
      A list of aliases for the device. This is fetched from the /aliases node.

    compats:
      A list of 'compatible' strings for the device

    binding:
      The data from the device's binding file, in the format returned by PyYAML
      (plain Python lists, dicts, etc.), or None if the device has no binding.

    regs:
      A list of Register instances for the device's registers

    interrupts:
      TODO: now is [(<controller>, <interrupts>), ...]

    gpios:
      TODO

    bus:
      The bus the device is on, e.g. "i2c" or "spi", as a string, or None if
      non-applicable

    enabled:
      True unless the device's node has 'status = "disabled"'

    matching_compat:
      The 'compatible' string for the binding that matched the device, or
      None if the device has no binding

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
    def unit_addr(self):
        "See the class docstring"
        if "@" not in self.name:
            return None

        # TODO: Return the untranslated address here?
        # TODO: A non-numeric @<unit-address> seems to be allowed

        try:
            addr = int(self.name.split("@", 1)[1], 16)
        except ValueError:
            raise EDTError(self.name + " has non-hex unit address")

        addr = _translate(addr, self._node)

        # TODO: Is this a good spot to check it? Could be moved to some
        # checking code later maybe...
        if self.regs and self.regs[0].addr != addr:
            _warn("unit-address and first reg (0x{:x}) don't match for {}"
                  .format(self.regs[0].addr, self.name))

        return addr

    @property
    def aliases(self):
        "See the class docstring"
        return [alias for alias, node in self._node.dt.alias2node.items()
                if node is self._node]

    @property
    def interrupts(self):
        "See the class docstring"
        # Later, maybe compute this once
        return [(self.edt._node2dev[node], to_nums(specifier))
                for node, specifier in _interrupts(self._node)]

    @property
    def gpios(self):
        "See the class docstring"

        res = {}

        for prefix, gpios in _gpios(self._node).items():
            res[prefix] = [(self.edt._node2dev[controller], to_nums(data))
                           for controller, data in gpios]

        return res

    @property
    def bus(self):
        "See the class docstring"
        if self.binding and "parent" in self.binding:
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

    def __init__(self, edt, node):
        "Private constructor. Not meant to be called by clients."

        self.edt = edt
        self._node = node
        self._init_binding()
        self._create_regs()
        self._set_instance_no()

    def _init_binding(self):
        # Initializes Device.matching_compat and Device.binding

        if "compatible" not in self._node.props:
            self.compats = []
            self.matching_compat = self.binding = None
            return

        self.compats = self._node.props["compatible"].to_strings()

        for compat in self.compats:
            binding = self.edt._compat2binding.get(compat)
            if binding:
                self.matching_compat = compat
                self.binding = _load_binding(binding)
                return

        self.matching_compat = self.binding = None

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


def _binding_compat(binding_path):
    # TODO: document

    with open(binding_path) as binding:
        for line in binding:
            match = re.match(r'\s+constraint:\s*"([^"]*)"', line)
            if match:
                return match.group(1)

    return None


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

    # Allow the category to be changed from 'optional' to 'required'
    # without a warning
    #
    # TODO: The category is never checked otherwise, and wasn't in
    # extract_dts_includes.py either
    if prop == "category" and \
       to_dict["category"] == "optional" and from_dict["category"] == "required":
        return False

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


def _interrupt_parent(node):
    # TODO: Update documentation
    # TODO: Fixup error handling if we don't find a match

    if "interrupt-parent" in node.props:
        return node.props["interrupt-parent"].to_node()
    return _interrupt_parent(node.parent)


def _address_cells(node):
    # Returns the #address-cells setting for 'node', giving the number of <u32>
    # cells used to encode the address in the 'reg' property

    if "#address-cells" in node.parent.props:
        return node.parent.props["#address-cells"].to_num()
    return 2  # Default value per DT spec.


def _interrupts(node):
    # TODO: document

    # Takes precedence over 'interrupts' if both are present
    if "interrupts-extended" in node.props:
        prop = node.props["interrupts-extended"]
        raw = prop.value

        res = []

        while raw:
            if len(raw) < 4:
                # Not enough room for phandle
                raise EDTError("bad value for " + repr(prop))
            phandle = to_num(raw[:4])
            raw = raw[4:]

            # Could also be a nexus (with interrupt-map = ...)
            iparent = node.dt.phandle2node.get(phandle)
            if not iparent:
                raise EDTError("bad phandle in " + repr(prop))

            interrupt_cells = _interrupt_cells(iparent)
            if len(raw) < 4*interrupt_cells:
                raise EDTError("missing data after phandle in " + repr(prop))

            res.append(_map_interrupt(node, iparent, raw[:4*interrupt_cells]))
            raw = raw[4*interrupt_cells:]

        return res

    if "interrupts" in node.props:
        # Treat 'interrupts' as a special case of 'interrupts-extended' with
        # the same interrupt parent for all interrupts

        iparent = _interrupt_parent(node)
        interrupt_cells = _interrupt_cells(iparent)

        return [_map_interrupt(node, iparent, raw)
                for raw in _slice(node, "interrupts", 4*interrupt_cells)]

    return []


def _map_interrupt(child, parent, child_spec):
    # TODO: document

    if "interrupt-controller" in parent.props:
        return (parent, child_spec)

    def spec_len_fn(node):
        return 4*(_address_cells(node) + _interrupt_cells(node))

    parent, raw_spec = _map(
        "interrupt", child, parent, _raw_unit_addr(child) + child_spec,
        spec_len_fn)

    # Strip the parent unit address part, if any
    return (parent, raw_spec[4*_address_cells(parent):])


def _gpios(node):
    # TODO: document

    res = {}

    for name, prop in node.props.items():
        if name.endswith("gpios"):
            # Get the prefix from the property name:
            #   - gpios     -> "" (deprecated, should have a prefix)
            #   - foo-gpios -> "foo"
            #   - etc.
            prefix = name[:-5]
            if prefix.endswith("-"):
                prefix = prefix[:-1]

            res[prefix] = _gpios_from_prop(prop)

    return res


def _gpios_from_prop(prop):
    # gpios() helper. Returns a list of (<controller>, <data>) GPIO
    # specifications parsed from 'prop'.

    raw = prop.value
    res = []

    while raw:
        if len(raw) < 4:
            # Not enough room for phandle
            raise EDTError("bad value for " + repr(prop))
        phandle = to_num(raw[:4])
        raw = raw[4:]

        controller = prop.node.dt.phandle2node.get(phandle)
        if not controller:
            raise EDTError("bad phandle in " + repr(prop))

        gpio_cells = _gpio_cells(controller)
        if len(raw) < 4*gpio_cells:
            raise EDTError("missing data after phandle in " + repr(prop))

        res.append(_map_gpio(prop.node, controller, raw[:4*gpio_cells]))
        raw = raw[4*gpio_cells:]

    return res


def _map_gpio(child, parent, child_spec):
    # TODO: document

    if "gpio-map" not in parent.props:
        return (parent, child_spec)

    return _map("gpio", child, parent, child_spec,
                lambda node: 4*_gpio_cells(node))


def _map(prefix, child, parent, child_spec, spec_len_fn):
    # TODO: document

    map_prop = parent.props.get(prefix + "-map")
    if not map_prop:
        # No mapping
        return (parent, child_spec)

    masked_child_spec = _mask(prefix, child, parent, child_spec)

    raw = map_prop.value
    while raw:
        if len(raw) < len(child_spec):
            raise EDTError("bad value for {!r}, missing/truncated child "
                           "specifier".format(map_prop))
        child_spec_entry = raw[:len(child_spec)]
        raw = raw[len(child_spec_entry):]

        if len(raw) < 4:
            raise EDTError("bad value for {!r}, missing/truncated phandle"
                           .format(map_prop))
        phandle = to_num(raw[:4])
        raw = raw[4:]

        # Parent specified in *-map
        map_parent = parent.dt.phandle2node.get(phandle)
        if not map_parent:
            raise EDTError("bad phandle in " + repr(map_prop))

        map_parent_spec_len = spec_len_fn(map_parent)
        if len(raw) < map_parent_spec_len:
            raise EDTError("bad value for {!r}, missing/truncated parent "
                           "specifier".format(map_prop))
        parent_spec = raw[:map_parent_spec_len]
        raw = raw[map_parent_spec_len:]

        # Got one *-map row. Check if it matches the child specifier.
        if child_spec_entry == masked_child_spec:
            # Handle *-map-pass-thru
            parent_spec = _pass_thru(
                prefix, child, parent, child_spec, parent_spec)

            # Found match. Recursively map and return it.
            return _map(prefix, parent, map_parent, parent_spec, spec_len_fn)

    # TODO: Is raising an error the right thing to do here?
    raise EDTError("child specifier for {!r} ({}) does not appear in {!r}"
                   .format(child, child_spec, map_prop))


def _mask(prefix, child, parent, child_spec):
    # TODO: document

    mask_prop = parent.props.get(prefix + "-map-mask")
    if not mask_prop:
        # No mask
        return child_spec

    mask = mask_prop.value
    if len(mask) != len(child_spec):
        raise EDTError("{!r}: expected '{}-mask' in {!r} to be {} bytes, is "
                       "{} bytes".format(
                           child, prefix, parent, len(child_spec), len(mask)))

    return _and(child_spec, mask)


def _pass_thru(prefix, child, parent, child_spec, parent_spec):
    # TODO: document

    pass_thru_prop = parent.props.get(prefix + "-map-pass-thru")
    if not pass_thru_prop:
        # No pass-thru
        return parent_spec

    pass_thru = pass_thru_prop.value
    if len(pass_thru) != len(child_spec):
        raise EDTError("{!r}: expected '{}-map-pass-thru' in {!r} to be {} "
                       "bytes, is {} bytes".format(
                           child, prefix, len(child_spec), len(pass_thru)))

    return _or(_and(parent_spec, _not(pass_thru)),
               _and(child_spec, pass_thru))


def _raw_unit_addr(node):
    # TODO: document

    # TODO: get this from the 'reg' property instead, since they're required to
    # match?

    if not node.unit_addr:
        return b""

    try:
        child_unit_addr = int(node.unit_addr, 16)
    except ValueError:
        raise EDTError(repr(node) + " has non-numeric unit address")

    return child_unit_addr.to_bytes(4*_address_cells(node), "big")


def _and(b1, b2):
    # Returns the bitwise AND of the two 'bytes' objects b1 and b2. Pads
    # with ones on the left if the lengths are not equal.

    # Pad on the left, to equal length
    maxlen = max(len(b1), len(b2))
    b1 = b1.rjust(maxlen, b'\xff')
    b2 = b2.rjust(maxlen, b'\xff')

    return bytes(x & y for x, y in zip(b1, b2))


def _or(b1, b2):
    # Returns the bitwise OR of the two 'bytes' objects b1 and b2. Pads with
    # zeros on the left if the lengths are not equal.

    # Pad on the left, to equal length
    maxlen = max(len(b1), len(b2))
    b1 = b1.rjust(maxlen, b'\x00')
    b2 = b2.rjust(maxlen, b'\x00')

    return bytes(x | y for x, y in zip(b1, b2))


def _not(b):
    # Returns the bitwise not of the 'bytes' object 'b'

    # ANDing with 0xFF avoids negative numbers
    return bytes(~x & 0xFF for x in b)


def _interrupt_cells(node):
    # Returns the #interrupt-cells property value on 'node', erroring out if
    # 'node' has no #interrupt-cells property

    if "#interrupt-cells" not in node.props:
        raise EDTError("{} lacks #interrupt-cells".format(node.path))
    return node.props["#interrupt-cells"].to_num()


def _gpio_cells(node):
    # TODO: have a _required_prop(node, "blah") or similar?

    if "#gpio-cells" not in node.props:
        raise EDTError("{!r} lacks #gpio-cells".format(node))
    return node.props["#gpio-cells"].to_num()


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

# TODO: replace node.path, etc., with repr's, which give more information
# TODO: check if interrupt-controller exists on domain root?
# TODO: does e.g. gpio-controller need to exist as well?

# Unimplemented features:
#   virtual-reg (unused)
#   dma-ranges (unused)
#   name (deprecated)
#   device_type (deprecated)

# Nexus node properties:
#   #<specifier>-cells
#   #<specifier>-map
#   #<specifier>-map-mask
#   #<specifier>-map-pass-thru
#
# Questions:
#   - Is there even anything like a gpio-controller spec-wise?
#
# Spec stuff:
#   Bad grammar:
#     If a CPU/thread can be the target of an exter-
#     nal interrupt the reg property value must be a
#     unique CPU/thread id that is addressable by
#     the interrupt controller
#
#   "child specifier domian"
#
#  Translation look-aside buffer?
#    The following properties of a cpu node describe the translate look-aside
#    buffer in the processor’s MMU.
