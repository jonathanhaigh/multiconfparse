<!--
Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
SPDX-License-Identifier: MIT
-->
# `multiconfig`

`multiconfig` is a Python3 library for specifying and reading configuration
data from multiple sources.

## Installation

```shell
python -m pip install git+https://github.com/jonathanhaigh/multiconfig
```

## Quickstart

1. Import the `multiconfig` module:
   ```python
   import multiconfig.multiconfig as mc
   ```

1. Create a `ConfigParser` object:
   ```python
   import multiconfig.multiconfig as mc

   config_parser = mc.ConfigParser()
   ```
   `ConfigParser`s:
   * contain the specifications of your configuration items;
   * have "subparsers" that can obtain configuration values from different
     sources;
   * coordinate the parsing done by subparsers;
   * merge configuration values from subparsers into a single set of values.

1. Add specifications of your config items:
   ```python
   # Add config items
   config_parser.add_config("config_item1", required=True)
   config_parser.add_config("config_item2", default="default_value")
   ```

1. Add subparsers/config sources:
   ```python
    config_parser.add_subparser(mc.SimpleArgparseSubparser)
    config_parser.add_subparser(mc.JsonSubparser, "/path/to/config/file.json")
    ```
    Each subparser is responsible for obtaining config values from a particular
    source.

1. Parse all config sources:
    ```python
    config = config_parser.parse_config()
    ```
    `ConfigParser.parse_config()` returns a `multiconfig.Namespace` object
    which is essentially just a plain object with attributes for each config
    item.

1. Use the config
   ```python
   item1 = config.config_item1
   item2 = config.config_item2
   ```
