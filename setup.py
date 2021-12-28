# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='bitflyerapi',
    version='0.0.1',
    description='bitflyer api wrapper',
    long_description=readme,
    author='pedestrian618',
    url='https://github.com/pedestrian618/bitflyerapi',
    install_requires=['requests'],
    license=license,
    packages=find_packages(exclude=('tests', 'docs'))
)

