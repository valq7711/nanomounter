from setuptools import setup, find_packages
import nanomounter

setup(
    name="nanomounter",
    version=nanomounter.__version__,
    url="https://github.com/valq7711/ombott",
    license=nanomounter.__license__,
    author=nanomounter.__author__,
    author_email="valq7711@gmail.com",
    maintainer=nanomounter.__author__,
    maintainer_email="valq7711@gmail.com",
    description="Nano mounter",
    platforms="any",
    keywords='python webapplication',
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires='>=3.7',
    python_modules = ['nanomounter']
)
