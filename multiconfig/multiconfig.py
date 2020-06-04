#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import abc
import argparse
import copy
import functools
import json
import re

# Make argparse.FileType available in this module
FileType = argparse.FileType


# ------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------


class ParseError(RuntimeError):
    """
    Base class for exceptions indicating a configuration error.
    """


class RequiredConfigNotFoundError(ParseError):
    """
    Exception raised when a required config value could not be found from any
    source.
    """


class InvalidChoiceError(ParseError):
    """
    Exception raised when a config value is not from a specified set of values.
    """

    def __init__(self, spec, value):
        choice_str = ",".join((str(c) for c in spec.choices))
        super().__init__(
            f"invalid choice '{value}' for config item '{spec.name}'; "
            f"valid choices are ({choice_str})"
        )


class InvalidNumberOfValuesError(ParseError):
    """
    Exception raised when the number of values supplied for a config item is
    not valid.
    """

    def __init__(self, spec, value):
        assert spec.nargs != ZERO_OR_MORE
        if spec.nargs == 1:
            expecting = "1 value"
        elif isinstance(spec.nargs, int):
            expecting = f"{spec.nargs} values"
        elif spec.nargs == ZERO_OR_ONE:
            expecting = "up to 1 value"
        else:
            assert spec.nargs == ONE_OR_MORE
            expecting = "1 or more values"

        super().__init__(
            f"invalid number of values for config item {spec.name}; "
            f"expecting {expecting}"
        )


class InvalidValueForNargs0Error(ParseError):
    """
    Exception raised when a value recieved for a config item with nargs=0 is
    not a "none" value.
    """

    def __init__(self, value, none_values):
        none_values_str = ", ".join((str(v) for v in none_values))
        super().__init__(
            f"invalid value '{str(value)}' for config item with nargs=0; "
            f"valid values: ({none_values_str})"
        )


# ------------------------------------------------------------------------------
# Tags
# ------------------------------------------------------------------------------


class _SuppressAttributeCreation:
    def __str__(self):
        return "SUPPRESS"

    __repr__ = __str__


SUPPRESS = _SuppressAttributeCreation()


class _None:
    def __str__(self):
        return "NONE"

    __repr__ = __str__


NONE = _None()


class _PresentWithoutValue:
    def __str__(self):
        return "PRESENT_WITHOUT_VALUE"

    __repr__ = __str__


PRESENT_WITHOUT_VALUE = _PresentWithoutValue()


def present_without_value(v):
    return PRESENT_WITHOUT_VALUE


ZERO_OR_MORE = "*"
ONE_OR_MORE = "+"
ZERO_OR_ONE = "?"

# ------------------------------------------------------------------------------
# Classes
# ------------------------------------------------------------------------------


class Namespace:
    def __str__(self):
        return str(vars(self))

    def __eq__(self, other):
        return other.__class__ == self.__class__ and vars(other) == vars(self)

    __repr__ = __str__


class Source(abc.ABC):
    """
    ABC for Source classes.
    """

    @abc.abstractmethod
    def parse_config(self):
        """
        Parse this config source.

        Returns: a multiconfig.Namespace object containing the values parsed
        from this config source.

        The values should *not* be coerced to the type specified by their
        _ConfigSpec.

        Subclasses must implement this method.
        """


class DictSource(Source):
    """
    Obtains config values from a dict.

    Do not create objects of this class directly - create them via
    ConfigParser.add_source() instead:
    config_parser.add_source(multiconfig.DictSource, {"some": "dict"})
    """

    def __init__(
        self, config_specs, d, none_values=[None, PRESENT_WITHOUT_VALUE]
    ):
        self._config_specs = config_specs
        self._dict = d
        self._none_values = none_values

    def parse_config(self):
        ns = Namespace()
        for spec in self._config_specs:
            if spec.name not in self._dict:
                continue
            value = self._dict[spec.name]
            if spec.nargs == ZERO_OR_ONE and value in self._none_values:
                value = PRESENT_WITHOUT_VALUE
            if spec.nargs == ZERO_OR_MORE and value in self._none_values:
                if value in self._none_values:
                    value = []
            if spec.nargs == 0:
                if value not in self._none_values:
                    raise InvalidValueForNargs0Error(value, self._none_values)
                value = PRESENT_WITHOUT_VALUE
            setattr(ns, spec.name, [value])
        return ns


