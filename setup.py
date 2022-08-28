from setuptools import setup
import os
from pathlib import Path


current_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(current_directory, 'README.rst')) as f:
    long_description = f.read()

setup(
    name='kcanal',
    version='0.0.1',
    author='Keyi Zhang',
    author_email='keyi@cs.stanford.edu',
    long_description=long_description,
    long_description_content_type='text/x-rst',
    packages=['kcanal'],
    url="https://github.com/Kuree/kcanal",
    install_requires=[
        "kratos",
    ],
    license_files=['LICENSE'],
    python_requires=">=3.6",
    extras_require={
        "test": ["pytest", "archipelago"],
    }
)
