#!/usr/bin/env python3

from setuptools import find_packages, setup

setup(
    name="kpsync",
    version="1.0",
    description="Partial sync of KeePassX databases through the command-line",
    maintainer="Julien Rabinow",
    maintainer_email="randprefix.github@fastmail.com",
    url="https://github.com/jrabinow/kpsync.git",
    license="GPL3+",
    install_requires=[
        "pykeepass-cache",
        "pykeepass",
        "strictyaml",
        "xdg",
    ],
    packages=find_packages(exclude=["tests*"]),
    entry_points={
        "console_scripts": [
            "kpsync = src.kpsync:main",
        ],
    },
    classifiers=[
        (
            "License :: OSI Approved :: "
            "GNU General Public License v3 or later (GPLv3+)"
        ),
        "Environment :: Console",
        "Programming Language :: Python",
    ],
)