class ArgparseSource(Source):
    def __init__(self, config_specs):
        """
        Don't call this directly, use ConfigParser.add_source() instead.
        """
        self._config_specs = config_specs
        self._parsed_values = None

    def parse_config(self):
        """
        Don't call this directly, use ConfigParser.parse_config() instead.
        """
        return self._parsed_values

    def add_configs_to_argparse_parser(self, argparse_parser):
        """
        Add arguments to an argparse.ArgumentParser object (or an object of a
        subclass) to obtain config values from the command line.
        """
        for spec in self._config_specs:
            arg_name = self._config_name_to_arg_name(spec.name)
            kwargs = {
                "help": spec.help,
                "default": [],
            }
            if spec.nargs == 0:
                kwargs["action"] = "append_const"
            else:
                kwargs["action"] = "append"

            if spec.nargs is not NONE and spec.nargs not in (0, 1):
                kwargs["nargs"] = spec.nargs

            if spec.nargs is ZERO_OR_ONE or spec.nargs == 0:
                kwargs["const"] = PRESENT_WITHOUT_VALUE

            argparse_parser.add_argument(arg_name, **kwargs)

    def notify_parsed_args(self, argparse_namespace):
        """
        Call this method with the argparse.Namespace object returned by
        argparse.ArgumentParser.parse_args() to notify this ArgparseSource
        object of the results.
        """
        ns = Namespace()
        for spec in self._config_specs:
            if not hasattr(argparse_namespace, spec.name):
                continue
            values = getattr(argparse_namespace, spec.name)
            if values:
                setattr(ns, spec.name, values)
        self._parsed_values = ns

    @staticmethod
    def _config_name_to_arg_name(config_name):
        return f"--{config_name.replace('_', '-')}"


class SimpleArgparseSource(Source):
    """
    Obtains config values from the command line using argparse.ArgumentParser.

    This class is simpler to use than ArgparseSource but does not allow adding
    arguments beside those added to the ConfigParser.

    Do not create objects of this class directly - create them via
    ConfigParser.add_source() instead:
    config_parser.add_source(multiconfig.SimpleArgparseSource, **options)

    Extra options that can be passed to ConfigParser.add_source() for
    SimpleArgparseSource are:
    * argument_parser_class: a class derived from argparse.ArgumentParser to
      use instead of ArgumentParser itself. This can be useful if you want to
      override ArgumentParser's exit() or error() methods.

    * Extra arguments to pass to ArgumentParser.__init__() (or the __init__()
      method for the class specified by the 'argument_parser_class' option.
      E.g.  'prog', 'allow_help'. You probably don't want to use the
      'argument_default' option though - see ConfigParser.__init__()'s
      'config_default' option instead.
    """

    def __init__(
        self,
        config_specs,
        argument_parser_class=argparse.ArgumentParser,
        **kwargs,
    ):
        """
        Don't call this directly, use ConfigParser.add_source() instead.
        """
        self._argparse_source = ArgparseSource(config_specs)
        self._argparse_parser = argument_parser_class(**kwargs)
        self._argparse_source.add_configs_to_argparse_parser(
            self._argparse_parser
        )

    def parse_config(self):
        """
        Don't call this directly, use ConfigParser.parse_config() instead.
        """
        self._argparse_source.notify_parsed_args(
            self._argparse_parser.parse_args()
        )
        return self._argparse_source.parse_config()


class JsonSource(Source):
    """
    Obtains config values from a JSON file.

    Do not create objects of this class directly - create them via
    ConfigParser.add_source() instead:
    config_parser.add_source(multiconfig.JsonSource, **options)

    Extra options that can be passed to ConfigParser.add_source() for
    JsonSource are:
    * path: path to the JSON file to parse.
    * fileobj: a file object representing a stream of JSON data.

    Note: exactly one of the 'path' and 'fileobj' options must be given.
    """

    def __init__(
        self,
        config_specs,
        path=None,
        fileobj=None,
        none_values=[],
        json_none_values=["null"],
    ):
        """
        Don't call this directly, use ConfigParser.add_source() instead.
        """
        if path and fileobj:
            raise ValueError(
                "JsonSource's 'path' and 'fileobj' options were both "
                "specified but only one is expected"
            )
        d = self._get_json(path, fileobj)
        self._dict_source = DictSource(
            config_specs,
            d,
            none_values=[json.loads(v) for v in json_none_values]
            + none_values,
        )

    def parse_config(self):
        """
        Don't call this directly, use ConfigParser.parse_config() instead.
        """
        return self._dict_source.parse_config()

    @staticmethod
    def _get_json(path, fileobj):
        if path:
            with open(path, mode="r") as f:
                return json.load(f)
        else:
            return json.load(fileobj)


