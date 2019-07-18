# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
Helper library for working with .dts files at a higher level compared to dtlib.
Deals with devices, registers, bindings, etc.
"""
import os
import re
import sys

import yaml

from dtlib import DT, DTError, to_num, to_nums

#
# Public classes
#


class EDT:
    """
    Represents a "high-level" view of a device tree, with a list of devices
    that each have some number of registers, etc.

    These attributes are available on EDT objects:

    devices:
      A list of Device objects for the devices

    dts_path:
      The .dts path passed to __init__()

    bindings_dir:
      The bindings directory path passed to __init__()
    """
    def __init__(self, dts, bindings_dir):
        """
        EDT constructor. The top-level entry point to the library.

        dts:
          Path to device tree .dts file

        bindings_dir:
          Path to directory containing bindings, in YAML format. This directory
          is recursively searched for .yaml files.
        """
        self.dts_path = dts
        self.bindings_dir = bindings_dir

        self._dt = DT(dts)

        self._init_compat2binding(bindings_dir)
        self._init_devices()

    def get_dev(self, path):
        """
        Returns the Device at the DT path or alias 'path'. Raises EDTError if
        the path or alias doesn't exist.
        """
        try:
            return self._node2dev[self._dt.get_node(path)]
        except DTError as e:
            _err(e)

    def chosen_dev(self, name):
        """
        Returns the Device pointed at by the property named 'name' in /chosen,
        or None if the property is missing
        """
        try:
            chosen = self._dt.get_node("/chosen")
        except DTError:
            # No /chosen node
            return None

        if name not in chosen.props:
            return None

        path = chosen.props[name].to_string()
        try:
            return self._node2dev[self._dt.get_node(path)]
        except DTError:
            _err("{} in /chosen points to {}, which does not exist"
                 .format(name, path))

    def _init_compat2binding(self, bindings_dir):
        # Creates self._compat2binding. This is a dictionary that maps
        # (<compatible>, <bus>) tuples (both strings) to (<binding>, <path>)
        # tuples. <binding> is the binding in parsed PyYAML format, and <path>
        # the path to the binding (nice for binding-related error messages).
        #
        # For example, self._compat2binding["company,dev", "can"] contains the
        # binding/path for the 'company,dev' device, when it appears on the CAN
        # bus.
        #
        # For bindings that don't specify a bus, <bus> is None, so that e.g.
        # self._compat2binding["company,notonbus", None] contains the binding.
        #
        # Only bindings for compatible strings that appear in the device tree
        # are loaded.

        dt_compats = _dt_compats(self._dt)
        self._binding_paths = _binding_paths(bindings_dir)

        # Add '!include foo.yaml' handling.
        #
        # Do yaml.Loader.add_constructor() instead of yaml.add_constructor() to be
        # compatible with both version 3.13 and version 5.1 of PyYAML.
        #
        # TODO: Is there some 3.13/5.1-compatible way to only do this once, even
        # if multiple EDT objects are created?
        yaml.Loader.add_constructor("!include", self._binding_include)

        self._compat2binding = {}
        for binding_path in self._binding_paths:
            compat = _binding_compat(binding_path)
            if compat in dt_compats:
                binding = _load_binding(binding_path)
                self._compat2binding[compat, _binding_bus(binding)] = \
                    (binding, binding_path)

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

        _yaml_inc_error("unrecognised node type in !include statement")

    def _binding_include_file(self, filename):
        # _binding_include() helper for loading an !include'd file. !include
        # takes just the basename of the file, so we need to make sure there
        # aren't multiple candidates.

        paths = [path for path in self._binding_paths
                 if os.path.basename(path) == filename]

        if not paths:
            _yaml_inc_error("'{}' not found".format(filename))

        if len(paths) > 1:
            _yaml_inc_error("multiple candidates for '{}' in !include: {}"
                            .format(filename, ", ".join(paths)))

        with open(paths[0], encoding="utf-8") as f:
            return yaml.load(f, Loader=yaml.Loader)

    def _init_devices(self):
        # Creates a list of devices (Device objects) from the DT nodes, in
        # self.devices. 'dt' is the dtlib.DT instance for the device tree.

        # Maps dtlib.Node's to their corresponding Devices
        self._node2dev = {}

        self.devices = []

        for node in self._dt.node_iter():
            # Warning: Device.__init__() relies on parent Devices being created
            # before their children
            dev = Device(self, node)
            self.devices.append(dev)
            self._node2dev[node] = dev

        for dev in self.devices:
            # These depend on all Device objects having been created, so we do
            # them separately
            dev._init_interrupts()
            dev._init_gpios()
            dev._init_pwms()
            dev._init_clocks()

        # TODO: Remove this sorting later? It's there to make it easy to
        # compare output against extract_dts_include.py.
        self.devices.sort(key=lambda dev: dev.name)

    def __repr__(self):
        return "<EDT for '{}', binding directory '{}'>".format(
            self.dts_path, self.bindings_dir)


class Device:
    """
    Represents a device.

    These attributes are available on Device objects:

    edt:
      The EDT instance this device is from

    name:
      The name of the device. This is fetched from the node name.

    unit_addr:
      An integer with the ...@<unit-address> portion of the node name,
      translated through any 'ranges' properties on parent nodes, or None if
      the node name has no unit-address portion

    path:
      The device tree path of the device

    label:
      The text from the 'label' property on the DT node of the Device, or None
      if the node has no 'label'

    parent:
      The parent Device, or None if there is no parent

    enabled:
      True unless the device's node has 'status = "disabled"'

    read_only:
      True if the DT node of the Device has a 'read-only' property, and False
      otherwise

    instance_no:
      Dictionary that maps each 'compatible' string for the device to a unique
      index among all devices that have that 'compatible' string.

      As an example, 'instance_no["foo,led"] == 3' can be read as "this is the
      fourth foo,led device".

      Only enabled devices (status != "disabled") are counted. 'instance_no' is
      meaningless for disabled devices.

    matching_compat:
      The 'compatible' string for the binding that matched the device, or
      None if the device has no binding

    binding_path:
      The path to to the device's binding file, or None if the device has no
      binding

    compats:
      A list of 'compatible' strings for the device

    regs:
      A list of Register objects for the device's registers

    props:
      A dictionary that maps property names to Property objects. Property
      objects are created for all DT properties on the device that are
      mentioned in 'properties:' in the binding.

    aliases:
      A list of aliases for the device. This is fetched from the /aliases node.

    interrupts:
      A list of Interrupt objects for the interrupts generated by the device

    gpios:
      A dictionary that maps the <prefix> part in '<prefix>-gpios' properties
      to a list of GPIO objects (see the GPIO class).

      For example, 'foo-gpios = <&gpio1 1 2 &gpio2 3 4>' makes gpios["foo"] a
      list of two GPIO objects.

    pwms:
      A list of PWM objects, derived from the 'pwms' property. The list is
      empty if the device has no 'pwms' property.

    clocks:
      A list of Clock objects, derived from the 'clocks' property. The list is
      empty if the device has no 'clocks' property.

    bus:
      The bus for the device as specified in its binding, e.g. "i2c" or "spi".
      None if the binding doesn't specify a bus.

    flash_controller:
      The flash controller for the device. Only meaningful for devices
      representing flash partitions.
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
            _err("{!r} has non-hex unit address".format(self))

        addr = _translate(addr, self._node)

        # TODO: Is this a good spot to check it? Could be moved to some
        # checking code later maybe...
        if self.regs and self.regs[0].addr != addr:
            _warn("unit-address and first reg (0x{:x}) don't match for {}"
                  .format(self.regs[0].addr, self.name))

        return addr

    @property
    def path(self):
        "See the class docstring"
        return self._node.path

    @property
    def label(self):
        "See the class docstring"
        if "label" in self._node.props:
            return self._node.props["label"].to_string()
        return None

    @property
    def parent(self):
        "See the class docstring"
        return self.edt._node2dev.get(self._node.parent)

    @property
    def enabled(self):
        "See the class docstring"
        return "status" not in self._node.props or \
            self._node.props["status"].to_string() != "disabled"

    @property
    def read_only(self):
        "See the class docstring"
        return "read-only" in self._node.props

    @property
    def aliases(self):
        "See the class docstring"
        return [alias for alias, node in self._node.dt.alias2node.items()
                if node is self._node]

    @property
    def bus(self):
        "See the class docstring"
        return _binding_bus(self._binding)

    @property
    def flash_controller(self):
        "See the class docstring"

        # The node path might be something like
        # /flash-controller@4001E000/flash@0/partitions/partition@fc000. We go
        # up two levels to get the flash and check its compat. The flash
        # controller might be the flash itself (for cases like NOR flashes).
        # For the case of 'soc-nv-flash', we assume the controller is the
        # parent of the flash node.

        if not self.parent or not self.parent.parent:
            _err("flash partition {!r} lacks parent or grandparent node"
                 .format(self))

        controller = self.parent.parent
        if controller.matching_compat == "soc-nv-flash":
            return controller.parent
        return controller

    def __repr__(self):
        return "<Device {} in '{}', {}>".format(
            self.path, self.edt.dts_path,
            "binding " + self.binding_path if self.binding_path
                else "no binding")

    def __init__(self, edt, node):
        "Private constructor. Not meant to be called by clients."

        # Interrupts and GPIOs are initialized separately, because
        # they depend on all Devices existing

        self.edt = edt
        self._node = node

        self._init_binding()
        self._init_props()
        self._init_regs()
        self._set_instance_no()

    def _init_binding(self):
        # Initializes Device.matching_compat, Device._binding, and
        # Device.binding_path.
        #
        # Device._binding holds the data from the device's binding file, in the
        # format returned by PyYAML (plain Python lists, dicts, etc.), or None
        # if the device has no binding.

        # This relies on the parent of the Device having already been
        # initialized, which is guaranteed by going through the nodes in
        # node_iter() order.

        if "compatible" in self._node.props:
            self.compats = self._node.props["compatible"].to_strings()
            bus = self._bus_from_parent_binding()

            for compat in self.compats:
                if (compat, bus) in self.edt._compat2binding:
                    # Binding found
                    self.matching_compat = compat
                    self._binding, self.binding_path = \
                        self.edt._compat2binding[compat, bus]
                    return
        else:
            # No 'compatible' property. See if the parent has a 'sub-node:' key
            # that gives the binding.

            self.compats = []

            if self.parent and self.parent._binding and \
                "sub-node" in self.parent._binding:

                # Binding found
                self._binding = self.parent._binding["sub-node"]
                self.binding_path = self.parent.binding_path
                self.matching_compat = self.parent.matching_compat
                return

        # No binding found
        self.matching_compat = self._binding = self.binding_path = None

    def _bus_from_parent_binding(self):
        # _init_binding() helper. Returns the bus specified by
        # 'child: bus: ...' in the parent binding, or None if missing.

        if not self.parent:
            return None

        binding = self.parent._binding
        if binding and "child" in binding:
            return binding["child"].get("bus")
        return None

    def _init_props(self):
        # Creates self.props. See the class docstring.

        self.props = {}

        if not self._binding or "properties" not in self._binding:
            return

        for name, options in self._binding["properties"].items():
            self._init_prop(name, options)

    def _init_prop(self, name, options):
        # _init_props() helper for initializing a single property

        # Skip properties that start with '#', like '#size-cells', and mapping
        # properties like 'gpio-map'/'interrupt-map'
        if name[0] == "#" or name.endswith("-map"):
            return

        prop_type = options.get("type")
        if not prop_type:
            _err("'{}' in {} lacks 'type'".format(name, self.binding_path))

        val = self._prop_val(name, prop_type,
                             options.get("category") == "optional")
        if val is None:
            # 'category: optional' property that wasn't there
            return

        prop = Property()
        prop.dev = self
        prop.name = name
        prop.val = val
        enum = options.get("enum")
        if enum is None:
            prop.enum_index = None
        else:
            if val not in enum:
                _err("value ({}) for property ({}) is not in enumerated "
                     "list {} for node {}".format(val, name, enum, self.name))

            prop.enum_index = enum.index(val)

        self.props[name] = prop

    def _prop_val(self, name, prop_type, optional):
        # _init_prop() helper for getting the property's value

        node = self._node

        if prop_type == "boolean":
            # True/False
            return name in node.props

        prop = node.props.get(name)
        if not prop:
            if not optional and \
                ("status" not in node.props or
                 node.props["status"].to_string() != "disabled"):

                _warn("'{}' appears in 'properties:' in {}, but not in {!r}"
                      .format(name, self.binding_path, node))

            return None

        if prop_type == "int":
            return prop.to_num()

        if prop_type == "array":
            return prop.to_nums()

        if prop_type == "uint8-array":
            return prop.value  # Plain 'bytes'

        if prop_type == "string":
            return prop.to_string()

        if prop_type == "string-array":
            return prop.to_strings()

        _warn("'{}' in 'properties:' in {} has unknown type '{}'"
              .format(name, self.binding_path, prop_type))

        return None

    def _init_regs(self):
        # Initializes self.regs with a list of Register objects

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

        _add_names(node, "reg-names", self.regs)

    def _init_interrupts(self):
        # Initializes self.interrupts with a list of Interrupt objects

        node = self._node

        self.interrupts = []

        for controller_node, spec in _interrupts(node):
            controller = self.edt._node2dev[controller_node]

            interrupt = Interrupt()
            interrupt.dev = self
            interrupt.controller = controller
            interrupt.specifier = self._named_cells(controller, spec, "interrupt")

            self.interrupts.append(interrupt)

        _add_names(node, "interrupt-names", self.interrupts)

    def _init_gpios(self):
        # Initializes self.gpios

        self.gpios = {}

        for prefix, gpios in _gpios(self._node).items():
            self.gpios[prefix] = []
            for controller_node, spec in gpios:
                controller = self.edt._node2dev[controller_node]

                gpio = GPIO()
                gpio.dev = self
                gpio.controller = controller
                gpio.specifier = self._named_cells(controller, spec, "GPIOS")
                gpio.name = prefix

                self.gpios[prefix].append(gpio)

    def _init_clocks(self):
        # Initializes self.clocks

        node = self._node

        self.clocks = []

        for controller_node, spec in _clocks(node):
            controller = self.edt._node2dev[controller_node]

            clock = Clock()
            clock.dev = self
            clock.controller = controller
            clock.specifier = self._named_cells(controller, spec, "clocks")
            if "fixed-clock" in controller.compats:
                clock.frequency = controller.props["clock-frequency"].val
            else:
                clock.frequency = None

            self.clocks.append(clock)

        # TODO: verify clock name support
        _add_names(node, "clock-names", self.clocks)

    def _init_pwms(self):
        # Initializes self.pwms

        self.pwms = []

        for controller_node, spec in _pwms(self._node):
            controller = self.edt._node2dev[controller_node]

            pwm = PWM()
            pwm.dev = self
            pwm.controller = controller
            pwm.specifier = self._named_cells(controller, spec, "pwms")

            self.pwms.append(pwm)

        _add_names(self._node, "pwm-names", self.pwms)

    def _named_cells(self, controller, spec, controller_s):
        # _init_{interrupts,gpios}() helper. Returns a dictionary that maps
        # #cell names given in the binding for 'controller' to cell values.
        # 'spec' is the raw interrupt/GPIO data, and 'controller_s' a string
        # that gives the context (for error messages).

        if not controller._binding:
            _err("{} controller {!r} for {!r} lacks binding"
                 .format(controller_s, controller._node, self._node))

        if "#cells" in controller._binding:
            cell_names = controller._binding["#cells"]
            if not isinstance(cell_names, list):
                _err("binding for {} controller {!r} has malformed #cells array"
                     .format(controller_s, controller._node))
        else:
            # Treat no #cells in the binding the same as an empty #cells, so
            # that bindings don't have to have an empty #cells for e.g.
            # '#clock-cells = <0>'.
            cell_names = []

        spec_list = to_nums(spec)
        if len(spec_list) != len(cell_names):
            _err("unexpected #cells length in binding for {!r} - {} instead "
                 "of {}".format(controller._node, len(cell_names),
                                len(spec_list)))

        return dict(zip(cell_names, spec_list))

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

    These attributes are available on Register objects:

    dev:
      The Device instance this register is from

    name:
      The name of the register as given in the 'reg-names' property, or None if
      there is no 'reg-names' property

    addr:
      The starting address of the register, in the parent address space. Any
      'ranges' properties are taken into account.

    size:
      The length of the register in bytes
    """
    def __repr__(self):
        fields = []

        if self.name is not None:
            fields.append("name: " + self.name)
        fields.append("addr: " + hex(self.addr))

        if self.size:
            fields.append("size: " + hex(self.size))

        return "<Register, {}>".format(", ".join(fields))


class Interrupt:
    """
    Represents an interrupt generated by a device.

    These attributes are available on Interrupt objects:

    dev:
      The Device instance that generated the interrupt

    name:
      The name of the interrupt as given in the 'interrupt-names' property, or
      None if there is no 'interrupt-names' property

    controller:
      The Device instance for the controller the interrupt gets sent to. Any
      'interrupt-map' is taken into account, so that this is the final
      controller node.

    specifier:
      A dictionary that maps names from the #cells portion of the binding to
      cell values in the interrupt specifier. 'interrupts = <1 2>' might give
      {"irq": 1, "level": 2}, for example.
    """
    def __repr__(self):
        fields = []

        if self.name is not None:
            fields.append("name: " + self.name)

        fields.append("target: {}".format(self.controller))
        fields.append("specifier: {}".format(self.specifier))

        return "<Interrupt, {}>".format(", ".join(fields))


class GPIO:
    """
    Represents a GPIO used by a device.

    These attributes are available on GPIO objects:

    dev:
      The Device instance that uses the GPIO

    name:
      The name of the gpio as extracted out of the "<NAME>-gpios" property. If
      the property is just "gpios" than there is no name.

    controller:
      The Device instance for the controller of the GPIO

    specifier:
      A dictionary that maps names from the #cells portion of the binding to
      cell values in the gpio specifier. 'foo-gpios = <&gpioc 5 0>' might give
      {"pin": 5, "flags": 0}, for example.
    """
    def __repr__(self):
        fields = []

        if self.name is not None:
            fields.append("name: " + self.name)

        fields.append("target: {}".format(self.controller))
        fields.append("specifier: {}".format(self.specifier))

        return "<GPIO, {}>".format(", ".join(fields))


class Clock:
    """
    Represents a clock used by a device.

    These attributes are available on Clock objects:

    dev:
      The Device instance that uses the clock

    name:
      The name of the clock as given in the 'clock-names' property, or
      None if there is no 'clock-names' property

    controller:
      The Device instance for the controller of the clock.

    frequency:
      The frequency of the clock, None if its not determinable

    specifier:
      A dictionary that maps names from the #cells portion of the binding to
      cell values in the clock specifier
    """
    def __repr__(self):
        fields = []

        if self.name is not None:
            fields.append("name: " + self.name)

        fields.append("target: {}".format(self.controller))
        fields.append("specifier: {}".format(self.specifier))

        return "<Clock, {}>".format(", ".join(fields))


class PWM:
    """
    Represents a PWM used by a device.

    These attributes are available on PWM objects:

    dev:
      The Device instance that uses the PWM

    name:
      The name of the pwm as given in the 'pwm-names' property, or the
      node name if the 'pwm-names' property doesn't exist.

    controller:
      The Device instance for the controller of the PWM

    specifier:
      A dictionary that maps names from the #cells portion of the binding to
      cell values in the pwm specifier. 'pwms = <&pwm 0 5000000>' might give
      {"channel": 0, "period": 5000000}, for example.
    """
    def __repr__(self):
        fields = []

        if self.name is not None:
            fields.append("name: " + self.name)

        fields.append("target: {}".format(self.controller))
        fields.append("specifier: {}".format(self.specifier))

        return "<PWM, {}>".format(", ".join(fields))


class Property:
    """
    Represents a property on a Device, as set in its DT node and with
    additional info from the 'properties:' section of the binding.

    Only properties mentioned in 'properties:' get created.

    These attributes are available on Property objects:

    dev:
      The Device instance the property is on

    name:
      The name of the property

    val:
      The value of the property, with the format determined by the 'type:'
      key from the binding

    enum_index:
      The index of the property's value in the 'enum:' list in the binding, or
      None if the binding has no 'enum:'
    """
    def __repr__(self):
        fields = ["name: " + self.name,
                  "value: " + self.val]

        if self.enum_val is not None:
            fields.append("enum index: {}".format(self.enum_val))

        return "<Property, {}>".format(", ".join(fields))


class EDTError(Exception):
    "Exception raised for Extended Device Tree-related errors"


#
# Public global functions
#


def spi_dev_cs_gpio(dev):
    # Returns an SPI device's GPIO chip select if it exists, as a GPIO
    # instance, and None otherwise. See
    # Documentation/devicetree/bindings/spi/spi-bus.txt in the Linux kernel.

    if dev.bus == "spi" and "cs" in dev.parent.gpios:
        # cs-gpios is indexed by the unit address
        # TODO: Error out if dev.regs is empty?
        return dev.parent.gpios["cs"][dev.regs[0].addr]

    return None


#
# Private global functions
#


def _dt_compats(dt):
    # Returns a set() with all 'compatible' strings in the device tree
    # represented by dt (a dtlib.DT instance)

    return {compat
            for node in dt.node_iter()
                if "compatible" in node.props
                    for compat in node.props["compatible"].to_strings()}


def _binding_paths(bindings_dir):
    # Returns a list with the paths to all bindings (.yaml files) in
    # 'bindings_dir'

    return [os.path.join(root, filename)
            for root, _, filenames in os.walk(bindings_dir)
                for filename in filenames
                    if filename.endswith(".yaml")]


def _binding_compat(binding_path):
    # Returns the compatible string specified in the binding at 'binding_path'.
    # Uses a regex to avoid having to parse the bindings, which is slow when
    # done for all bindings.

    with open(binding_path) as binding:
        for line in binding:
            match = re.match(r'\s+constraint:\s*"([^"]*)"', line)
            if match:
                return match.group(1)

    return None


def _binding_bus(binding):
    # Returns the bus specified in 'binding' (the bus the device described by
    # 'binding' is on), e.g. "i2c", or None if 'binding' is None or doesn't
    # specify a bus

    if binding and "parent" in binding:
        return binding["parent"].get("bus")
    return None


def _yaml_inc_error(msg):
    # Helper for reporting errors in the !include implementation

    raise yaml.constructor.ConstructorError(None, None, "error: " + msg)


def _load_binding(path):
    # Loads a top-level binding .yaml file from 'path', also handling any
    # !include'd files. Returns the parsed PyYAML output (Python
    # lists/dictionaries/strings/etc. representing the file).

    with open(path, encoding="utf-8") as f:
        # TODO: Nicer way to deal with this?
        return _merge_binding(yaml.load(f, Loader=yaml.Loader), path)


def _merge_binding(yaml_top, binding_path):
    # Recursively merges yaml_top into the bindings in the 'inherits:' section
    # of the binding. !include's have already been processed at this point, and
    # leave the data for the !include'd file(s) in the 'inherits:' section.
    #
    # Properties from the !include'ing file override properties from the
    # !include'd file, which is why this logic might seem "backwards".

    _check_expected_props(yaml_top, binding_path)

    if 'inherits' in yaml_top:
        for inherited in yaml_top.pop('inherits'):
            inherited = _merge_binding(inherited, binding_path)
            _merge_props(None, inherited, yaml_top, binding_path)
            yaml_top = inherited

    return yaml_top


def _check_expected_props(yaml_top, binding_path):
    # Checks that the top-level YAML node 'node' has the expected properties.
    # Prints warnings and substitutes defaults otherwise.

    for prop in "title", "version", "description":
        if prop not in yaml_top:
            _err("'{}' lacks '{}' property".format(binding_path, prop))


def _merge_props(parent_prop, to_dict, from_dict, binding_path):
    # Recursively merges 'from_dict' into 'to_dict', to implement !include.
    #
    # binding_path is the path of the top-level binding, and parent_prop the
    # name of the dictionary containing 'prop'. These are used to generate
    # warnings for sketchy property overwrites.

    for prop in from_dict:
        if isinstance(from_dict[prop], dict) and \
           isinstance(to_dict.get(prop), dict):
            _merge_props(prop, to_dict[prop], from_dict[prop], binding_path)
        else:
            if _bad_overwrite(to_dict, from_dict, prop):
                _err("{} (in '{}'): '{}' from !include'd file overwritten "
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


def _add_names(node, names_ident, objs):
    # Helper for registering names from <foo>-names properties.
    #
    # node:
    #   Device tree node
    #
    # names-ident:
    #   name of property holding names, e.g. "reg-names"
    #
    # objs:
    #   list of objects whose .name field should be set

    if names_ident in node.props:
        names = node.props[names_ident].to_strings()
        if len(names) != len(objs):
            _err("{} property in {} has {} strings, expected {} strings"
                 .format(names_ident, node.name, len(names), len(objs)))

        for obj, name in zip(objs, names):
            obj.name = name
    else:
        for obj in objs:
            obj.name = None


def _interrupt_parent(node):
    # Returns the node pointed at by the closest 'interrupt-parent', searching
    # the parents of 'node'. As of writing, this behavior isn't specified in
    # the DT spec., but seems to match what some .dts files except.

    while node:
        if "interrupt-parent" in node.props:
            return node.props["interrupt-parent"].to_node()
        node = node.parent

    _err("{!r} has an 'interrupts' property, but neither the node nor any "
         "of its parents has an 'interrupt-parent' property".format(node))


def _interrupts(node):
    # Returns a list of (<controller>, <specifier>) tuples, with one tuple per
    # interrupt generated by 'node'. <controller> is the destination of the
    # interrupt (possibly after mapping through an 'interrupt-map'), and
    # <specifier> the data associated with the interrupt (as a 'bytes' object).

    # Takes precedence over 'interrupts' if both are present
    if "interrupts-extended" in node.props:
        prop = node.props["interrupts-extended"]
        return [_map_interrupt(node, iparent, spec)
                for iparent, spec in _phandle_val_list(prop, _interrupt_cells)]

    if "interrupts" in node.props:
        # Treat 'interrupts' as a special case of 'interrupts-extended', with
        # the same interrupt parent for all interrupts

        iparent = _interrupt_parent(node)
        interrupt_cells = _interrupt_cells(iparent)

        return [_map_interrupt(node, iparent, raw)
                for raw in _slice(node, "interrupts", 4*interrupt_cells)]

    return []


def _map_interrupt(child, parent, child_spec):
    # Translates an interrupt headed from 'child' to 'parent' with specifier
    # 'child_spec' through any 'interrupt-map' properties. Returns a
    # (<controller>, <specifier>) tuple with the final destination after
    # mapping.

    if "interrupt-controller" in parent.props:
        return (parent, child_spec)

    def own_address_cells(node):
        # Used for parents pointed at by 'interrupt-map'. We can't use
        # _address_cells(), because it's the #address-cells property on 'node'
        # itself that matters.

        address_cells = node.props.get("#address-cells")
        if not address_cells:
            _err("missing #address-cells on {!r} (while handling interrupt-map)"
                 .format(node))
        return address_cells.to_num()

    def spec_len_fn(node):
        # Can't use _address_cells() here, because it's the #address-cells
        # property on 'node' itself that matters
        return 4*(own_address_cells(node) + _interrupt_cells(node))

    parent, raw_spec = _map(
        "interrupt", child, parent, _raw_unit_addr(child) + child_spec,
        spec_len_fn)

    # Strip the parent unit address part, if any
    return (parent, raw_spec[4*own_address_cells(parent):])


def _clocks(node):
    # Returns a list of (<controller>, <specifier>) tuples for any 'clocks'
    # property on 'node', or an empty list if 'node' has no 'clocks'.
    # <controller> is a dtlib.Node.

    if "clocks" not in node.props:
        return []
    return _phandle_val_list(node.props["clocks"], _clock_cells)


def _pwms(node):
    # Returns a list of (<controller>, <specifier>) tuples for any 'pwms'
    # property on 'node', or an empty list if 'node' has no 'pwms'.
    # <controller> is a dtlib.Node.

    if "pwms" not in node.props:
        return []
    return _phandle_val_list(node.props["pwms"], _pwm_cells)


def _gpios(node):
    # Returns a dictionary that maps '<prefix>-gpios' prefixes to lists of
    # (<controller>, <specifier>) tuples (possibly after mapping through an
    # gpio-map). <controller> is a dtlib.Node.

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

            res[prefix] = [
                _map_gpio(prop.node, controller, spec)
                for controller, spec in _phandle_val_list(prop, _gpio_cells)
            ]

    return res


def _map_gpio(child, parent, child_spec):
    # Returns a (<controller>, <specifier>) tuple with the final destination
    # after mapping through any 'gpio-map' properties. See _map_interrupt().

    if "gpio-map" not in parent.props:
        return (parent, child_spec)

    return _map("gpio", child, parent, child_spec,
                lambda node: 4*_gpio_cells(node))


def _map(prefix, child, parent, child_spec, spec_len_fn):
    # Common code for mapping through <prefix>-map properties, e.g.
    # interrupt-map and gpio-map.
    #
    # prefix:
    #   The prefix, e.g. "interrupt" or "gpio"
    #
    # child:
    #   The "sender", e.g. the node with 'interrupts = <...>'
    #
    # parent:
    #   The "receiver", e.g. a node with 'interrupt-map = <...>' or
    #   'interrupt-controller' (no mapping)
    #
    # child_spec:
    #   The data associated with the interrupt/GPIO/etc., as a 'bytes' object,
    #   e.g. <1 2> for 'foo-gpios = <&gpio1 1 2>'.
    #
    # spec_len_fn:
    #   Function called on a parent specified in a *-map property to get its
    #   *-cells value, e.g. #interrupt-cells

    map_prop = parent.props.get(prefix + "-map")
    if not map_prop:
        if prefix + "-controller" not in parent.props:
            _err("expected '{}-controller' property on {!r} "
                 "(referenced by {!r})".format(prefix, parent, child))

        # No mapping
        return (parent, child_spec)

    masked_child_spec = _mask(prefix, child, parent, child_spec)

    raw = map_prop.value
    while raw:
        if len(raw) < len(child_spec):
            _err("bad value for {!r}, missing/truncated child specifier"
                 .format(map_prop))
        child_spec_entry = raw[:len(child_spec)]
        raw = raw[len(child_spec):]

        if len(raw) < 4:
            _err("bad value for {!r}, missing/truncated phandle"
                 .format(map_prop))
        phandle = to_num(raw[:4])
        raw = raw[4:]

        # Parent specified in *-map
        map_parent = parent.dt.phandle2node.get(phandle)
        if not map_parent:
            _err("bad phandle in " + repr(map_prop))

        map_parent_spec_len = spec_len_fn(map_parent)
        if len(raw) < map_parent_spec_len:
            _err("bad value for {!r}, missing/truncated parent specifier"
                 .format(map_prop))
        parent_spec = raw[:map_parent_spec_len]
        raw = raw[map_parent_spec_len:]

        # Got one *-map row. Check if it matches the child specifier.
        if child_spec_entry == masked_child_spec:
            # Handle *-map-pass-thru
            parent_spec = _pass_thru(
                prefix, child, parent, child_spec, parent_spec)

            # Found match. Recursively map and return it.
            return _map(prefix, parent, map_parent, parent_spec, spec_len_fn)

    _err("child specifier for {!r} ({}) does not appear in {!r}"
         .format(child, child_spec, map_prop))


def _mask(prefix, child, parent, child_spec):
    # Common code for handling <prefix>-mask properties, e.g. interrupt-mask.
    # See _map() for the parameters.

    mask_prop = parent.props.get(prefix + "-map-mask")
    if not mask_prop:
        # No mask
        return child_spec

    mask = mask_prop.value
    if len(mask) != len(child_spec):
        _err("{!r}: expected '{}-mask' in {!r} to be {} bytes, is {} bytes"
             .format(child, prefix, parent, len(child_spec), len(mask)))

    return _and(child_spec, mask)


def _pass_thru(prefix, child, parent, child_spec, parent_spec):
    # Common code for handling <prefix>-map-thru properties, e.g.
    # interrupt-pass-thru.
    #
    # parent_spec:
    #   The parent specifier from the matched entry in the <prefix>-map
    #   property
    #
    # See _map() for the other parameters.

    pass_thru_prop = parent.props.get(prefix + "-map-pass-thru")
    if not pass_thru_prop:
        # No pass-thru
        return parent_spec

    pass_thru = pass_thru_prop.value
    if len(pass_thru) != len(child_spec):
        _err("{!r}: expected '{}-map-pass-thru' in {!r} to be {} bytes, is {} bytes"
             .format(child, prefix, parent, len(child_spec), len(pass_thru)))

    res = _or(_and(child_spec, pass_thru),
              _and(parent_spec, _not(pass_thru)))

    # Truncate to length of parent spec.
    return res[-len(parent_spec):]


def _raw_unit_addr(node):
    # _map_interrupt() helper. Returns the unit address (derived from 'reg' and
    # #address-cells) as a raw 'bytes'

    if 'reg' not in node.props:
        _err("{!r} lacks 'reg' property (needed for 'interrupt-map' unit "
             "address lookup)".format(node))

    addr_len = 4*_address_cells(node)

    if len(node.props['reg'].value) < addr_len:
        _err("{!r} has too short 'reg' property (while doing 'interrupt-map' "
             "unit address lookup)".format(node))

    return node.props['reg'].value[:addr_len]


def _and(b1, b2):
    # Returns the bitwise AND of the two 'bytes' objects b1 and b2. Pads
    # with ones on the left if the lengths are not equal.

    # Pad on the left, to equal length
    maxlen = max(len(b1), len(b2))
    return bytes(x & y for x, y in zip(b1.rjust(maxlen, b'\xff'),
                                       b2.rjust(maxlen, b'\xff')))


def _or(b1, b2):
    # Returns the bitwise OR of the two 'bytes' objects b1 and b2. Pads with
    # zeros on the left if the lengths are not equal.

    # Pad on the left, to equal length
    maxlen = max(len(b1), len(b2))
    return bytes(x | y for x, y in zip(b1.rjust(maxlen, b'\x00'),
                                       b2.rjust(maxlen, b'\x00')))


def _not(b):
    # Returns the bitwise not of the 'bytes' object 'b'

    # ANDing with 0xFF avoids negative numbers
    return bytes(~x & 0xFF for x in b)


def _phandle_val_list(prop, n_cells_fn):
    # Parses a '<phandle> <value> <phandle> <value> ...' value
    #
    # prop:
    #   dtlib.Property with value to parse
    #
    # cells_fn:
    #   A function that returns the number of cells for a <value>. Takes the
    #   node pointed at by the preceding <phandle> as its argument.
    #
    # Returns a list of (<node>, <value>) tuples, where <node> is the node
    # pointed at by <phandle>. 'cells_fn' is a function called on <node> to get
    # the number of cells for <value>.

    res = []

    raw = prop.value
    while raw:
        if len(raw) < 4:
            # Not enough room for phandle
            _err("bad value for " + repr(prop))
        phandle = to_num(raw[:4])
        raw = raw[4:]

        node = prop.node.dt.phandle2node.get(phandle)
        if not node:
            _err("bad phandle in " + repr(prop))

        n_cells = n_cells_fn(node)
        if len(raw) < 4*n_cells:
            _err("missing data after phandle in " + repr(prop))

        res.append((node, raw[:4*n_cells]))
        raw = raw[4*n_cells:]

    return res


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


def _interrupt_cells(node):
    # Returns the #interrupt-cells property value on 'node', erroring out if
    # 'node' has no #interrupt-cells property

    if "#interrupt-cells" not in node.props:
        _err("{!r} lacks #interrupt-cells".format(node))
    return node.props["#interrupt-cells"].to_num()


def _gpio_cells(node):
    if "#gpio-cells" not in node.props:
        _err("{!r} lacks #gpio-cells".format(node))
    return node.props["#gpio-cells"].to_num()


def _pwm_cells(node):
    if "#pwm-cells" not in node.props:
        _err("{!r} lacks #pwm-cells".format(node))
    return node.props["#pwm-cells"].to_num()


def _clock_cells(node):
    if "#clock-cells" not in node.props:
        _err("{!r} lacks #clock-cells".format(node))
    return node.props["#clock-cells"].to_num()


def _slice(node, prop_name, size):
    # Splits node.props[prop_name].value into 'size'-sized chunks, returning a
    # list of chunks. Raises EDTError if the length of the property is not
    # evenly divisible by 'size'.

    raw = node.props[prop_name].value
    if len(raw) % size:
        _err("'{}' property in {!r} has length {}, which is not evenly "
             "divisible by {}".format(prop_name, node, len(raw), size))

    return [raw[i:i + size] for i in range(0, len(raw), size)]


def _err(msg):
    raise EDTError(msg)


def _warn(msg):
    print("warning: " + msg, file=sys.stderr)
