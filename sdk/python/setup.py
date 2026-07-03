from setuptools import setup, find_packages

setup(
    name="scrapex",
    version="0.1.0",
    description="Python SDK for ScrapeX — AI-powered web & social media scraping",
    author="KanyouAI",
    packages=find_packages(),
    install_requires=["httpx>=0.28.0"],
    python_requires=">=3.10",
)