class _ConfigSpec(abc.ABC):
    """
    Base class for config specifications.
    """

    # Dict of subclasses that handle specific actions. The name of the action
    # is the dict item's key and the subclass is the dict item's value.
    _subclasses = {}

    def __init_subclass__(cls, **kwargs):
        """
        Automatically register subclasses specialized to handle a particular
        action. For a subclass to be registered it must have the name of the
        action it handles in an 'action' class attribute.
        """
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "action"):
            cls._subclasses[cls.action] = cls

    @classmethod
    def create(cls, action="store", **kwargs):
        """
        Factory to obtain _ConfigSpec objects with the correct subclass to
        handle the given action.
        """
        if action == "append_const":
            raise NotImplementedError(
                f"action '{action}' has not been implemented"
            )
        if action not in cls._subclasses:
            raise ValueError(f"unknown action '{action}'")
        return cls._subclasses[action](**kwargs)

    def __init__(
        self,
        name,
        nargs=NONE,
        type=str,
        required=False,
        choices=NONE,
        help=None,
    ):
        """
        Don't call this directly - use create() instead.
        """
        self._set_name(name)
        self._set_nargs(nargs)
        self._set_type(type)
        self.required = required
        self.choices = choices
        self.help = help

    def accumulate_raw_values(self, current, raw_news):
        return functools.reduce(self.accumulate_raw_value, raw_news, current)

    def accumulate_raw_value(self, current, raw_new):
        """
        Combine a new raw value for this config with any existing value.

        Args:
        * current: The current value for this config item (which may be NONE).
        * raw_new: The new value to combine with the current value for this
          config item. The value has not already been coerced to the config's
          type.

        Returns: the new combined value.
        """
        return self._accumulate_processed_value(
            current, self._process_value(raw_new)
        )

    @abc.abstractmethod
    def _accumulate_processed_value(self, current, new):
        """
        Combine a new processed value for this config with any existing value.

        This method must be implemented by subclasses.

        Args:
        * current: The current value for this config item (which may be NONE).
        * new: The new value to combine with the current value for this config
          item.

        The new value will have:
        * been coerced to the config's type;
        * checked for nargs and choices validity;
        * had values for nargs==1 converted to a single element list.

        The new value will not have:
        * had any processing for values;
        * had any processing for const arguments.

        Returns: the new combined value.
        """

    @abc.abstractmethod
    def apply_default(self, value):
        """
        Returns a value for this config item after applying defaults.

        This method must be implemented by subclasses.

        Args:
        * value: The current value for this config item (which maybe NONE).
          This value has already been coerced to the config's type (unless it
          is NONE).
        """

    def _set_name(self, name):
        if re.match(r"[^0-9A-Za-z_]", name) or re.match(r"^[^a-zA-Z_]", name):
            raise ValueError(
                f"invalid config name '{name}', "
                "must be a valid Python identifier"
            )
        self.name = name

    def _set_nargs(self, nargs):
        if nargs is NONE or nargs is None:
            self.nargs = NONE
        elif nargs == "*":
            self.nargs = ZERO_OR_MORE
        elif nargs == "+":
            self.nargs = ONE_OR_MORE
        elif nargs == "?":
            self.nargs = ZERO_OR_ONE
        elif isinstance(nargs, int) and nargs >= 0:
            self.nargs = nargs
        else:
            raise ValueError(f"invalid nargs value {nargs}")

    def _set_type(self, type):
        """
        Validate and set the type of this config item.

        This is a default implementation that may be called by subclasses.
        """
        if not callable(type):
            raise TypeError("'type' argument must be callable")
        self.type = type

    def _process_value(self, value):
        assert value is not NONE
        if self.nargs == 0:
            assert value is PRESENT_WITHOUT_VALUE
            return value
        if self.nargs is NONE:
            new = self.type(value)
            self._validate_choice(new)
            return new
        if self.nargs == ZERO_OR_ONE:
            if value is PRESENT_WITHOUT_VALUE:
                return value
            else:
                new = self.type(value)
                self._validate_choice(new)
                return new
        if self.nargs == 1:
            new = [self.type(value)]
            self._validate_choices(new)
            return new
        new = [self.type(v) for v in value]
        self._validate_choices(new)
        if self.nargs == ONE_OR_MORE and not new:
            raise InvalidNumberOfValuesError(self, new)
        elif isinstance(self.nargs, int) and len(new) != self.nargs:
            raise InvalidNumberOfValuesError(self, new)
        return new

    def _validate_choice(self, value):
        if self.choices is not NONE and value not in self.choices:
            raise InvalidChoiceError(self, value)

    def _validate_choices(self, values):
        for v in values:
            self._validate_choice(v)


