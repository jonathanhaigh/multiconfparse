#!/usr/bin/env python3

# Copyright 2020 Jonathan Haigh <jonathanhaigh@gmail.com>
# SPDX-License-Identifier: MIT

import setuptools

with open("README.md") as f:
    long_desc = f.read()

setuptools.setup(
    name="multiconfparse",
    packages=setuptools.find_packages(),
    version="0.1.0",
    description="Parser for configuration from multiple sources",
    long_description=long_desc,
    long_description_content_type="text/markdown",
    url="https://github.com/jonathanhaigh/multiconfparse",
    author="Jonathan Haigh",
    author_email="jonathanhaigh@gmail.com",
    license="MIT",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="config configuration",
    python_requires="~=3.6",
)
