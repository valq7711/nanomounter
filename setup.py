from setuptools import setup
import orgapyzer

setup(
    name="orgapyzer",
    version=orgapyzer.__version__,
    url="https://github.com/valq7711/ombott",
    license=orgapyzer.__license__,
    author=orgapyzer.__author__,
    author_email="valq7711@gmail.com",
    maintainer=orgapyzer.__author__,
    maintainer_email="valq7711@gmail.com",
    description="Actions mounter",
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
    ],nn
    python_requires='>=3.7',
    python_modules = ['orgapyzer']
)
