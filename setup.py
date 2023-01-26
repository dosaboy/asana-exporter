from setuptools import setup, find_packages


def get_dependencies():
    """Reads the dependencies from the requirements file."""
    with open('requirements.txt', 'r') as d:
        dependencies = d.read()

    return dependencies


setup(
    name='asana_exporter',
    version='1.0.0',
    packages=find_packages(include=['asana_exporter*']),
    install_requires=get_dependencies(),
    entry_points={
      'console_scripts': [
        'asana-exporter=asana_exporter.client:main']
    }
)