class _ConfigSpecWithChoices(_ConfigSpec):
    def __init__(self, choices=NONE, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(**kwargs)
        self.choices = choices


class _StoreConfigSpec(_ConfigSpecWithChoices):
    action = "store"

    def __init__(self, const=NONE, default=NONE, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(**kwargs)
        self._set_const(const)
        self.default = default

    def _accumulate_processed_value(self, current, new):
        assert new is not NONE
        return new

    def apply_default(self, value, global_default):
        if value is NONE:
            return self.default
        if self.nargs == ZERO_OR_ONE and value is PRESENT_WITHOUT_VALUE:
            return self.const
        return value

    def _set_nargs(self, nargs):
        super()._set_nargs(nargs)
        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action} action"
            )

    def _set_const(self, const):
        if const is not NONE and self.nargs != ZERO_OR_ONE:
            raise ValueError(
                f"const cannot be supplied to the {self.action} action "
                f"unless nargs is {ZERO_OR_ONE}"
            )
        self.const = const


class _StoreConstConfigSpec(_ConfigSpec):
    action = "store_const"

    def __init__(
        self, const, default=NONE, **kwargs,
    ):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(
            nargs=0, type=present_without_value, required=False, **kwargs
        )
        self.const = const
        self.default = default

    def _accumulate_processed_value(self, current, new):
        assert new is PRESENT_WITHOUT_VALUE
        return PRESENT_WITHOUT_VALUE

    def apply_default(self, value, global_default):
        if value is PRESENT_WITHOUT_VALUE:
            return self.const
        assert value is NONE
        return self.default


