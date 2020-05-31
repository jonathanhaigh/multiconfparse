<!--
Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
SPDX-License-Identifier: MIT
-->
# `multiconfig` (name to be changed!)

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
   * have `source`s - objects that can obtain configuration values from
     different sources;
   * coordinate the parsing done by `source`s;
   * merge configuration values from `source`s into a single set of values.

1. Add specifications of your config items:
   ```python
   # Add config items
   config_parser.add_config("config_item1", required=True)
   config_parser.add_config("config_item2", default="default_value")
   ```

1. Add `source`s:
   ```python
    config_parser.add_source(mc.SimpleArgparseSource)
    config_parser.add_source(mc.JsonSource, "/path/to/config/file.json")
    ```

1. Parse config from all `source`s:
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
