from setuptools import setup, find_packages
import sys, os

version = '0.0.2'

setup(name='swift_ring_sync',
      version=version,
      description="",
      long_description="""\
""",
      classifiers=[], # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
      keywords='',
      author='',
      author_email='',
      url='',
      license='',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      package_data = {'': ['stub.conf']},
      include_package_data=True,
      zip_safe=False,
      install_requires=[
          # -*- Extra requirements: -*-
      ],
      scripts=[
        'bin/swift-ring-sync', 'bin/swift-ring-uploader'
        ],
      entry_points="""
      # -*- Entry points: -*-
      """,
      )