class _StoreTrueConfigSpec(_StoreConstConfigSpec):
    action = "store_true"

    def __init__(self, default=False, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(const=True, default=default, **kwargs)


class _StoreFalseConfigSpec(_StoreConstConfigSpec):
    action = "store_false"

    def __init__(self, default=True, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(const=False, default=default, **kwargs)


class _AppendConfigSpec(_ConfigSpecWithChoices):
    action = "append"

    def __init__(self, const=NONE, default=NONE, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(**kwargs)
        self._set_const(const)
        self.default = default

    def _accumulate_processed_value(self, current, new):
        assert new is not NONE
        if current is NONE:
            return [new]
        return current + [new]

    def apply_default(self, value, global_default):
        if value is NONE:
            return self.default
        if self.nargs == ZERO_OR_ONE:
            const = None
            if self.const is not NONE:
                const = self.const
            value = [const if v is PRESENT_WITHOUT_VALUE else v for v in value]
        if self.default is not NONE and self.default is not SUPPRESS:
            return self.default + value
        return value

    def _set_nargs(self, nargs):
        super()._set_nargs(nargs)
        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action} action"
            )

    def _set_const(self, const):
        if const is not NONE and self.nargs != ZERO_OR_ONE:
            raise ValueError(
                f"const cannot be supplied to the {self.action} action "
                f"unless nargs is {ZERO_OR_ONE}"
            )
        self.const = const


class _CountConfigSpec(_ConfigSpec):
    action = "count"

    def __init__(
        self, default=NONE, **kwargs,
    ):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(nargs=0, type=present_without_value, **kwargs)
        self.default = default

    def _accumulate_processed_value(self, current, new):
        assert new is PRESENT_WITHOUT_VALUE
        if current is NONE:
            return 1
        return current + 1

    def apply_default(self, value, global_default):
        if value is NONE:
            return self.default
        if self.default is not NONE and self.default is not SUPPRESS:
            return self.default + value
        return value


class _ExtendConfigSpec(_ConfigSpecWithChoices):
    action = "extend"

    def __init__(self, default=NONE, **kwargs):
        """
        Do not call this directly - use _ConfigItem.create() instead.
        """
        super().__init__(nargs="+", **kwargs)
        self.default = default

    def _accumulate_processed_value(self, current, new):
        assert new is not NONE
        assert isinstance(new, list)
        if current is NONE:
            return new
        return current + new

    def apply_default(self, value, global_default):
        if value is NONE:
            return self.default
        if self.default is not NONE and self.default is not SUPPRESS:
            return self.default + value
        return value


class ConfigParser:
    def __init__(self, config_default=NONE):
        """
        Create a ConfigParser object.

        Args:
        * config_default: the value to use in the multiconfig.Namespace
          returned by parse_config() for config items for which a value was not
          found in any config source. The default behaviour is to represent
          these config items with None. Set config_default to
          multiconfig.SUPPRESS to prevent these configs from having an
          attribute set in the Namespace at all.
        """
        self._config_specs = []
        self._sources = []
        self._parsed_values = Namespace()
        self._global_default = config_default

    def add_config(self, name, **kwargs):
        """
        Add a config item to this ConfigParser.
        """
        if "default" not in kwargs and self._global_default is not NONE:
            kwargs["default"] = self._global_default
        spec = _ConfigSpec.create(name=name, **kwargs)
        self._config_specs.append(spec)
        return spec

    def add_source(self, source_class, *args, **kwargs):
        """
        Add a config source to this ConfigParser.
        """
        source = source_class(copy.copy(self._config_specs), *args, **kwargs)
        self._sources.append(source)
        return source

    def _add_preparsed_values(self, preparsed_values):
        for spec in self._config_specs:
            if not hasattr(preparsed_values, spec.name):
                continue
            current = _getattr_or_none(self._parsed_values, spec.name)
            raw_news = getattr(preparsed_values, spec.name)
            new = spec.accumulate_raw_values(current, raw_news)
            assert new is not NONE
            setattr(self._parsed_values, spec.name, new)

    def partially_parse_config(self):
        """
        Parse the config sources, but don't raise a RequiredConfigNotFoundError
        exception if a required config is not found in any config source.

        Returns: a multiconfig.Namespace object containing the parsed values.
        """
        for source in self._sources:
            new_values = source.parse_config()
            self._add_preparsed_values(new_values)
        return self._get_configs_with_defaults()

    def parse_config(self):
        """
        Parse the config sources.

        Returns: a multiconfig.Namespace object containing the parsed values.
        """
        values = self.partially_parse_config()
        self._check_required_configs()
        return values

    def _get_configs_with_defaults(self):
        values = copy.copy(self._parsed_values)
        for spec in self._config_specs:
            value = spec.apply_default(
                _getattr_or_none(values, spec.name), self._global_default
            )
            if value is SUPPRESS:
                continue
            elif value is NONE:
                setattr(values, spec.name, None)
            else:
                setattr(values, spec.name, value)
        return values

    def _check_required_configs(self):
        for spec in self._config_specs:
            if not _has_nonnone_attr(self._parsed_values, spec.name):
                if spec.required:
                    raise RequiredConfigNotFoundError(
                        f"Did not find value for config item '{spec.name}'"
                    )


# ------------------------------------------------------------------------------
# Free functions
# ------------------------------------------------------------------------------


def _getattr_or_none(obj, attr):
    if hasattr(obj, attr):
        return getattr(obj, attr)
    return NONE


def _has_nonnone_attr(obj, attr):
    return _getattr_or_none(obj, attr) is not NONE


def _namespace_from_dict(d, config_specs=None):
    ns = Namespace()
    if config_specs is not None:
        for spec in config_specs:
            if spec.name in d:
                setattr(ns, spec.name, d[spec.name])
    else:
        for k, v in d.items():
            setattr(ns, k, v)
    return ns


def _namespace(obj, config_specs=None):
    return _namespace_from_dict(vars(obj), config_specs)
