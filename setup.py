from setuptools import setup
import omfitt

setup(
    name="omfitt",
    version=omfitt.__version__,
    url="https://github.com/valq7711/ombott",
    license=omfitt.__license__,
    author=omfitt.__author__,
    author_email="valq7711@gmail.com",
    maintainer=omfitt.__author__,
    maintainer_email="valq7711@gmail.com",
    description="Actions fitter for py4web",
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
    python_modules = ['omfitt']
)
