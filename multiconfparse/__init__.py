#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import abc
import argparse
import json
import operator
import os
import re
import shlex

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

    def __init__(self, action, value):
        choice_str = ",".join((str(c) for c in action.choices))
        super().__init__(
            f"invalid choice '{value}' for config item '{action.name}'; "
            f"valid choices are ({choice_str})"
        )


class InvalidNumberOfValuesError(ParseError):
    """
    Exception raised when the number of values supplied for a config item is
    not valid.
    """

    def __init__(self, action):
        assert action.nargs != "*"
        if action.nargs == 1:
            expecting = "1 value"
        elif isinstance(action.nargs, int):
            expecting = f"{action.nargs} values"
        elif action.nargs == "?":
            expecting = "up to 1 value"
        else:
            assert action.nargs == "+"
            expecting = "1 or more values"

        super().__init__(
            f"invalid number of values for config item {action.name}; "
            f"expecting {expecting}"
        )


class InvalidValueForNargs0Error(ParseError):
    """
    Exception raised when a value recieved for an action with nargs=0 is
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


#: Singleton used as a ``default`` value for a config item to indicate that if
#: no value is found for the config item in any source, it should not be given
#: an attribute in the :class:`Namespace` returned by
#: :meth:`ConfigParser.parse_config`. The default behaviour (when a
#: ``default`` value is not given by the user) is for the :class:`Namespace`
#: returned by :meth:`ConfigParser.parse_config` to have an attribute with a
#: value of :data:`None`.
SUPPRESS = _SuppressAttributeCreation()


class _NotGiven:
    def __str__(self):
        return "NOT_GIVEN"

    __repr__ = __str__


#: Singleton used to represent that an option or config item is not present.
#:
#: This is used rather than :data:`None` to distinguish between:
#:
#: * the case where the user has provided an option with the value :data:None`
#:   and the user has not provided an option at all;
#:
#: * the case where a config item has the value :data:`None` and the case
#:   where the config item not been mentioned at all in the config.
NOT_GIVEN = _NotGiven()


# ------------------------------------------------------------------------------
# Classes
# ------------------------------------------------------------------------------


class Namespace:
    """
    An object to hold values of config items.

    :class:`Namespace` objects are essentially plain objects used as the return
    values of :meth:`ConfigParser.parse_config`. Retrieve values with normal
    attribute accesses:

    .. code-block:: python

        config_values = parser.parse_config()
        config_value = config_values.config_name

    To set values (if, for example, you are implementing a :class:`Source`
    subclass), use ``setattr``:

    .. code-block:: python

        ns = multiconfparse.Namespace()
        setattr(ns, config_name, config_value)
    """

    def __str__(self):
        return str(vars(self))

    def __eq__(self, other):
        return other.__class__ == self.__class__ and vars(other) == vars(self)

    __repr__ = __str__


class ConfigMention:
    """
    A :class:`ConfigMention` object represents a single mention of a config
    item in a source.

    The arguments are:

    * ``action``: the :class:`Action` object that corresponds with the config
      item being mentioned.

    * ``args``: a :class:`list` of arguments that accompany the config item
      mention.

    * ``priority``: the priority of the mention. Generally, this is the same as
      the ``priority`` of the :class:`Source` object that found the mention.
    """

    def __init__(self, action, args, priority):
        self.action = action
        self.args = args
        self.priority = priority


class Source(abc.ABC):
    """
    Abstract base for classes that parse config sources.

    All config source classes should:

    * Inherit from :class:`Source`.

    * Have a ``source_name`` class attribute containing the name of the source.

    * Provide an implementation for the :meth:`parse_config` method.

    * Have an :meth:`__init__` method that forwards its ``actions`` and
      ``priority`` arguments to :meth:`Source.__init__`.
      :meth:`Source.__init__` will create ``actions`` and ``priority``
      attributes to make them available to subclass methods.

      ``actions`` is a :class:`dict` with config item names as the keys and
      :class:`Action` objects as the values. The :class:`Action` attributes
      that are most useful for source classes to use are:

      * ``name``: the name of the config item to which the :class:`Action`
        applies. The source class should use this to determine which
        :class:`Action` object corresponds with each config item mention in the
        source. The ``name`` atribute of an :class:`Action` has the same value
        as the key in the ``actions`` :class:`dict`.

      * ``nargs``: this specifies the number of arguments/values that a config
        item should have when mentioned in the source.
    """

    # Dict of subclasses that handle specific config sources. The source_name
    # of the source is the dict item's key and the subclass is the dict item's
    # value.
    _subclasses = {}

    def __init_subclass__(cls, **kwargs):
        # Automatically register subclasses specialized to handle a particular
        # config source. For a subclass to be registered it must have the
        # source_name of the source it handles in a 'source_name' class
        # attribute.
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "source_name"):
            cls._subclasses[cls.source_name] = cls

    @classmethod
    def create(cls, source, *args, **kwargs):
        # Factory to obtain Source objects with the correct subclass to
        # handle the given source.

        # Users can specify the class for the source directly rather than
        # giving its source_name. Assume that's what's happening if source
        # isn't a str
        if not isinstance(source, str):
            return source(*args, **kwargs)

        if source not in cls._subclasses:
            raise ValueError(f"unknown source '{source}'")

        return cls._subclasses[source](*args, **kwargs)

    def __init__(self, actions, priority=0):
        self.actions = actions
        self.priority = priority

    @abc.abstractmethod
    def parse_config(self):
        """
        Read the values of config items for this source.

        This is an abstract method that subclasses must implement to return a
        :class:`list` containing a :class:`ConfigMention` element for each
        config item mentioned in the source, in the order in which they
        appear (unless order makes no sense for the source).

        The implementation of this method will need to make use of the
        ``actions`` and ``priority`` attributes created by the :class:`Action`
        base class.

        .. autoclass:: ConfigMention
            :noindex:
        """


class DictSource(Source):
    """
    Obtains config values from a Python :class:`dict` object.

    Do not create :class:`DictSource` objects directly, add them to a
    :class:`ConfigParser` object using :meth:`ConfigParser.add_source`. For
    example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")

        values_dict = {
            "config_item1": "v1",
            "config_item2": [1, 2],
            "config_item3": None,
        }
        parser.add_source("dict", values_dict)
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for the ``dict`` source
    are:

    * ``source`` (required, positional): ``"dict"``.

    * ``values_dict`` (required, positional): the :class:`dict` containing
      the config values.

      Note that:

      * Values in ``values_dict`` for config items with ``nargs == 0`` or
        ``nargs == "?"`` (where the ``const`` value should be used rather than
        the value from the dict) should be values from the ``none_values`` list
        described below.

      * Values in ``values_dict`` for config items with ``nargs >= 2``,
        ``nargs == "+"`` or ``nargs == "*"`` should be :class:`list` objects
        with an element for each argument of the config item.

        In the special case where ``nargs == "+"`` or ``nargs == "*"`` and
        there is a single argument for the config item, the value may be given
        without the enclosing :class:`list`, unless the argument is itself a
        :class:`list`.

    * ``none_values`` (optional, keyword): a list of values that, when seen in
      ``values_dict``, should be treated as if they were not present (i.e.
      values for config items with ``nargs == 0`` or ``nargs == "?"`` (where
      the ``const`` value should be used rather than the value from the dict).

      The default ``none_values`` is ``[None]``. Using a different
      ``none_values`` is useful if you want :data:`None` to be treated as a
      valid config value.

    * ``priority`` (optional, keyword): The priority for the source. The
      default priority for a ``dict`` source is ``0``.
    """

    source_name = "dict"

    def __init__(
        self, actions, values_dict, none_values=None, priority=0,
    ):
        super().__init__(actions, priority=priority)
        self._dict = values_dict
        if none_values is None:
            none_values = [None]
        self._none_values = none_values

    def parse_config(self):
        mentions = []
        for key, value in self._dict.items():
            action = self.actions.get(key)
            if action is None:
                continue
            if value in self._none_values and action.nargs in (0, "?", "*"):
                args = []
            elif action.nargs == 0:
                raise InvalidValueForNargs0Error(value, self._none_values)
            elif (
                action.nargs in (1, "?")
                or action.nargs is None
                or (action.nargs in ("*", "+") and not isinstance(value, list))
            ):
                args = [value]
            elif not isinstance(value, list):
                raise InvalidNumberOfValuesError(action)
            else:
                args = value
            mentions.append(ConfigMention(action, args, self.priority))
        return mentions


class EnvironmentSource(Source):
    """
    Obtains config values from the environment.

    Do not create :class:`EnvironmentSource` objects directly, add them to a
    :class:`ConfigParser` object using :meth:`ConfigParser.add_source`. For
    example:

    .. code-block:: python

        # For demonstration purposes, set some config values in environment
        # variables
        os.environ["MY_APP_CONFIG_ITEM1"] = "v1"
        os.environ["MY_APP_CONFIG_ITEM2"] = "1 2"
        os.environ["MY_APP_CONFIG_ITEM3"] = ""

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")
        parser.add_source("environment", env_var_prefix="MY_APP_")
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for the ``environment``
    source are:

    * ``source`` (required, positional): ``"environment"``

    * ``none_values`` (optional, keyword): a list of values that, when seen in
      environment variables, should be treated as if they were not present
      (i.e.  values for config items with ``nargs == 0`` or ``nargs == "?"``
      (where the ``const`` value should be used rather than the value from the
      dict).

      The default ``none_values`` is ``[""]``. using a different value for
      ``none_values`` is useful you want the empty string to be treated as a
      valid config value.

    * ``priority`` (optional, keyword): The priority for the source. The
      default priority for an ``environment`` source is ``10``.

    * ``env_var_prefix`` (optional, keyword): a string prefixed to the
      environment variable names that the source will look for.  The default
      value is ``""``.

    Note that:

    * The name of the environment variable for a config item is the config
      item's name, converted to upper case, then prefixed with
      ``env_var_prefix``.

    * Values in environment variables for config items with ``nargs == 0`` or
      ``nargs == "?"`` (where the ``const`` value should be used rather than
      the value from the environment variable) should be values from the
      ``none_values`` list described above.

    * Values in environment variables for config items with ``nargs >= 2``,
      ``nargs == "+"`` or ``nargs == "*"`` are split into arguments by
      :func:`shlex.split` (i.e. like arguments given on a command line via a
      shell). See the :mod:`shlex` documentation for full details.
    """

    source_name = "environment"

    def __init__(
        self, actions, none_values=None, priority=10, env_var_prefix="",
    ):
        super().__init__(actions, priority=priority)

        if none_values is None:
            none_values = [""]
        self._none_values = none_values
        self._env_var_prefix = env_var_prefix

    def parse_config(self):
        mentions = []
        for action in self.actions.values():
            env_name = self._config_name_to_env_name(action.name)
            if env_name not in os.environ:
                continue
            value = os.environ[env_name]
            if value in self._none_values and action.nargs in (0, "?", "*"):
                args = []
            elif action.nargs == 0:
                raise InvalidValueForNargs0Error(value, self._none_values)
            elif action.nargs is None or action.nargs in (1, "?"):
                args = [value]
            else:
                args = shlex.split(value)
            mentions.append(ConfigMention(action, args, self.priority))
        return mentions

    def _config_name_to_env_name(self, config_name):
        return f"{self._env_var_prefix}{config_name.upper()}"


class ArgparseSource(Source):
    """
    Obtains config values from an :class:`argparse.ArgumentParser`.

    Do not create :class:`ArgparseSource` objects directly, add them to a
    :class:`ConfigParser` object using :meth:`ConfigParser.add_source`. For
    example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")
        argparse_source = parser.add_source("argparse")

        argparse_parser = argparse.ArgumentParser()
        argparse_parser.add_argument("arg1")
        argparse_parser.add_argument("--opt1", type=int, action="append")
        argparse_source.add_configs_to_argparse_parser(argparse_parser)

        args = argparse_parser.parse_args((
            "arg1_value --config-item1 v1 --config-item2 1 2 --opt1 opt1_v1 "
            "--config-item-3 --opt1 opt1_v2"
        ).split())

        argparse_source.notify_parsed_args(args)

        config_parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The ``argparse`` source does not create an :class:`argparse.ArgumentParser`
    for you. This is to allow extra command line arguments to be added to an
    :class:`argparse.ArgumentParser` that are not config items. Instead
    :class:`ArgparseSource`, which implements the ``argparse`` source provides
    two methods to provide communication with the
    :class:`argparse.ArgumentParser`:

    .. automethod:: ArgparseSource.add_configs_to_argparse_parser
        :noindex:

    .. automethod:: ArgparseSource.notify_parsed_args
        :noindex:

    If you don't need to add command line arguments other than for config
    items, see :class:`SimpleArgparseSource` which implements the
    ``simple_argparse`` source.

    The arguments of :meth:`ConfigParser.add_source` for the ``argparse``
    source are:

    * ``source`` (required, positional): ``"argparse"``

    * ``priority`` (optional, keyword): The priority for the source. The
      default priority for an ``argparse`` source is ``20``.

    Note that:

    * The name of the command line argument for a config item is the config
      item's name with underscores (``_``) converted to hyphens (``-``) and
      prefixed with ``--``.
    """

    source_name = "argparse"

    class MulticonfparseAction(argparse.Action):
        def __init__(self, option_strings, dest, action_obj, priority):
            self._action = action_obj
            self._priority = priority
            help = action_obj.help
            if help is SUPPRESS:
                help = argparse.SUPPRESS
            super().__init__(
                option_strings,
                help=help,
                default=[],
                dest=dest,
                nargs=action_obj.nargs,
            )

        def __call__(self, parser, namespace, values, option_string):
            nargs = self._action.nargs
            if values is None:
                assert nargs == "?"
                args = []
            elif not isinstance(values, list):
                assert nargs is None or nargs == "?"
                args = [values]
            else:
                assert nargs is not None and nargs != "?"
                if isinstance(nargs, int):
                    assert len(values) == nargs
                if nargs == "+":
                    assert values
                args = values
            current = getattr(namespace, self.dest)
            current.append(ConfigMention(self._action, args, self._priority))

    def __init__(self, actions, priority=20):
        super().__init__(actions, priority=priority)
        self._parsed_values = []

    def parse_config(self):
        return self._parsed_values

    def add_configs_to_argparse_parser(self, argparse_parser):
        """
        Add arguments to an :class:`argparse.ArgumentParser` for config items.
        """
        for action in self.actions.values():
            arg_name = self._config_name_to_arg_name(action.name)
            argparse_parser.add_argument(
                arg_name,
                action=self.MulticonfparseAction,
                dest="multiconfparse_values",
                action_obj=action,
                priority=self.priority,
            )

    def notify_parsed_args(self, argparse_namespace):
        """
        Notify the ``argparse`` source of the :class:`argparse.Namespace`
        object returned by :meth:`argparse.ArgumentParser.parse_args`.
        """
        self._parsed_values = argparse_namespace.multiconfparse_values

    @staticmethod
    def _config_name_to_arg_name(config_name):
        return f"--{config_name.replace('_', '-')}"


class SimpleArgparseSource(Source):
    """
    Obtains config values from the command line.

    The ``simple_argparse`` source is simpler to use than the ``argparse``
    source but it doesn't allow adding arguments that are not config items.

    Do not create objects of this class directly - create them via
    :meth:`ConfigParser.add_source` instead. For example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")
        parser.add_source("simple_argparse")
        config_parser.parse_config()
        # If the command line looks something like:
        #    PROG_NAME --config-item1 v1 --config-item2 1 2 --config-item-3
        # The result would be:
        # multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for the
    ``simple_argparse`` source are:

    * ``source`` (required, positional): ``"simple_argparse"``

    * ``argument_parser_class`` (optional, keyword): a class derived from
      :class:`argparse.ArgumentParser` to use instead of
      :class:`ArgumentParser` itself. This can be useful if you want to
      override :meth:`argparse.ArgumentParser.exit` or
      :meth:`argparse.ArgumentParser.error`.

    * ``priority`` (optional, keyword): The priority for the source. The
      default priority for a ``simple_argparse`` source is ``20``.

    * Extra keyword arguments to pass to :class:`argparse.ArgumentParser`.
      E.g.  ``prog``, ``allow_help``. Don't use the ``argument_default`` option
      though - the ``simple_argparse`` sources sets this internally. See the
      ``config_default`` option for :class:`ConfigParser` instead.

    Note that:

    * The name of the command line argument for a config item is the config
      item's name with underscores (``_``) converted to hyphens (``-``) and
      prefixed with ``--``.
    """

    source_name = "simple_argparse"

    def __init__(
        self,
        actions,
        argument_parser_class=argparse.ArgumentParser,
        priority=20,
        **kwargs,
    ):
        super().__init__(actions, priority=priority)
        self._argparse_source = ArgparseSource(actions, priority=priority)
        self._argparse_parser = argument_parser_class(**kwargs)
        self._argparse_source.add_configs_to_argparse_parser(
            self._argparse_parser
        )

    def parse_config(self):
        self._argparse_source.notify_parsed_args(
            self._argparse_parser.parse_args()
        )
        return self._argparse_source.parse_config()


class JsonSource(Source):
    """
    Obtains config values from a JSON file.

    Do not create objects of this class directly - create them via
    :meth:`ConfigParser.add_source`. For example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_config("config_item2", nargs=2, type=int)
        parser.add_config("config_item3", action="store_true")

        fileobj = io.StringIO('''
            {
                "config_item1": "v1",
                "config_item2": [1, 2],
                "config_item3": null
            }
        ''')
        parser.add_source("json", fileobj=fileobj)

        config_parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        #   "config_item2": [1, 2],
        #   "config_item3": True,
        # }

    The arguments of :meth:`ConfigParser.add_source` for ``json`` sources are:

    * ``source`` (required, positional): ``"json"``.

    * ``priority`` (optional, keyword): The priority for the source. The
      default priority for a ``json`` source is ``0``.

    * ``path`` (optional, keyword): path to the JSON file to parse. Exactly one
      of the ``path`` and ``fileobj`` options must be given.

    * ``fileobj`` (optional keyword): a file object representing a stream of
      JSON data. Exactly one of the ``path`` and ``fileobj`` options must be
      given.

    * ``none_values`` (optional, keyword): a list of python values that, when
      seen as config item values after JSON decoding, should be treated as if
      they were not present (i.e.  values for config items with ``nargs == 0``
      or ``nargs == "?"`` (where the ``const`` value should be used rather than
      the value from the dict). The default ``none_values`` is ``[]``.

    * ``json_none_values`` (optional, keyword): a list of JSON values (as
      strings) that are decoded into Python values and added to
      ``none_values``.  The default ``json_none_values`` is ``["null"]``.

    Notes:

    * The data in the JSON file should be a JSON object. Each config item value
      should be assigned to a field of the object that has the same name as the
      config item.

    * Fields in the JSON object for config items with ``nargs == 0`` or
      ``nargs == "?"`` (where the ``const`` value should be used rather than
      the value from the dict) should either have values from the
      ``json_none_values`` list or should decode to values in the
      ``none_values`` list.

    * Fields in the JSON object for config items with ``nargs >= 2``,
      ``nargs == "+"`` or ``nargs == "*"`` should be JSON arrays with an
      element for each argument of the config item.

      In the special case where ``nargs == "+"`` or ``nargs == "*"`` and
      there is a single argument for the config item, the value may be given
      without the enclosing JSON array, unless the argument is itself an array.
    """

    source_name = "json"

    def __init__(
        self,
        actions,
        path=None,
        fileobj=None,
        none_values=None,
        json_none_values=None,
        priority=0,
    ):
        super().__init__(actions, priority=priority)
        if path and fileobj:
            raise ValueError(
                "JsonSource's 'path' and 'fileobj' options were both "
                "specified but only one is expected"
            )

        if none_values is None:
            none_values = []
        if json_none_values is None:
            json_none_values = ["null"]

        values_dict = self._get_json(path, fileobj)
        self._dict_source = DictSource(
            actions,
            values_dict,
            none_values=[json.loads(v) for v in json_none_values]
            + none_values,
        )

    def parse_config(self):
        return self._dict_source.parse_config()

    @staticmethod
    def _get_json(path, fileobj):
        if path:
            with open(path, mode="r") as f:
                return json.load(f)
        else:
            return json.load(fileobj)


class Action(abc.ABC):
    """
    Abstract base class config actions.

    Classes to support actions should:

    * Inherit from :class:`Action`.

    * Implement the :meth:`__call__` method documented below.

    * Have an ``action_name`` class attribute set to the name of the action
      that the class implements.

    * Have an ``__init__()`` method that accepts arguments passed to
      :meth:`ConfigParser.add_config` calls by the user (except the ``action``
      argument) and which calls :meth:`Action.__init__` with any of those
      arguments which are not specific to the action handled by the class.
      I.e.:

        * ``name``;

        * ``nargs``;

        * ``type``;

        * ``required``;

        * ``default``;

        * ``choices``;

        * ``help``;

        * ``dest``;

        * ``include_sources``;

        * ``exclude_sources``.

      These arguments will be assigned to attributes of the :class:`Action`
      object being created (perhaps after some processing or validation) that
      are available for access by subclasses. The names of the attributes are
      the same as the argument names.

      It is recommended that passing the arguments to
      :meth:`Action.__init__` is done by the subclass ``__init__`` method
      accepting a ``**kwargs`` argument to collect any arguments that are not
      used or modified by the action class, then passing that ``**kwargs``
      argument to :meth:`Action.__init__`. The action class may also want
      pass some arguments that aren't specified by the user if the value of
      those arguments is implied by the action. For example, the
      ``store_const`` action class has the following ``__init__`` method:

      .. code-block:: python

          def __init__(self, const, **kwargs):
              super().__init__(
                  nargs=0,
                  type=None,
                  required=False,
                  choices=None,
                  **kwargs,
              )
              self.const = const

      This ensures that an exception is raised if the user specifies ``nargs``,
      ``type``, ``required``, or ``choices`` arguments when adding a
      ``store_const`` action because if the user specifies those arguments they
      will be given twice in the call to :meth:`Action.__init__`.

    .. automethod:: __call__
      :noindex:

    The full example of the class for the ``store_const`` action is:

    .. code-block:: python

        class StoreConstAction(Action):
            action_name = "store_const"

            def __init__(self, const, **kwargs):
                super().__init__(
                    nargs=0,
                    type=None,
                    required=False,
                    choices=None,
                    **kwargs,
                )
                self.const = const

                def __call__(self, namespace, args):
                    assert not args
                    setattr(namespace, self.dest, self.const)
    """

    # Dict of subclasses that handle specific actions. The action_name of the
    # action is the dict item's key and the subclass is the dict item's value.
    _subclasses = {}

    def __init_subclass__(cls, **kwargs):
        # Automatically register subclasses specialized to handle a particular
        # action. For a subclass to be registered it must have the action_name
        # of the action it handles in an 'action_name' class attribute.
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "action_name"):
            cls._subclasses[cls.action_name] = cls

    @classmethod
    def create(cls, action="store", **kwargs):
        # Factory to obtain Action objects with the correct subclass to
        # handle the given action.

        # Users can specify the class for the action directly rather than
        # giving its action_name. Assume that's what's happening if action
        # isn't a str
        if not isinstance(action, str):
            return action(**kwargs)

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
        dest=None,
        nargs=None,
        type=str,
        required=False,
        default=NOT_GIVEN,
        choices=None,
        help=None,
        include_sources=None,
        exclude_sources=None,
    ):
        self._set_name(name)
        self._set_dest(dest, self.name)
        self._set_nargs(nargs)
        self._set_type(type, nargs)
        self.required = required
        self.default = default
        self.choices = choices
        self.help = help
        self.include_sources = include_sources
        self.exclude_sources = exclude_sources
        if include_sources is not None and exclude_sources is not None:
            raise ValueError(
                "cannot set both include_sources and exclude_sources"
            )

    def accumulate_mention(self, namespace, mention):
        self._check_nargs_for_mention(mention)
        self._coerce_types_for_mention(mention)
        self._validate_choices_for_mention(mention)
        self.__call__(namespace, mention.args)

    @abc.abstractmethod
    def __call__(self, namespace, args):
        """
        Combine the arguments from a mention of this config with any existing
        value.

        This method is called once for each mention of the config item in the
        sources in order to combine the arguments from the mention with any
        existing value.

        ``namespace`` will be the same :class:`Namespace` object for all calls
        to this function during a :meth:`ConfigParser.parse_config` call, and
        it is used to hold the so-far-accumulated value for the config item
        mentions.

        This method's purpose is to combine the current value for this config
        item in ``namespace`` with the values in the ``new`` argument, and
        write the combined value into back into ``namespace``.

        The first time this method is called, if the config item has a default
        value, ``namespace`` will have an attribute for the config item and it
        will contain the default value; otherwise ``namespace`` will not have
        an attribute for the config item.

        After the first call to this method, ``namespace`` should have an
        attribute value for the config item set by the previous call.

        Notes:

        * The name of the attribute in ``namespace`` for this config item is
          given by this object's ``dest`` attribute.

        * The calls to this method are made in order of the priorities of the
          config item mentions in the sources, lowest priority first.

        * The values in ``new`` have already been coerced to the config item's
          ``type``.

        * The values in ``new`` have already been checked for ``choices``
          validity.

        * The number of values in ``new`` has already been checked for
          ``nargs`` validity.

        * If no arguments for the config item were given, ``new`` will just be
          an empty :class:`list`.

        For example, the :meth:`__call__` method for the ``append`` action is:

        .. code-block:: python

            def __call__(self, namespace, args):
                current = getattr(namespace, self.dest, [])
                if self.nargs == "?" and not args:
                    current.append(self.const)
                elif self.nargs is None or self.nargs == "?":
                    assert len(args) == 1
                    current.extend(args)
                else:
                    current.append(args)
                setattr(namespace, self.dest, current)
        """

    @staticmethod
    def _is_python_identifier(name):
        return not re.search(r"[^0-9A-Za-z_]", name) and not re.search(
            r"^[^a-zA-Z_]", name
        )

    def _set_name(self, name):
        if not self._is_python_identifier(name):
            raise ValueError(
                f"invalid config name '{name}', must be a valid Python"
                " identifier"
            )
        self.name = name

    def _set_dest(self, dest, name):
        if dest is None:
            dest = name
        if not self._is_python_identifier(dest):
            raise ValueError(
                f"invalid dest '{dest}', must be a valid Python identifier"
            )
        self.dest = dest

    def _set_nargs(self, nargs):
        if nargs is None or nargs in ("*", "+", "?") or isinstance(nargs, int):
            self.nargs = nargs
        else:
            raise ValueError(f"invalid nargs value {nargs}")

    def _set_type(self, type, nargs):
        if nargs != 0 and not callable(type):
            raise TypeError("'type' argument must be callable")
        self.type = type

    def _check_nargs_for_mention(self, mention):
        if (
            (isinstance(self.nargs, int) and len(mention.args) != self.nargs)
            or (self.nargs is None and len(mention.args) != 1)
            or (self.nargs == "+" and not mention.args)
        ):
            raise InvalidNumberOfValuesError(self)

    def _coerce_types_for_mention(self, mention):
        mention.args = [self.type(a) for a in mention.args]

    def _validate_choices_for_mention(self, mention):
        if self.choices is None:
            return
        for arg in mention.args:
            if arg not in self.choices:
                raise InvalidChoiceError(self, arg)


class StoreAction(Action):
    """
    The ``store`` action simply stores the value from the highest priority
    mention of a config item. Its behaviour is based on the ``store``
    :mod:`argparse` action and is the default action.

    Arguments to :meth:`ConfigParser.add_config` have standard behaviour, but
    note:

    * ``nargs == 0`` is not allowed. The default ``nargs`` value is
      :data:`None`.

    * The ``const`` argument is only accepted when ``nargs == "?"``.

    Examples:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1")
        parser.add_source("dict", {"config_item1": "v1"}, priority=2)
        parser.add_source("dict", {"config_item1": "v2"}, priority=1)
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "v1",
        # }

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", nargs=2, type=int, default=[1, 2])
        parser.add_source("simple_argparse")
        parser.parse_config()
        #
        # If the command line looks something like:
        #   prog some-arg --config-item1 3 4
        # parse_config() will return something like:
        #   multiconfparse.Namespace {
        #       "config_item1": [3, 4],
        #   }
        #
        # If the command line looks something like:
        #   prog some-arg
        # parse_config() will return something like:
        #   multiconfparse.Namespace {
        #       "config_item1": [1, 2],
        #   }
    """

    action_name = "store"

    def __init__(self, const=None, **kwargs):
        super().__init__(**kwargs)
        self._set_const(const)

    def __call__(self, namespace, args):
        if self.nargs == "?" and not args:
            setattr(namespace, self.dest, self.const)
        elif self.nargs is None or self.nargs == "?":
            assert len(args) == 1
            setattr(namespace, self.dest, args[0])
        else:
            setattr(namespace, self.dest, args)

    def _set_nargs(self, nargs):
        super()._set_nargs(nargs)
        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action_name} action"
            )

    def _set_const(self, const):
        if const is not None and self.nargs != "?":
            raise ValueError(
                f"const cannot be supplied to the {self.action_name} action "
                'unless nargs is "?"'
            )
        self.const = const


class StoreConstAction(Action):
    """
    The ``store_const`` action stores the value from the ``const`` argument
    whenever a config item is mentioned in a source. Its behaviour is based on
    the ``store_const`` :mod:`argparse` action.

    Notes about the arguments to :meth:`ConfigParser.add_config`:

    * The ``const`` argument is mandatory.

    * ``nargs`` is not accepted as an argument. ``nargs`` is always ``0``
      for ``store_const`` actions.

    * ``required`` is not accepted as an argument. ``required`` is always
      :data:`False` for ``store_const`` actions.

    * ``type`` is not accepted as an argument - it doesn't make sense for
      ``store_const``.

    * ``choices`` is not accepted as an argument - it doesn't make sense for
      ``store_const``.

    Example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config(
            "config_item1",
            action="store_const",
            const="yes",
            default="no"
        )
        parser.add_source("dict", {"config_item1": None})
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": "yes",
        # }
    """

    action_name = "store_const"

    def __init__(self, const, **kwargs):
        super().__init__(
            nargs=0, type=None, required=False, choices=None, **kwargs,
        )
        self.const = const

    def __call__(self, namespace, args):
        assert not args
        setattr(namespace, self.dest, self.const)


class StoreTrueAction(StoreConstAction):
    """
    The ``store_true`` action simply stores the value :data:`True` whenever a
    config item is mentioned in a source. Its behaviour is based on the
    ``store_true`` :mod:`argparse` action.

    Notes about the arguments to :meth:`ConfigParser.add_config`:

    * ``const`` is not accepted as an argument - ``const`` is always
      :data:`True` for ``store_true`` actions.

    * ``nargs`` is not accepted as an argument. ``nargs`` is always ``0``
      for ``store_true`` actions.

    * ``required`` is not accepted as an argument. ``required`` is always
      :data:`False` for ``store_true`` actions.

    * ``type`` is not accepted as an argument - it doesn't make sense for
      ``store_true``.

    * ``choices`` is not accepted as an argument - it doesn't make sense for
      ``store_true``.

    * The default value for the ``default`` argument is :data:`False`.

    Examples:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="store_true")
        parser.add_source("dict", {"config_item1": None})
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": True,
        # }

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="store_true")
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": False,
        # }
    """

    action_name = "store_true"

    def __init__(self, default=False, **kwargs):
        super().__init__(const=True, default=default, **kwargs)


class StoreFalseAction(StoreConstAction):
    """
    The ``store_false`` action simply stores the value :data:`False` whenever a
    config item is mentioned in a source. Its behaviour is based on the
    ``store_false`` :mod:`argparse` action.

    Notes about the arguments to :meth:`ConfigParser.add_config`:

    * ``const`` is not accepted as an argument - ``const`` is always
      :data:`False` for ``store_false`` actions.

    * ``nargs`` is not accepted as an argument. ``nargs`` is always ``0``
      for ``store_false`` actions.

    * ``required`` is not accepted as an argument. ``required`` is always
      :data:`False` for ``store_false`` actions.

    * ``type`` is not accepted as an argument - it doesn't make sense for
      ``store_false``.

    * ``choices`` is not accepted as an argument - it doesn't make sense for
      ``store_false``.

    * The default value for the ``default`` argument is :data:`True`.

    Examples:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="store_false")
        parser.add_source("dict", {"config_item1": None})
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": False,
        # }

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="store_false")
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": True,
        # }
    """

    action_name = "store_false"

    def __init__(self, default=True, **kwargs):
        super().__init__(const=False, default=default, **kwargs)


class AppendAction(Action):
    """
    The ``append`` action stores the value for each mention of a config item
    in a :class:`list`. The :class:`list` is sorted according to the priorities
    of the mentions of the config item, lower priorities first. The Behaviour
    is based on the ``append`` :mod:`argparse` action.

    Notes about the arguments to :meth:`ConfigParser.add_config`:

    * ``nargs == 0`` is not allowed. The default ``nargs`` value is
      :data:`None`.

      When ``nargs >= 1``, ``nargs == "+"`` or ``nargs == "*"``, each value in
      the :class:`list` for the config item is itself a :class:`list`
      containing the arguments for a mention of the config item.

    * The ``const`` argument is only accepted when ``nargs == "?"``.

    * The ``default`` argument (if it is given and is not :const:`SUPPRESS`) is
      used as the initial :class:`list` of values. This means that the
      ``default`` value is incorporated into the final value for the config
      item, even if the config item is mentioned in a source.

    Examples:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="append", default=["v1"])
        parser.add_source("dict", {"config_item1": "v2"}, priority=2)
        parser.add_source("dict", {"config_item1": "v3"}, priority=1)
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": ["v1", "v3", "v2"],
        # }

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config(
            "config_item1",
            action="append",
            nargs="?",
            const="v0",
        )
        parser.parse_config()
        parser.add_source("dict", {"config_item1": "v1"}, priority=2)
        parser.add_source("dict", {"config_item1": None}, priority=1)
        # -> multiconfparse.Namespace {
        #   "config_item1": ["v0", "v1"],
        # }
    """

    action_name = "append"

    def __init__(self, const=None, default=NOT_GIVEN, **kwargs):
        # Copy the default value. It will be put into the Namespace returned by
        # ConfigParser.parse_config() and modified, and those modifications
        # shouldn't affect the user's copy.
        if default is not NOT_GIVEN and default is not SUPPRESS:
            default = list(default)
        super().__init__(default=default, **kwargs)
        self._set_const(const)

    def __call__(self, namespace, args):
        current = getattr(namespace, self.dest, [])
        if self.nargs == "?" and not args:
            current.append(self.const)
        elif self.nargs is None or self.nargs == "?":
            assert len(args) == 1
            current.extend(args)
        else:
            current.append(args)
        setattr(namespace, self.dest, current)

    def _set_nargs(self, nargs):
        super()._set_nargs(nargs)
        if self.nargs == 0:
            raise ValueError(
                f"nargs == 0 is not valid for the {self.action_name} action"
            )

    def _set_const(self, const):
        if const is not None and self.nargs != "?":
            raise ValueError(
                f"const cannot be supplied to the {self.action_name} action "
                'unless nargs is "?"'
            )
        self.const = const


class CountAction(Action):
    """
    The ``count`` action stores the number of times a config item is mentioned
    in the config sources. Its behaviour is based on the ``count``
    :mod:`argparse` action.

    Notes about the arguments to :meth:`ConfigParser.add_config`:

    * ``nargs`` is not accepted as an argument. ``nargs`` is always ``0``
      for ``count`` actions.

    * ``const`` is not accepted as an argument - it doesn't make sense for
      ``count``.

    * ``required`` is not accepted as an argument. ``required`` is always
      :data:`False` for ``count`` actions.

    * ``type`` is not accepted as an argument - it doesn't make sense for
      ``count``.

    * ``choices`` is not accepted as an argument - it doesn't make sense for
      ``count``.

    * If the ``default`` argument is given and is not :const:`SUPPRESS`, it
      acts as the initial value for the count. I.e. the final value for the
      config item will be the number of mentions of the config item in the
      sources, plus the value of ``default``.

      Note that if the config item is not found in any sources and ``default``
      is not given, it is *not* assumed to be ``0``. The final value for the
      config item would be :data:`None` in this case.

    Examples:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="count")
        parser.add_source("dict", {"config_item1": None})
        parser.add_source("dict", {"config_item1": None})
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": 2,
        # }

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="count", default=10)
        parser.add_source("dict", {"config_item1": None})
        parser.add_source("dict", {"config_item1": None})
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": 12,
        # }

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config("config_item1", action="count")
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": None,
        # }

    """

    action_name = "count"

    def __init__(
        self, **kwargs,
    ):
        super().__init__(
            nargs=0, type=None, choices=None, required=False, **kwargs,
        )

    def __call__(self, namespace, args):
        assert not args
        current = 0
        if hasattr(namespace, self.dest):
            current = getattr(namespace, self.dest)
        setattr(namespace, self.dest, current + 1)


class ExtendAction(AppendAction):
    """
    The ``extend`` action stores the value for each argument of each mention of
    a config item in a :class:`list`. The :class:`list` is sorted according to
    the priorities of the mentions of the config item, lower priorities first.
    The Behaviour is based on the ``extend`` :mod:`argparse` action, although
    the behaviour when ``nargs == None`` or ``nargs == "?"`` is different.

    Notes about the arguments to :meth:`ConfigParser.add_config`:

    * ``nargs == 0`` is not allowed. The default ``nargs`` value is "+".

      Unlike the ``append`` action, when ``nargs >= 1``, ``nargs == "+"`` or
      ``nargs == "*"``, each value in the :class:`list` for the config item is
      *not* itself a :class:`list` containing the arguments for a mention of
      the config item.  Each argument of each mention is added separately to
      the :class:`list` that makes the final value for the config item.

      Unlike the :mod:`argparse` ``extend`` action, when ``nargs == None`` or
      ``nargs == "?"``, the :mod:`multiconfparse` ``extend`` action behaves
      exactly like the ``append`` action.

    * The ``const`` argument is only accepted when ``nargs == "?"``.

    * The ``default`` argument (if it is given and is not :const:`SUPPRESS`) is
      used as the initial :class:`list` of values. This means that the
      ``default`` value is incorporated into the final value for the config
      item, even if the config item is mentioned in a source.

    Example:

    .. code-block:: python

        parser = multiconfparse.ConfigParser()
        parser.add_config(
            "config_item1",
            action="extend",
            default=[["v1", "v2"]]
        )
        parser.add_source("dict", {"config_item1": ["v3", "v4"]}, priority=2)
        parser.add_source("dict", {"config_item1": ["v5"]}, priority=1)
        parser.parse_config()
        # -> multiconfparse.Namespace {
        #   "config_item1": ["v1", "v2", "v5", "v3", "v4"],
        # }
    """

    action_name = "extend"

    def __init__(self, **kwargs):
        if "nargs" not in kwargs:
            kwargs["nargs"] = "+"
        super().__init__(**kwargs)

    def __call__(self, namespace, args):
        if self.nargs is None or self.nargs == "?":
            return super().__call__(namespace, args)
        current = getattr(namespace, self.dest, [])
        current.extend(args)
        setattr(namespace, self.dest, current)


class ConfigParser:
    """
    Create a new ConfigParser object. Options are:

    * ``config_default``: the default value to use in the :class:`Namespace`
      returned by :meth:`parse_config` for config items for which no value was
      found.

      The default behaviour (when ``config_default`` is
      :const:`NOT_GIVEN`) is to represent these config items with the
      value ``None``.

      Set ``config_default`` to :const:`SUPPRESS` to prevent
      these configs from having an attribute set in the :class:`Namespace` at
      all.
    """

    class ValueWithPriority:
        def __init__(self, value, priority):
            self.value = value
            self.priority = priority

        def __str__(self):
            return f"ValueWithPriority({self.value}, {self.priority})"

        __repr__ = __str__

    def __init__(self, config_default=NOT_GIVEN):
        self._actions = {}
        self._sources = []
        self._parsed_values = {}
        self._global_default = config_default

    def add_config(self, name, **kwargs):
        """
        Add a config item to the :class:`ConfigParser`.

        The arguments that apply to all config items are:

        * ``name`` (required, positional): the name of the config item.

          In the :class:`Namespace` object returned by :meth:`parse_config`,
          the name of the attribute used for this config item will be ``name``
          and must be a valid Python identifier.

          ``name`` is also used by source `Source` classes to generate the
          strings that will be used to find the config in config sources. The
          `Source` classes may, use a modified version of ``name``, however.
          For example, the ``argparse`` and ``simple_argparse`` sources will
          convert underscores (``_``) to hyphens (``-``) and add a ``--``
          prefix, so if a config item had the name ``"config_item1"``, the
          ``argparse`` and ``simple_argparse`` sources would use the option
          string ``"--config-item1"``.

        * ``action`` (optional, keyword): the name of the action that should be
          performed when a config item is found in a config source. The default
          action is ``"store"``, and the built-in actions are described briefly
          below. See :ref:`Actions` for more detailed information about the
          built-in actions and creating your own actions. The built-in actions
          are all based on :mod:`argparse` actions so the :mod:`argparse
          documentation<argparse>` may also provide useful information.

          * ``store``: this action just stores the highest priority value for
            config item.

          * ``store_const``: this stores the value specified in the ``const``
            argument.

          * ``store_true``: this stores the value :data:`True` and sets the
            ``default`` argument to :data:`False`. It is a special case of
            ``store_const``.

          * ``store_false``: this stores the value :data:`False` and sets the
            ``default`` argument to :data:`True`. It is a special case of
            ``store_const``.

          * ``append``: this creates a :class:`list` containing every value
            seen (with lower priority values first). When ``nargs >= 1``,
            ``nargs == "+"`` or ``nargs == "*"``, each value in the list s
            iteself a list containing the arguments for a mention of the
            config item.

          * ``count``: this stores the number of mentions of the config item.

          * ``extend``: this creates a :class:`list` containing every value
            seen (with lower priority values first). Unlike ``append``, when
            ``nargs >= 1``, ``nargs == "+"`` or ``nargs == "*"``, arguments for
            mentions of the config item are not placed in separate sublists for
            each mention.

        * ``default``: the default value for this config item. Note that some
          actions will incorporate the ``default`` value into the final value
          for the config item even if the config item is mentioned in one of
          the sources (e.g. ``append``, ``count`` and ``extend``).

          Note that the default value for all config items can also be set by
          passing a value for the ``config_default`` argument of
          :class:`ConfigParser`. If both the ``config_default`` argument to
          :class:`ConfigParser` and the ``default`` argument to
          :meth:`add_config` are used then only the ``default`` argument to
          :meth:`add_config` is used.

          If a default value is not provided for the config item by the
          ``default`` argument, the ``config_default`` argument or by the
          action class (like e.g. `store_true` does), then the final value for
          the config will be :data:`None` if the config item is not mentioned
          in any source.

          The special value :const:`SUPPRESS` can be passed as the ``default``
          argument. In this case, if the config item is not mentioned in any
          source, it will not be given an attribute in the :class:`Namespace`
          object returned by :meth:`parse_config`.

        * ``dest`` (optional, keyword): the name of the attribute for the
          config item in the :class:`Namespace` object returned by
          :meth:`parse_config`. By default, ``dest`` is set to the name of the
          config item (``name``).

        * ``exclude_sources`` (optional, keyword): a collection of source names
          or :class:`Source` classes that should ignore this config item. This
          argument is mutually exclusive with ``include_sources``. If neither
          ``exclude_sources`` nor ``include_sources`` is given, the config item
          will be looked for by all sources added to the :class:`ConfigParser`.

        * ``include_sources`` (optional, keyword): a collection of source names
          or :class:`Source` classes that should look for this config item.
          This argument is mutually exclusive with ``exclude_sources``.  If
          neither ``exclude_sources`` nor ``include_sources`` is given, the
          config item will be looked for by all sources added to the
          :class:`ConfigParser`.

        * ``help``: the help text/description for the config item. Set this to
          :data:`SUPPRESS` to prevent this config item from being mentioned in
          generated documentation.

        The other arguments are all keyword arguments and are passed on to the
        class that implements the config items action and may have different
        default values or may not even be valid for all actions. See
        :ref:`Actions` for action specific documentation.

        * ``nargs``: specifies the number of arguments that the config item
          accepts. The values that ``nargs`` can take are:

          * :data:`None`: the config item will take a single argument.

          * ``0``: the config item will take no arguments. This value is
            usually not given to :meth:`add_config` but may be implicit for
            an action (e.g. ``store_const`` or ``count``).

          * An :class:`int` ``N >= 1``: the config item will take ``N``
            arguments and the value for a mention of the config item will be a
            :class:`list` containing each argument. In particular, when ``nargs
            == 1`` the value for each mention of a config item will be a
            :class:`list` containing a single element.

          * ``"?"``: The config item will take a single optional argument. When
            the config item is mentioned without an accompanying value, the
            value for the mention is the value of the config item's ``const``
            argument.

          * ``"*"``: The config item will take zero or more arguments and the
            value for a mention of the config item will be a :class:`list`
            containing each argument.

          * ``"+"`` The config item will take one or more arguments and the
            value for a mention of the config item will be a :class:`list`
            containing each argument.

        * ``const``: The value to use for a mention of the config item where
          there is no accompanying argument. This is only ever used when
          ``nargs == 0`` or ``nargs == "+"``.

        * ``type``: The type to which each argument of the config item should
          be converted. This can be any callable object that takes a single
          argument (an object with a ``__call__(self, arg)`` method), including
          classes like :class:`int` and functions that take a single argument.
          Note that some sources that read typed data may produce config item
          argument values that aren't always :class:`str` objects.

          The default ``type`` is :class:`str` unless that doesn't make sense
          (e.g. when ``nargs == 0``.

        * ``required``: specifies whether an exception should be raised if a
          value for this config item cannot be found in any source.

          The default ``required`` is :data:`False`.

        * ``choices``: specifies a collection of valid values for the arguments
          of the config item. If ``choices`` is specified, an exception is
          raised if the config item is mentioned in a source with an argument
          that is not in ``choices``.
        """
        if name in self._actions:
            raise ValueError(f"Config item with name '{name}' already exists")
        if "default" not in kwargs and self._global_default is not NOT_GIVEN:
            kwargs["default"] = self._global_default
        action = Action.create(name=name, **kwargs)
        self._actions[name] = action
        return action

    def add_source(self, source, *args, **kwargs):
        """
        Add a new config source to the :class:`ConfigParser`.

        The only argument required for all sources is the ``source`` parameter
        which may be the name of a source or a class that implements a source.
        Other arguments are passed on to the class that implements the source.

        The built-in sources are:

        * ``argparse``: for getting config values from the command line using
          an :class:`argparse.ArgumentParser`.

        * ``simple_argparse``: a simpler version of the ``argparse`` source
          that is easier to use but doesn't allow you to add any arguments that
          aren't also config items.

        * ``environment``: for getting config values from environment
          variables.

        * ``json``: for getting config values from JSON files.

        * ``dict``: for getting config values from Python dictionaries.

        See :ref:`Sources` for more information about the built-in sources and
        creating your own sources.

        Return the created config source object.
        """
        source_obj = Source.create(
            source, self._actions.copy(), *args, **kwargs,
        )
        self._sources.append(source_obj)
        return source

    def _accumulate_mentions(self, namespace, mentions):
        # Sort the values according to the priorities of the sources,
        # lowest priority first. When accumulating, sources should give the
        # so-far-accumulated value less priority than a new value.
        #
        # sorted() is guaranteed to use a stable sort so the order in which
        # values are given for any particular source is preserved.
        mentions = sorted(mentions, key=operator.attrgetter("priority"))
        for mention in mentions:
            mention.action.accumulate_mention(namespace, mention)

    def _parse_config(self, check_required):
        ns = Namespace()
        self._collect_defaults(ns)
        mentions = self._collect_mentions()
        self._accumulate_mentions(ns, mentions)
        if check_required:
            self._check_required_configs(ns)
        self._process_missing(ns)
        return ns

    def _collect_mentions(self):
        return [
            mention
            for source in self._sources
            for mention in source.parse_config()
            if not self._ignore_config_for_source(mention.action, source)
        ]

    def _collect_defaults(self, ns):
        for action in self._actions.values():
            if action.default is NOT_GIVEN or action.default is SUPPRESS:
                continue
            setattr(ns, action.dest, action.default)

    def partially_parse_config(self):
        """
        Parse the config sources, but don't raise a RequiredConfigNotFoundError
        exception if a required config is not found in any config source.

        Returns: a Namespace object containing the parsed
        values.
        """
        return self._parse_config(check_required=False)

    def parse_config(self):
        """
        Parse the config sources.

        Returns: a :class:`Namespace` object containing the parsed values.
        """
        return self._parse_config(check_required=True)

    def _process_missing(self, ns):
        for action in self._actions.values():
            if not hasattr(ns, action.dest) and action.default is not SUPPRESS:
                setattr(ns, action.dest, None)

    def _check_required_configs(self, namespace):
        for action in self._actions.values():
            if action.required and not hasattr(namespace, action.dest):
                raise RequiredConfigNotFoundError(
                    f"Did not find value for config item '{action.name}'"
                )

    @staticmethod
    def _ignore_config_for_source(config, source):
        if config.exclude_sources is not None:
            return (
                source.__class__ in config.exclude_sources
                or source.source_name in config.exclude_sources
            )
        if config.include_sources is not None:
            return (
                source.__class__ not in config.include_sources
                and source.source_name not in config.include_sources
            )
        return False


# ------------------------------------------------------------------------------
# Free functions
# ------------------------------------------------------------------------------


def _getattr_or_none(obj, attr):
    if hasattr(obj, attr):
        return getattr(obj, attr)
    return NOT_GIVEN


def _has_nonnone_attr(obj, attr):
    return _getattr_or_none(obj, attr) is not NOT_GIVEN


def _namespace_from_dict(d, actions=None):
    ns = Namespace()
    if actions is not None:
        for action in actions:
            if action.name in d:
                setattr(ns, action.dest, d[action.name])
    else:
        for k, v in d.items():
            setattr(ns, k, v)
    return ns


def _namespace(obj, actions=None):
    return _namespace_from_dict(vars(obj), actions)
